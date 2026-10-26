"""读取上游用量台账，回答「今天每个账号扣了多少积分」。

## 为什么读文件而不问接口

上游（wb2api）把每次成功请求的 `usage.credit` 按
`(时间片, realm, uid, model, rate)` 分桶累计，落盘在 `data/usage.json`
（`internal/usage`，原子替换 + 30s 防抖）。这是本机唯一带 **uid 维度** 的扣费
记录：

* 上游 HTTP 层只有 `/v1/*`、`/status`、`/healthz`；用量接口在 `/panel/api/usage`，
  而本机的 `cfg.Panel` 为空（面板未挂载，实测全 404）；
* `/status` 的 `accounts[].token_usage` 是**累计**值（无时间维度、且不含积分），
  `spent_since_pin` 只在「单账号运行」模式下有意义；
* 管理端自己的 `request_logs` 有逐请求 `credit`，但**没有 uid 列**——网关在
  上游内部选号，管理端无从得知这次请求用了哪个号（响应头也只有 `X-Service`）。

所以只能在磁盘这一层取数：`data/usage.json` 就在网关日志旁边。

## 口径

* 桶的 scope 是 `h:2006-01-02T15`（小时）或 `d:2006-01-02`（日，小时桶过
  90 天后折叠而来）。**两个都要算**——否则跨过折叠边界的日期会突然变少。
* `cr` = `usage.credit` 累计，`cn` = 样本数（区分「上游没给 credit」与「真的是 0」）。
  只累加 `cr`；`cn=0` 的桶视为没观测到，跳过（不写 0，避免把「不知道」说成「没消耗」）。
* 不做任何「余额差」推算：余额有 60s 缓存、还会被签到/领奖加上去，差出来的
  数字既滞后又会把收入算成负消耗。
* 台账是**上游进程的口径**（cn/global 两个 realm 各算各的桶）：多分组部署时
  每个分组有自己的 `data/usage.json`，本模块**全部合并**（默认分组来自
  `WB2API_USAGE_FILE`，其余分组来自 `upstreams.usage_file`）——只读一份的话，
  分组账号的「今日消耗」会一直显示成「—」。

## 解析结果缓存

`usage.json` 实测 80KB+、30s 防抖写入。按 `(路径, mtime, size, 日期)` 缓存解析
结果，命中率接近 100%，又不会读到半截文件（上游是写临时文件再 rename，读到的
要么是旧版要么是新版，不会撕裂）。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .. import config

logger = logging.getLogger(__name__)

# 解析缓存：{(path, mtime_ns, size, day): {'credits': {uid: float}, 'requests': {uid: int}}}
_cache: dict[tuple, dict] = {}


def _usage_path() -> Path:
    """默认分组的台账文件路径。显式配置优先，否则取网关日志同目录的 `usage.json`。"""
    configured = getattr(config, 'WB2API_USAGE_FILE', None)
    if configured:
        return Path(configured)
    return Path(config.WB2API_LOG_FILE).parent / 'usage.json'


def _group_usage_paths() -> list[Path]:
    """其余分组的台账文件（`upstreams.usage_file`），默认分组不在其中。

    多分组部署时每个上游实例都有自己的 `data/` 目录，各自写自己的
    `usage.json`——只看默认那一份的话，分组账号的「今日消耗」会全部显示成
    「—」（读不到）。这个函数是唯一读数据库的地方，单独拎出来便于测试打桩：
    台账解析本身不该依赖 `manager.db` 是否存在。
    """
    paths: list[Path] = []
    try:
        from .. import upstreamsvc  # 延迟导入：避免 config → 服务层 → 循环依赖

        for up in upstreamsvc.list_upstreams(include_default=False):
            if not up.get('enabled'):
                continue
            raw = str(up.get('usage_file') or '').strip()
            if raw:
                paths.append(Path(raw))
    except Exception as exc:  # noqa: BLE001 —— 读不到分组列表就当没有，不拦主流程
        logger.debug('读取分组台账路径失败：%s', exc)
    return paths


def _usage_paths() -> list[Path]:
    """所有要合并的台账文件：默认分组在前，其余按分组顺序，去重保序。"""
    out: list[Path] = []
    seen: set[str] = set()
    for path in [_usage_path(), *_group_usage_paths()]:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


def today_of(now: float | None = None) -> str:
    """本机时区的「今天」（`YYYY-MM-DD`），与上游分片键同一格式、同一时区。"""
    return time.strftime('%Y-%m-%d', time.localtime(now if now is not None else time.time()))


def _parse(path: Path, day: str) -> dict:
    """把台账里属于 `day` 的桶按 uid 汇总。读不到/格式坏 → 空结果。"""
    credits: dict[str, float] = {}
    requests: dict[str, int] = {}
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        logger.debug('用量台账不存在：%s', path)
        return {'credits': credits, 'requests': requests}
    except (OSError, json.JSONDecodeError) as exc:  # noqa: PERF203
        logger.warning('用量台账读取失败（%s）：%s', path, exc)
        return {'credits': credits, 'requests': requests}

    buckets = raw.get('buckets') if isinstance(raw, dict) else None
    if not isinstance(buckets, list):
        return {'credits': credits, 'requests': requests}

    day_key = f'd:{day}'
    hour_prefix = f'h:{day}T'
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        scope = bucket.get('s')
        if not isinstance(scope, str):
            continue
        if scope != day_key and not scope.startswith(hour_prefix):
            continue
        uid = bucket.get('u')
        if not isinstance(uid, str) or not uid:
            continue
        samples = bucket.get('cn') or 0
        try:
            samples = int(samples)
        except (TypeError, ValueError):
            samples = 0
        requests[uid] = requests.get(uid, 0) + int(bucket.get('q') or 0)
        if samples <= 0:
            # 该桶没有任何「上游回过 credit」的样本：不知道就一定不写 0
            continue
        try:
            credits[uid] = credits.get(uid, 0.0) + float(bucket.get('cr') or 0.0)
        except (TypeError, ValueError):
            continue
    return {'credits': credits, 'requests': requests}


def _load(path: Path, day: str) -> dict | None:
    """带缓存的单文件解析；文件不存在/不可读 → None（与「空台账」区分）。"""
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path), stat.st_mtime_ns, stat.st_size, day)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    cached = _parse(path, day)
    # 每个分组一份、每份每 30s 变一次：留旧键没有意义，超过上限就整体清掉
    if len(_cache) > 16:
        _cache.clear()
    _cache[key] = cached
    return cached


def today_usage(now: float | None = None) -> dict | None:
    """今日各账号的扣费与请求数（**所有分组合并**）。

    返回 `{'day': 'YYYY-MM-DD', 'credits': {uid: 积分}, 'requests': {uid: 次数},
    'total': 全账号合计, 'sources': 实际读到的台账份数}`；一份台账都读不到时返回
    `None`（界面据此显示「—」而不是「0」——「读不到」与「没消耗」必须能分开）。

    多分组部署时每个上游实例各写各的 `usage.json`（靠独立 `data/` 目录隔离），
    所以这里按 uid 逐份累加：同一个 uid 不会出现在两份台账里（账号目录本身也是
    分开的），累加只是把「不同分组」拼成一张表。
    """
    day = today_of(now)
    credits: dict[str, float] = {}
    requests: dict[str, int] = {}
    sources = 0
    for path in _usage_paths():
        parsed = _load(path, day)
        if parsed is None:
            continue
        sources += 1
        for uid, value in parsed['credits'].items():
            credits[uid] = credits.get(uid, 0.0) + value
        for uid, count in parsed['requests'].items():
            requests[uid] = requests.get(uid, 0) + count
    if sources == 0:
        return None
    return {
        'day': day,
        'credits': {uid: round(value, 2) for uid, value in credits.items()},
        'requests': requests,
        'total': round(sum(credits.values()), 2),
        'sources': sources,
    }


def today_credit_by_uid(now: float | None = None) -> dict[str, float] | None:
    """便捷入口：只取 `{uid: 今日消耗积分}`；台账不可用时返回 None。"""
    usage = today_usage(now)
    if usage is None:
        return None
    return usage['credits']
