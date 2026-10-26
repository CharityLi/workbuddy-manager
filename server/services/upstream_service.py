"""给「多上游分组」起一个**独立的上游实例**，并交给 launchd 托管（开机自启）。

背景
----
分组隔离的物理形态是「一个分组 = 一套上游接入点」：各自的地址、各自的 api_key、
各自的账号目录。默认分组用部署时那套（7863 + auths/）；每新增一个分组，就得有
第二个、第三个 `wb2api` 进程，各带一份 `config.json` 与 `data/`。

手工做这件事的代价在 `instance-liushui/` 里能看出来：复制 config.json、改 listen
端口、链 `wb2api` 与 `auths`、建 data/、再手写一份 launchd plist。任何一步漏了都
不会报错，只是「这个分组永远没有账号、也没有日志」——和「上游不干活」长得一样。
所以这里把它固化成一条可重复执行的流程。

为什么落到 launchd 而不是后台 nohup
-----------------------------------
开机自启要的是「机器重启后它自己回来」。本机实测 `launchctl kickstart -k` 不生效、
`bootout` 也不杀进程，唯一可靠的加载方式是 `launchctl bootstrap gui/<uid> <plist>`。
所以：**plist 是唯一真相**，进程由 launchd 拉起；本模块只负责把 plist 写对并加载。

幂等
----
重复调用只会补齐缺失的部分（目录、symlink、config、plist），不覆盖已有 `data/`，
也不会把正在跑的服务停掉重建——除非显式要求 restart。
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .. import config

#: launchd 的 label 前缀（与既有三个服务保持一致）
LABEL_PREFIX = 'com.wb2api.gateway.'

#: 上游实例二进制文件名
BINARY_NAME = 'wb2api'


class ServiceError(Exception):
    """无法起服务时抛出：消息直接给界面看（含下一步怎么办）。"""


def _launch_agents_dir() -> Path:
    return Path.home() / 'Library' / 'LaunchAgents'


def user_domain() -> str:
    return f'gui/{os.getuid()}'


def _run(cmd: list[str], *, timeout: int = 20) -> tuple[int, str]:
    """跑一条命令并取回输出（launchctl 的报错都在 stderr，合并返回）。"""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, f'未找到命令 {cmd[0]}'
    except subprocess.TimeoutExpired:
        return 124, f'{cmd[0]} 超时（{timeout}s）'
    out = (proc.stdout or '') + (proc.stderr or '')
    return proc.returncode, out.strip()


def port_of(base_url: str) -> int:
    """从上游地址里取端口（`http://127.0.0.1:7865` → 7865）。"""
    match = re.search(r':(\d{2,5})(?:/|$)', (base_url or '').strip())
    if not match:
        raise ServiceError('上游地址里没有端口，无法为该分组起独立实例')
    port = int(match.group(1))
    if not (1 <= port <= 65535):
        raise ServiceError(f'上游地址的端口不合法：{port}')
    return port


def slug_for(upstream: dict) -> str:
    """分组 → 实例目录/label 用的短名。

    优先从已有的 `usage_file` 反推（例如 `instance-liushui/data/usage.json` →
    `liushui`），这样**已存在的实例不会被改名**；否则用 `g<id>` 兜底：分组名可能是
    中文，直接拿来当目录名既有编码风险，也不适合出现在 launchd label 里。
    """
    usage_file = str(upstream.get('usage_file') or '')
    if usage_file:
        parent = Path(usage_file).parent
        if parent.name == 'data' and parent.parent.name.startswith('instance-'):
            return parent.parent.name[len('instance-'):]
    return f'g{upstream.get("id")}'


def instance_dir_for(upstream: dict) -> Path:
    usage_file = str(upstream.get('usage_file') or '')
    if usage_file:
        parent = Path(usage_file).parent
        if parent.name == 'data' and parent.parent.name.startswith('instance-'):
            return parent.parent
    return config.UPSTREAM_DIR / f'instance-{slug_for(upstream)}'


def label_for(upstream: dict) -> str:
    return f'{LABEL_PREFIX}{slug_for(upstream)}'


def plist_path_for(upstream: dict) -> Path:
    return _launch_agents_dir() / f'{label_for(upstream)}.plist'


def _read_default_config() -> dict:
    """读默认上游的 config.json 作为模板。

    读不到就报错而不是凭空造一份：模板里有 api_key、上游协议开关、超时等一整套
    部署参数，猜出来的实例能启动但行为会和默认分组不一致（更难排查）。
    """
    path = Path(config.UPSTREAM_CONFIG)
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError as exc:
        raise ServiceError(f'读不到上游模板配置：{path}') from exc
    except json.JSONDecodeError as exc:
        raise ServiceError(f'上游模板配置不是合法 JSON：{path}（{exc}）') from exc
    if not isinstance(data, dict):
        raise ServiceError(f'上游模板配置格式异常：{path}')
    return data


def build_config(upstream: dict, template: dict, *, port: int,
                 instance_dir: Path) -> dict:
    """生成本分组实例的 config.json（在模板基础上只改必要的几项）。"""
    data = json.loads(json.dumps(template))  # deep copy：模板会被多次复用
    data['listen'] = f'0.0.0.0:{port}'
    auth_dir = str(upstream.get('auth_dir') or '').strip()
    if not auth_dir:
        raise ServiceError('该分组没有账号目录：先在「编辑」里选一个目录再启服务')
    data['auth_dir'] = auth_dir
    # state/usage 都落在实例自己的 data/ 下（相对 WorkingDirectory）
    data['state_file'] = './data/state.json'
    api_key = str(upstream.get('api_key') or '').strip()
    if api_key and api_key != '******':
        # 分组自带的密钥优先：管理端把它当上游凭据用，两边必须一致
        data['api_key'] = api_key
    return data


def build_plist(upstream: dict, *, instance_dir: Path) -> dict:
    """launchd 任务描述：WorkingDirectory + 绝对路径的 config + 日志重定向。"""
    binary = instance_dir / BINARY_NAME
    label = label_for(upstream)
    return {
        'Label': label,
        'ProgramArguments': [str(binary), '-config', str(instance_dir / 'config.json')],
        'WorkingDirectory': str(instance_dir),
        'StandardOutPath': str(instance_dir / 'data' / 'gateway.out.log'),
        'StandardErrorPath': str(instance_dir / 'data' / 'gateway.err.log'),
        'RunAtLoad': True,
        'KeepAlive': True,
    }


def _link(target: Path, link: Path) -> str:
    """建符号链接（已存在且指向一致则跳过）。返回做了什么，便于回显。"""
    if link.is_symlink():
        if link.resolve() == target.resolve():
            return 'skip'
        link.unlink()
    elif link.exists():
        # 真目录/真文件不覆盖：那是别人的数据，删了就是事故
        return 'keep-existing'
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)
    return 'created'


def ensure_instance(upstream: dict) -> dict:
    """把该分组的实例目录准备到位（幂等），不碰 launchd。

    返回 `{instance_dir, port, created:[...], skipped:[...]}`。
    """
    if upstream.get('is_default'):
        raise ServiceError('默认分组用的是部署时那套服务，不需要（也不能）另起实例')
    port = port_of(str(upstream.get('base_url') or ''))
    instance_dir = instance_dir_for(upstream)
    steps: list[str] = []

    data_dir = instance_dir / 'data'
    if not data_dir.is_dir():
        data_dir.mkdir(parents=True, exist_ok=True)
        steps.append(f'建目录 {data_dir}')

    # 二进制：用符号链接指回仓库里那份（升级二进制时所有实例一起生效）
    binary = instance_dir / BINARY_NAME
    if not binary.exists() and not binary.is_symlink():
        source = Path(config.UPSTREAM_DIR) / BINARY_NAME
        if not source.exists():
            raise ServiceError(f'找不到上游二进制：{source}')
        status = _link(source, binary)
        steps.append(f'{status} 二进制链接 {binary}')

    # 账号目录：实例通过 auths/ 链接直接用分组自己的目录
    auth_dir = Path(str(upstream.get('auth_dir') or ''))
    if not auth_dir.is_dir():
        raise ServiceError(f'账号目录不存在：{auth_dir}（先在「编辑」里选对目录）')
    status = _link(auth_dir, instance_dir / 'auths')
    steps.append(f'{status} 账号目录链接 auths → {auth_dir}')

    # config.json：存在就不动（可能被人手工调过），除非端口/目录与分组不一致
    cfg_path = instance_dir / 'config.json'
    want = build_config(upstream, _read_default_config(), port=port,
                        instance_dir=instance_dir)
    if cfg_path.exists():
        try:
            current = json.loads(cfg_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            current = None
        if current == want:
            steps.append('skip config.json（已一致）')
        else:
            if current:
                backup = cfg_path.with_suffix('.json.bak')
                shutil.copy2(cfg_path, backup)
                steps.append(f'备份旧配置 → {backup.name}')
            cfg_path.write_text(json.dumps(want, ensure_ascii=False, indent=2) + '\n',
                                encoding='utf-8')
            steps.append('写入 config.json')
    else:
        cfg_path.write_text(json.dumps(want, ensure_ascii=False, indent=2) + '\n',
                            encoding='utf-8')
        steps.append('新建 config.json')

    return {'instance_dir': str(instance_dir), 'port': port, 'steps': steps}


def write_plist(upstream: dict) -> tuple[Path, bool]:
    """写 launchd plist。返回 (路径, 是否有变化)。"""
    instance_dir = instance_dir_for(upstream)
    payload = build_plist(upstream, instance_dir=instance_dir)
    path = plist_path_for(upstream)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = plistlib.dumps(payload, sort_keys=True)
    if path.exists():
        # 比**解析后的内容**而不是字节：本机既有 plist 是手写的（缩进/键序不同），
        # 按字节比会判成「变了」→ 触发一次无谓的 bootout+bootstrap，等于重启服务。
        try:
            if plistlib.loads(path.read_bytes()) == payload:
                return path, False
        except (plistlib.InvalidFileException, ValueError, OSError):
            pass  # 坏文件：当作需要重写
    path.write_bytes(raw)
    return path, True


def port_owner(port: int) -> int | None:
    """谁在监听这个端口（没有则 None）。"""
    code, out = _run(['lsof', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN', '-t'])
    if code != 0 or not out:
        return None
    first = out.split()[0]
    return int(first) if first.isdigit() else None


def _pid_command(pid: int) -> str:
    code, out = _run(['ps', '-o', 'command=', '-p', str(pid)])
    return out if code == 0 else ''


def port_conflict(upstream: dict, port: int) -> str | None:
    """端口被谁占着？返回一句可执行的说明，或 None（端口空闲）。

    这台机器上**有孤儿实例**的先例：手工 `nohup` 起的进程没进 launchd，`launchctl
    bootout` 也不会杀它。此时新任务 bind 失败 → launchd 每 10 秒拉起一次，日志里
    刷 `bind: address already in use`，而界面看着「任务已加载」。所以启动前必须先
    看清端口是不是自己的。
    """
    pid = port_owner(port)
    if pid is None:
        return None
    command = _pid_command(pid)
    instance_dir = instance_dir_for(upstream)
    if str(instance_dir) in command:
        # 本实例的孤儿进程：可以接管（kill -TERM 后由 launchd 拉起）
        return (f'端口 {port} 被本分组的旧进程占用（pid {pid}，非 launchd 托管）。'
                f'加 takeover 重新执行即可接管：kill -TERM {pid}')
    return (f'端口 {port} 已被其他进程占用（pid {pid}：{command[:120]}），'
            '换一个端口，或先停掉它')


def loaded(upstream: dict) -> bool:
    code, _ = _run(['launchctl', 'print', f'{user_domain()}/{label_for(upstream)}'])
    return code == 0


def pid_of(upstream: dict) -> int | None:
    """从 `launchctl print` 里取 pid（未运行时返回 None）。"""
    code, out = _run(['launchctl', 'print', f'{user_domain()}/{label_for(upstream)}'])
    if code != 0:
        return None
    match = re.search(r'\bpid = (\d+)', out)
    return int(match.group(1)) if match else None


def start(upstream: dict, *, takeover: bool = False) -> dict:
    """准备实例 → 写 plist → 交给 launchd。返回执行明细。

    `takeover=True`：端口被**本分组的孤儿进程**占着时，先 `kill -TERM` 它再加载。
    只对本实例目录的进程生效；别人的进程一律报错不动。
    """
    prep = ensure_instance(upstream)
    path, changed = write_plist(upstream)
    steps = list(prep['steps'])
    steps.append(f'{"更新" if changed else "skip"} plist {path.name}')

    domain = user_domain()
    label = label_for(upstream)

    # 端口先看清楚：被占着就 bootstrap 也起不来（会变成 10 秒一次的崩溃循环）
    conflict = None if pid_of(upstream) else port_conflict(upstream, prep['port'])
    if conflict:
        if not takeover:
            raise ServiceError(conflict)
        pid = port_owner(prep['port'])
        if pid is not None:
            code, out = _run(['kill', '-TERM', str(pid)])
            if code != 0:
                raise ServiceError(f'停不掉占用端口的进程 {pid}：{out or code}')
            steps.append(f'接管：已 kill -TERM {pid}')

    # **正在跑且配置没变 → 什么都不做**。点击这个按钮的常见场景是「我确认一下它起没起」，
    # 若无条件 bootout+bootstrap，就等于把正在服务的实例重启一次（正在进行的会话会断）。
    if not changed:
        pid = pid_of(upstream)
        if pid is not None:
            steps.append('已有任务在运行且配置未变，跳过重启')
            return {'ok': True, 'pid': pid, 'port': prep['port'], 'steps': steps,
                    'instance_dir': prep['instance_dir'], 'label': label}

    # 改过 plist 或本来没加载：先 bootout 再 bootstrap（launchd 不会自动重读）
    if loaded(upstream):
        code, out = _run(['launchctl', 'bootout', f'{domain}/{label}'])
        steps.append('bootout 旧任务' + ('' if code == 0 else f'（{out or code}）'))
    code, out = _run(['launchctl', 'bootstrap', domain, str(path)])
    if code != 0:
        raise ServiceError(f'launchctl bootstrap 失败：{out or code}')
    steps.append('bootstrap 完成')

    pid = pid_of(upstream)
    if pid is None:
        # bootstrap 成功但没起进程：多半是端口被别的实例占着
        raise ServiceError(
            'launchd 已加载但没有进程，通常是端口被占用：'
            f'`lsof -nP -iTCP:{prep["port"]} -sTCP:LISTEN` 看一下谁占了'
        )
    steps.append(f'pid={pid}')
    return {'ok': True, 'pid': pid, 'port': prep['port'], 'steps': steps,
            'instance_dir': prep['instance_dir'], 'label': label}


def stop(upstream: dict) -> dict:
    """从 launchd 摘掉（进程随之结束）。不删任何文件。"""
    label = label_for(upstream)
    path = plist_path_for(upstream)
    code, out = _run(['launchctl', 'bootout', f'{user_domain()}/{label}'])
    steps = [f'bootout {label}' + ('' if code == 0 else f'（{out or code}）')]
    return {'ok': True, 'steps': steps, 'plist': str(path)}


def status(upstream: dict) -> dict:
    """服务现状：label / plist 路径 / 是否已加载 / pid / 端口 / 实例目录。"""
    try:
        port = port_of(str(upstream.get('base_url') or ''))
    except ServiceError:
        port = None
    instance_dir = instance_dir_for(upstream)
    return {
        'label': label_for(upstream),
        'plist': str(plist_path_for(upstream)),
        'plist_exists': plist_path_for(upstream).exists(),
        'instance_dir': str(instance_dir),
        'instance_exists': (instance_dir / 'config.json').exists(),
        'port': port,
        'loaded': loaded(upstream),
        'pid': pid_of(upstream),
        'autostart': plist_path_for(upstream).exists(),
    }


def _main(argv: list[str]) -> int:  # pragma: no cover - 手工排查用
    print('用法：python -m server.services.upstream_service <分组名>', file=sys.stderr)
    return 2


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(_main(sys.argv[1:]))
