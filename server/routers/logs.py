"""请求日志查询。"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException

from .. import db, keysvc, security

router = APIRouter(prefix='/api/logs', tags=['logs'])


# ═══════════════════════════════════════════════════════════════════
# 账号回填（2026-10-23 实现 ✓）
#
# 背景：request_logs.account 一直是 NULL ✗
#   代码注释说"账号是采集上游日志后按时间对回来的"——但那套回填**从来没实现** ✗
#   ⇒ 现在补上：读网关的 request-logs/*.jsonl（那里有 request_id + account ✓）
#   ⇒ 按 (秒级时间, 模型, 总 token) 匹配回来 ✓
#
# ★ 只读文件 ✓ 不改网关 ✓ 不改库 ✓ 每次查询现算 ✓（几万行也就毫秒级 ✓）
# ★ 匹配不上就保持 None ⇒ 前端显示「—」（与原来一致 ✓）
# ═══════════════════════════════════════════════════════════════════

_GW_INDEX_CACHE: tuple[float, dict] | None = None   # (mtime, index) ✓ 按 mtime 缓存


def _account_index_from_gateway(days: int = 2) -> dict[tuple, str]:
    """建 (ts秒, model, total_tokens) → account 的索引 ✓

    顺带建一份粗粒度 (ts秒, model) → account 兜底用 ✓
    返回结构：{'exact': {...}, 'loose': {...}}
    """
    global _GW_INDEX_CACHE
    import json as _json
    import time as _time
    from datetime import date, timedelta
    from pathlib import Path

    from .. import config

    logdir = Path(config.UPSTREAM_DIR) / 'data' / 'request-logs'
    if not logdir.is_dir():
        return {'exact': {}, 'loose': {}}

    files = []
    for i in range(days):
        d = (date.today() - timedelta(days=i)).isoformat()
        f = logdir / f'requests-{d}.jsonl'
        if f.is_file():
            files.append(f)
    if not files:
        return {'exact': {}, 'loose': {}}

    newest = max(f.stat().st_mtime for f in files)
    if _GW_INDEX_CACHE and abs(_GW_INDEX_CACHE[0] - newest) < 0.5:
        return _GW_INDEX_CACHE[1]                       # 缓存命中 ✓

    exact: dict[tuple, str] = {}
    loose: dict[tuple, str] = {}
    for f in files:
        try:
            with f.open(encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = _json.loads(line)
                    except Exception:
                        continue
                    acct = d.get('account')
                    if not acct:
                        continue
                    ts_raw = str(d.get('time') or '')
                    try:
                        # "2026-10-23T16:19:02.545072+08:00" → unix 秒（本地时区 ✓）
                        from datetime import datetime
                        ts = int(datetime.fromisoformat(ts_raw).timestamp())
                    except Exception:
                        continue
                    model = str(d.get('model') or '')
                    tot = int(d.get('total_tokens') or 0)
                    exact[(ts, model, tot)] = acct
                    loose[(ts, model)] = acct
        except Exception:
            continue
    idx = {'exact': exact, 'loose': loose}
    _GW_INDEX_CACHE = (newest, idx)
    return idx


@router.get('')
def list_logs(
    page: int = 1,
    size: int = 20,
    key_id: str | None = None,
    model: str | None = None,
    status: str | None = None,
    ip: str | None = None,
    days: int | None = None,
    realm: str | None = None,
    user: dict = Depends(security.current_user),
) -> dict:
    # 分页参数钳制（审计 2026-10-18）：`page` 无上限时，超大整数会在 SQLite
    # 绑定期溢出抛 500；`size` 无上限则可以一次拉走整张表（内存与响应体都不受控）。
    page = max(1, min(int(page), 100_000))
    size = max(1, min(int(size), 500))
    where: list[str] = []
    args: list[object] = []

    # 版本过滤（cn / global）。历史记录该列为 NULL —— 按 cn 归类，
    # 与 db.realm_of_model 的口径一致（无前缀即国内版）。
    if realm in ('cn', 'global'):
        if realm == 'cn':
            where.append('(l.realm = ? OR l.realm IS NULL)')
        else:
            where.append('l.realm = ?')
        args.append(realm)

    if days:
        # 钳到 [1, 3650]：超大值会在 SQLite 绑定时溢出（曾 500），
        # 负值则会把过滤条件变成「未来之后」，语义上无意义
        d = min(3650, max(1, int(days)))
        where.append('l.ts >= ?')
        args.append(int(time.time()) - d * 86400)
    if key_id and key_id not in ('all', ''):
        # 非数字会 int() 抛错 → 500。这里显式拦成 400，避免用非法输入探测
        try:
            kid = int(key_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail='key_id 必须是数字或 all') from None
        where.append('l.key_id = ?')
        args.append(kid)
    if model:
        where.append('(l.model LIKE ? OR l.mapped_model LIKE ?)')
        args.extend([f'%{model}%', f'%{model}%'])
    if ip:
        where.append('l.ip LIKE ?')
        args.append(f'%{ip}%')
    if status == 'ok':
        where.append('l.status >= 200 AND l.status < 300')
    elif status == 'error':
        where.append('(l.status < 200 OR l.status >= 300)')

    clause = ('WHERE ' + ' AND '.join(where)) if where else ''
    total = db.query_one(f'SELECT COUNT(*) AS c FROM request_logs l {clause}', args)['c']

    page = max(1, page)
    size = min(200, max(1, size))
    rows = db.query(
        f'SELECT l.*, k.name AS key_name FROM request_logs l '
        f'LEFT JOIN api_keys k ON k.id = l.key_id {clause} '
        f'ORDER BY l.id DESC LIMIT ? OFFSET ?',
        (*args, size, (page - 1) * size),
    )
    # ★ 2026-10-23：账号列补全（读网关 JSONL ✓ 只读 ✓）
    _acc_idx = _account_index_from_gateway(days=2)

    def _model_variants(name: str) -> list[str]:
        """模型名的多种写法 ✓

        为什么需要：manager 存的 `model` 是**客户端请求名**（不带前缀 ✓）
        而网关日志里是**实际发往上游的名字**（带 `cn:` / `global:` ✓）
        ⇒ 不补这几种变体，就永远匹配不上 ✗（实测：全部 miss ✓）
        """
        n = (name or '').strip()
        if not n:
            return []
        out = [n]
        if not n.startswith(('cn:', 'global:')):
            out += ['cn:' + n, 'global:' + n]
            # 去掉版本后缀的形态也可能出现（如 deepseek-v4.1-flash-xxx）✓
        return out

    def _fill_account(r) -> str | None:
        """优先用库里已有的 ✓；空则去网关日志里找 ✓；找不到返回 None ✓"""
        if r['account']:
            return r['account']
        ex = _acc_idx.get('exact') or {}
        lo = _acc_idx.get('loose') or {}
        ts = int(r['ts'] or 0)
        model = str(r['model'] or '')
        mapped = str(r['mapped_model'] or '')
        models = []
        for m in (model, mapped):
            for v in _model_variants(m):
                if v not in models:
                    models.append(v)
        tot = int((r['prompt_tokens'] or 0) + (r['completion_tokens'] or 0))
        # ① 精确匹配（秒 + 模型变体 + token 数 ✓ 基本唯一 ✓）
        for mm in models:
            k = (ts, mm, tot)
            if k in ex:
                return ex[k]
        # ② 退一步只按 秒 + 模型变体 ✓
        for mm in models:
            k = (ts, mm)
            if k in lo:
                return lo[k]
        # ③ 再宽一点：±5 秒内 + 同模型变体 ✓（时钟/耗时偏差 ✓）
        for dt in (-1, 1, -2, 2, -3, 3, -5, 5):
            for mm in models:
                k = (ts + dt, mm)
                if k in lo:
                    return lo[k]
        return None

    items = [
        {
            'id': r['id'],
            'ts': r['ts'],
            'key_id': r['key_id'],
            'key_name': r['key_name'],
            'ip': r['ip'],
            'model': r['model'],
            'mapped_model': r['mapped_model'],
            'status': r['status'],
            'realm': r['realm'] or 'cn',
            'prompt_tokens': r['prompt_tokens'],
            'completion_tokens': r['completion_tokens'],
            'latency_ms': r['latency_ms'],
            # 首字延迟：NULL 表示未采集（非流式请求或旧记录）
            'first_token_ms': r['first_token_ms'],
            # 提示词缓存三段（issue #69）：NULL = 上游没给这三个字段（老上游），
            # 与「给了 0」不同 —— 界面据此显示「—」而不是 0%。
            'cache_hit_tokens': r['cache_hit_tokens'],
            'cache_miss_tokens': r['cache_miss_tokens'],
            'cache_write_tokens': r['cache_write_tokens'],
            'ua': r['ua'],
            'error': r['error'],
            'stream': bool(r['stream']),
            'credit': r['credit'],
            # 本次实际用了哪个上游账号（issue #69）。NULL = 尚未回填 ——
            # 账号是采集上游日志后按时间对回来的，比请求本身晚几秒，
            # 所以刚打完的请求这一列可能是空的（界面显示「—」，刷新即可）。
            'account': _fill_account(r),
            # 思考强度（reasoning_effort）：客户端显式传的档位；NULL=没传/旧记录。
            # 本地面板写的是 reasoning_effort 列，上游客户端写 effort 列，两者都返回：
            # 旧前端读 reasoning_effort，新前端读 effort，缺一个都会显示成「—」。
            'effort': r['effort'],
            'reasoning_effort': r['reasoning_effort'],
            # 客户端渠道（issue #233）：UA 归类；NULL=该列上线前的旧记录。
            'channel': r['channel'],
            # 输入侧命中缓存的 token 数。NULL 与 0 含义不同：
            # NULL = 上游没给这个字段，0 = 这次确实没命中（见 db 的列说明）。
            'cache_hit_tokens': r['cache_hit_tokens'],
        }
        for r in rows
    ]
    return {'total': total, 'items': items}


@router.post('/clear')
def clear_logs(user: dict = Depends(security.require_session_admin)) -> dict:
    db.execute('DELETE FROM request_logs')
    return {'ok': True}
