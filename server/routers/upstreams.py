"""多上游（账号池分组）管理接口。

与 `/api/settings/upstream`（那是**上游 workbuddy2api 的 config.json**）不是一回事：
这里管的是**本端接了几个上游**，以及每个上游用哪个地址、哪把密钥。密钥绑定上游
后，它的请求只走那个上游的账号池——这就是「分组隔离」在本端的落地形态。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import security, upstreamsvc
from ..iputil import client_ip
from ..services import upstream_service, wb2api

router = APIRouter(prefix='/api/upstreams', tags=['upstreams'])


class UpstreamIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=500)
    # 上游的 api_key。允许为空：少数自建上游不开鉴权，或者密钥先留空、稍后再填。
    api_key: str = Field(default='', max_length=500)
    note: str = Field(default='', max_length=200)
    enabled: bool = True
    # 分组的本地账号目录（绝对路径，见 upstreamsvc.normalize_auth_dir）。
    # 空 = 该分组不由本面板管理账号（只用于密钥转发）。
    auth_dir: str = Field(default='', max_length=500)
    # 分组上游实例的容器名（可选）：面板「重启该分组」按它 docker restart。
    container: str = Field(default='', max_length=64)
    # 该分组的用量台账（上游实例 data/usage.json 的绝对路径）：账号页「今日消耗」靠它合并。
    usage_file: str = Field(default='', max_length=500)


class UpstreamPatch(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    note: str | None = None
    enabled: bool | None = None
    auth_dir: str | None = None
    container: str | None = None
    usage_file: str | None = None


def _serialize(items: list[dict]) -> list[dict]:
    """对外形状：附上引用计数，并把 `api_key` 换成**脱敏值**（评审补）。

    凭据不明文回前端是本项目的既有约定（`api_key` / `upstash.token` /
    `device_token` 在其它接口一律 masked + `has_*`）。上游的 `api_key` 与它们同级，
    而且这一组接口的**列表只要登录**（含只读账号）——明文回传等于把上游凭据发给
    每一个登录用户。默认上游那行同样要脱敏：它的 key 来自运行中的上游配置。

    编辑时前端不预填，**留空 = 不修改**（PATCH 语义本来就是这样，见 update_upstream）。
    """
    from ..services import upstream_service, wb2api
    counts = upstreamsvc.key_counts()
    out = []
    for item in items:
        row = dict(item)
        raw_key = str(row.pop('api_key', '') or '')
        row['has_key'] = bool(raw_key)
        row['api_key_masked'] = wb2api._mask(raw_key) if raw_key else ''
        uid = row.get('id')
        # 默认上游统计的是「没绑定上游的密钥」——它们确实都走默认上游，
        # 这个数字对管理员判断「还有多少把钥匙在用默认池」有用。
        row['bound_keys'] = counts.get(('default' if uid is None else int(uid)), 0)
        out.append(row)
    return out


@router.get('')
def list_upstreams(user: dict = Depends(security.current_user)) -> dict:
    return {'items': _serialize(upstreamsvc.list_upstreams())}


@router.get('/auth-dir-options')
def auth_dir_options(user: dict = Depends(security.current_user)) -> dict:
    """账号目录的候选清单，供「编辑/新建分组」直接选而不是手敲路径。

    为什么要有它：账号目录是**绝对路径**，手敲一个不存在的路径不会报错（分组照
    样建好），只是那个分组永远没有账号 —— 表现为「新分组里一个号都没有」，很难
    联想到是路径写错。给候选清单后，用户只需要认名字。

    候选来自两处：
      1. 已经配过的目录（含默认分组的 `WB_AUTH_DIR`）——复用别人的目录是常见需求；
      2. 默认分组目录的**同级** `auths*` 目录 —— 上游按「auths-<分组名>」建目录，
          新实例的目录往往已经在磁盘上了。
    每项带 `account_count`（目录里 `.json` 个数）与 `owner`（哪个分组正在用它），
    方便判断「这个目录是不是已经被别人占了」。
    """
    items = upstreamsvc.list_upstreams()
    found: dict[str, dict] = {}
    for up in items:
        path = str(up.get('auth_dir') or '').strip()
        if not path:
            continue
        found[path] = {
            'path': path,
            'name': Path(path).name,
            'is_default': bool(up.get('is_default')),
            'owner': '' if up.get('is_default') else str(up.get('name') or ''),
        }

    default_dir = next((str(u.get('auth_dir') or '') for u in items if u.get('is_default')), '')
    if default_dir:
        try:
            siblings = sorted(Path(default_dir).parent.glob('auths*'))
        except OSError:
            siblings = []
        for path in siblings:
            if not path.is_dir():
                continue
            found.setdefault(str(path), {
                'path': str(path),
                'name': path.name,
                'is_default': str(path) == default_dir,
                'owner': '',
            })

    out = []
    for meta in found.values():
        directory = Path(meta['path'])
        try:
            count = sum(1 for f in directory.glob('*.json') if f.is_file())
        except OSError:
            count = 0
        out.append({**meta, 'exists': directory.is_dir(), 'account_count': count})
    # 默认分组排最前，其后按目录名（稳定顺序，避免下拉项跳动）
    out.sort(key=lambda item: (not item['is_default'], item['name'].lower()))
    return {'items': out}


@router.post('')
def create_upstream(body: UpstreamIn, request: Request,
                    user: dict = Depends(security.require_session_admin)) -> dict:
    """新建上游。要求**会话管理员**：它带着上游的 api_key，属于配置级凭据，
    与「一键更新 / 改安全策略」同级（见 docs/api-tokens.md 的作用域约定）。"""
    try:
        created = upstreamsvc.create_upstream(
            name=body.name, base_url=body.base_url, api_key=body.api_key,
            note=body.note, enabled=body.enabled,
            auth_dir=body.auth_dir, container=body.container,
            usage_file=body.usage_file,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    security.audit(user, 'create_upstream', created['name'],
                   f"id={created['id']}；地址={created['base_url']}；来源 {client_ip(request)}")
    return _serialize([created])[0]


@router.patch('/{upstream_id}')
def update_upstream(upstream_id: int, body: UpstreamPatch, request: Request,
                    user: dict = Depends(security.require_session_admin)) -> dict:
    patch = body.model_dump(exclude_unset=True)
    try:
        updated = upstreamsvc.update_upstream(upstream_id, patch)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=404, detail='上游不存在')
    # 审计里不写 api_key（可能只是改了个名字），只记改了哪些字段
    security.audit(user, 'update_upstream', updated['name'],
                   f"id={updated['id']}；字段={','.join(sorted(patch)) or '无'}；"
                   f'来源 {client_ip(request)}')
    return _serialize([updated])[0]


@router.get('/{upstream_id}/service')
def service_status(upstream_id: int,
                   user: dict = Depends(security.current_user)) -> dict:
    """该分组的上游实例现状（是否已交给 launchd、pid、端口、plist 路径）。"""
    upstream = upstreamsvc.get_upstream(upstream_id)
    if upstream is None:
        raise HTTPException(status_code=404, detail='上游不存在')
    return upstream_service.status(upstream)


@router.post('/{upstream_id}/service')
def service_start(upstream_id: int, request: Request, takeover: bool = False,
                  user: dict = Depends(security.require_session_admin)) -> dict:
    """一键把该分组的上游实例拉起来，并登记为 launchd 任务（开机自启）。

    做四件事，全部幂等：准备 `instance-<slug>/`（config.json、wb2api 与 auths
    符号链接、data/）、写 `~/Library/LaunchAgents/com.wb2api.gateway.<slug>.plist`、
    `launchctl bootstrap` 加载、把 `usage_file` 回填成 `instance/data/usage.json`
    （账号页「今日消耗」靠它合并各分组的台账）。
    """
    upstream = upstreamsvc.get_upstream(upstream_id)
    if upstream is None:
        raise HTTPException(status_code=404, detail='上游不存在')
    try:
        result = upstream_service.start(upstream, takeover=takeover)
    except upstream_service.ServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    instance_dir = Path(result['instance_dir'])
    if not str(upstream.get('usage_file') or '').strip():
        # 实例自己会写 data/usage.json：登记上，多分组「今日消耗」才能合并到它
        try:
            upstreamsvc.update_upstream(
                upstream_id, {'usage_file': str(instance_dir / 'data' / 'usage.json')},
            )
            result['steps'].append('登记 usage_file')
        except ValueError:
            pass
    security.audit(user, 'start_upstream_service', str(upstream.get('name') or ''),
                   f"id={upstream_id}；端口={result.get('port')}；pid={result.get('pid')}；"
                   f'来源 {client_ip(request)}')
    return result


@router.delete('/{upstream_id}/service')
def service_stop(upstream_id: int, request: Request,
                 user: dict = Depends(security.require_session_admin)) -> dict:
    """把该分组的上游实例从 launchd 摘掉（进程结束，文件都留着）。"""
    upstream = upstreamsvc.get_upstream(upstream_id)
    if upstream is None:
        raise HTTPException(status_code=404, detail='上游不存在')
    try:
        result = upstream_service.stop(upstream)
    except upstream_service.ServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    security.audit(user, 'stop_upstream_service', str(upstream.get('name') or ''),
                   f"id={upstream_id}；来源 {client_ip(request)}")
    return result


@router.delete('/{upstream_id}')
def delete_upstream(upstream_id: int, request: Request,
                    user: dict = Depends(security.require_session_admin)) -> dict:
    upstream = upstreamsvc.get_upstream(upstream_id)
    if upstream is None:
        raise HTTPException(status_code=404, detail='上游不存在')
    # 分组里还有账号时拒绝删除：删记录会让那个账号目录从面板里消失——文件仍在
    # 磁盘上，但面板不再有它的任何入口（移不走、删不掉、也看不见），只能去
    # 宿主机上手动处理。先把账号移走或删除，再删分组。
    auth_dir = str(upstream.get('auth_dir') or '').strip()
    if auth_dir:
        remaining = len(wb2api.list_auth_accounts(Path(auth_dir)))
        if remaining:
            raise HTTPException(
                status_code=409,
                detail=(f'分组「{upstream["name"]}」里还有 {remaining} 个账号，'
                        '先把它们移走或删除，再删除分组'
                        '（本操作只删分组记录，不会删除账号目录与文件）'),
            )
    deleted, used = upstreamsvc.delete_upstream(upstream_id)
    if not deleted:
        if used:
            # 409 而不是 400：请求本身没错，是**当前状态**不允许（还有密钥绑着它）。
            # 文案给出具体把数与下一步，管理员不用猜为什么删不掉。
            raise HTTPException(
                status_code=409,
                detail=(f'还有 {used} 把密钥绑定在这个上游上，先改绑或删掉它们再删除上游'
                        '（直接删会让那些密钥的请求全部报「上游不存在」）'),
            )
        raise HTTPException(status_code=404, detail='上游不存在')
    security.audit(user, 'delete_upstream', str(upstream_id), f'来源 {client_ip(request)}')
    return {'ok': True}


@router.post('/{upstream_id}/probe')
async def probe_upstream(upstream_id: int,
                         user: dict = Depends(security.require_admin)) -> dict:
    """探测某个上游是否可达（走它的 /healthz）。

    为什么要有：多上游部署里最常见的失误是「地址填对了但容器没起」或
    「填了另一个网段的地址」。等到下游调用失败才发现，排查成本高得多。
    探测结果原样返回失败原因（探测是管理员主动发起的，不是未认证端点）。
    """
    upstream = upstreamsvc.get_upstream(upstream_id)
    if upstream is None:
        raise HTTPException(status_code=404, detail='上游不存在')
    ok, message = await upstreamsvc.probe(upstream)
    return {'ok': ok, 'message': message}