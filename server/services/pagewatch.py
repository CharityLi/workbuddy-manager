"""页面监控：定时抓页面，看「正文」和「前端资源」有没有变。

为什么分两个指纹：
- **正文指纹**（去掉标签后的可见文本）能抓到「活动文案换了 / 新增了一条公告」；
- **资源指纹**（页面引用的同源 js/css 的内容哈希）能抓到「文案没动但前端包换了」
  —— 这通常意味着产品发版了，光看正文是看不出来的。

两者都变才算「改版」，只变正文算「内容更新」，只变资源算「前端发版」。
噪声控制：完全没变就不写事件；只有变了才落一条，带一段差异摘要。

`detail` 里存的是差异摘要而不是整页 HTML：整页几万字符塞进库里既难查也没意义。
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
from urllib.parse import urljoin, urlparse

import httpx

from .. import db

_TAG = re.compile(r'<[^>]+>')
_SCRIPT = re.compile(r'<script\b[^>]*>.*?</script>', re.S | re.I)
_STYLE = re.compile(r'<style\b[^>]*>.*?</style>', re.S | re.I)
_COMMENT = re.compile(r'<!--.*?-->', re.S)
_ASSET = re.compile(r'''(?:src|href)\s*=\s*["']([^"']+)["']''', re.I)
_TITLE = re.compile(r'<title[^>]*>(.*?)</title>', re.S | re.I)

_ASSET_SUFFIX = ('.js', '.mjs', '.css')
_MAX_ASSETS = 40          # 一页引四五十个包很常见，全抓太慢也没必要
_FETCH_TIMEOUT = 20.0


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8', 'replace')).hexdigest()[:16]


def visible_text(html: str) -> str:
    """去掉脚本/样式/注释/标签后的正文，空白归一。"""
    s = _SCRIPT.sub(' ', html)
    s = _STYLE.sub(' ', s)
    s = _COMMENT.sub(' ', s)
    s = _TAG.sub('\n', s)
    lines = [ln.strip() for ln in s.splitlines()]
    return '\n'.join(ln for ln in lines if ln)


def title_of(html: str) -> str:
    m = _TITLE.search(html)
    return _TAG.sub('', m.group(1)).strip() if m else ''


def asset_urls(html: str, base: str) -> list[str]:
    """页面引用的同源 js/css 绝对地址（去重、保序、限量）。"""
    host = urlparse(base).netloc
    out: list[str] = []
    for raw in _ASSET.findall(html):
        if not raw.split('?')[0].lower().endswith(_ASSET_SUFFIX):
            continue
        full = urljoin(base, raw)
        if urlparse(full).netloc != host:
            continue
        if full not in out:
            out.append(full)
        if len(out) >= _MAX_ASSETS:
            break
    return out


def body_fingerprint(html: str) -> str:
    return _digest(visible_text(html))


def asset_fingerprint(html: str, base: str, contents: dict[str, str]) -> str:
    """把同源 js/css 的内容哈希按地址排序后合成一个总指纹。"""
    parts = [f'{u}:{_digest(contents.get(u, ""))}' for u in sorted(asset_urls(html, base))]
    return _digest('\n'.join(parts)) if parts else ''


def diff_summary(old: str, new: str) -> str:
    """粗略差异：新增/删除了几行文本，并把变化的前几行摘出来。"""
    old_lines = [x for x in old.splitlines() if x]
    new_lines = [x for x in new.splitlines() if x]
    old_set, new_set = set(old_lines), set(new_lines)
    added = [x for x in new_lines if x not in old_set]
    removed = [x for x in old_lines if x not in new_set]
    bits = []
    if added:
        bits.append(f'+{len(added)} 行：' + ' / '.join(added[:3])[:200])
    if removed:
        bits.append(f'-{len(removed)} 行：' + ' / '.join(removed[:3])[:200])
    return '；'.join(bits)[:500] or '内容顺序或空白有变化'


async def _fetch_assets(client: httpx.AsyncClient, urls: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for u in urls:
        try:
            r = await client.get(u)
            out[u] = r.text if r.status_code < 400 else ''
        except Exception:  # noqa: BLE001
            # 单个资源拉不到不影响整页判定：它只会让这次资源指纹里少一项
            out[u] = ''
    return out


async def check_page(row: dict, client: httpx.AsyncClient | None = None) -> dict:
    """抓一次页面并比对，变化则写一条 watch_events。返回本次结果。"""
    # db.query_one 给的是 sqlite3.Row（不支持 .get），这里转成普通 dict
    row = dict(row)
    page_id = int(row['id'])
    url = str(row['url'])
    now = int(time.time())
    own = client is None
    client = client or httpx.AsyncClient(timeout=_FETCH_TIMEOUT, follow_redirects=True,
                                         headers={'User-Agent': 'workbuddy-manager/pagewatch'})
    try:
        resp = await client.get(url)
        if resp.status_code >= 400:
            raise RuntimeError(f'HTTP {resp.status_code}')
        html = resp.text
        assets = await _fetch_assets(client, asset_urls(html, url))
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)[:300]
        db.execute('UPDATE watch_pages SET last_check = ?, last_ok = 0, last_error = ?, updated_at = ? WHERE id = ?',
                   (now, msg, now, page_id))
        db.execute('INSERT INTO watch_events (page_id, ts, kind, summary, detail) VALUES (?, ?, ?, ?, ?)',
                   (page_id, now, 'error', '抓取失败', msg))
        return {'ok': False, 'error': msg}
    finally:
        if own:
            await client.aclose()

    body_hash = body_fingerprint(html)
    asset_hash = asset_fingerprint(html, url, assets)
    text = visible_text(html)
    title = title_of(html)
    old_body = str(row.get('body_hash') or '')
    old_asset = str(row.get('asset_hash') or '')

    changed = bool(old_body) and (body_hash != old_body or asset_hash != old_asset)
    if changed:
        body_changed = body_hash != old_body
        asset_changed = bool(old_asset) and asset_hash != old_asset
        if body_changed and asset_changed:
            kind, summary = 'revamp', '页面正文与前端资源都变了（疑似改版）'
        elif asset_changed:
            kind, summary = 'asset', '前端资源变了（疑似发版）'
        else:
            kind, summary = 'body', '页面内容变了'
        db.execute('INSERT INTO watch_events (page_id, ts, kind, summary, detail) VALUES (?, ?, ?, ?, ?)',
                   (page_id, now, kind, summary, diff_summary(str(row.get('last_text') or ''), text)))

    db.execute(
        "UPDATE watch_pages SET last_check = ?, last_ok = 1, last_error = '', title = ?, "
        "body_hash = ?, asset_hash = ?, last_text = ?, updated_at = ? WHERE id = ?",
        (now, title, body_hash, asset_hash, text[:20000], now, page_id))
    return {'ok': True, 'changed': changed, 'body_hash': body_hash, 'asset_hash': asset_hash}


def due_pages(now: int | None = None) -> list[dict]:
    cur = int(now if now is not None else time.time())
    return [r for r in db.query('SELECT * FROM watch_pages WHERE enabled = 1')
            if cur - int(r['last_check'] or 0) >= int(r['interval_min'] or 30) * 60]


async def check_due() -> dict:
    rows = due_pages()
    if not rows:
        return {'checked': 0}
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT, follow_redirects=True,
                                 headers={'User-Agent': 'workbuddy-manager/pagewatch'}) as client:
        results = []
        for r in rows:
            results.append({'id': r['id'], **(await check_page(r, client))})
    return {'checked': len(rows), 'results': results}


_TICK = 60
_task: asyncio.Task | None = None


async def _loop() -> None:
    await asyncio.sleep(45)
    while True:
        try:
            await check_due()
        except Exception:  # noqa: BLE001
            pass
        await asyncio.sleep(_TICK)


def start_scheduler() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


def stop_scheduler() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        _task = None


def list_pages() -> list[dict]:
    """按配置顺序列出所有被监控页面（含最近一次抓取结果）。"""
    return [dict(r) for r in db.query('SELECT * FROM watch_pages ORDER BY id')]


def add_page(url: str, name: str = '', interval_min: int = 30) -> dict:
    """新增一个监控页面。地址必须带 http(s) 前缀，间隔最低 5 分钟。

    「最低 5 分钟」是硬限制：站点不是我们的，抓太勤既不礼貌也容易被风控。
    """
    url = (url or '').strip()
    if not re.match(r'^https?://[^\s]+$', url):
        raise ValueError('地址必须以 http:// 或 https:// 开头')
    now = int(time.time())
    db.execute(
        'INSERT INTO watch_pages (name, url, interval_min, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?)',
        ((name or '').strip() or url, url, max(5, int(interval_min or 30)), now, now))
    row = dict(db.query_one('SELECT * FROM watch_pages WHERE url = ?', (url,)))
    return row


def set_enabled(page_id: int, enabled: bool) -> dict:
    if not db.query_one('SELECT id FROM watch_pages WHERE id = ?', (int(page_id),)):
        raise ValueError('页面不存在')
    db.execute('UPDATE watch_pages SET enabled = ?, updated_at = ? WHERE id = ?',
               (1 if enabled else 0, int(time.time()), int(page_id)))
    return dict(db.query_one('SELECT * FROM watch_pages WHERE id = ?', (int(page_id),)))


def delete_page(page_id: int) -> None:
    db.execute('DELETE FROM watch_events WHERE page_id = ?', (int(page_id),))
    db.execute('DELETE FROM watch_pages WHERE id = ?', (int(page_id),))


def events(page_id: int | None = None, limit: int = 50) -> list[dict]:
    if page_id is None:
        rows = db.query('SELECT * FROM watch_events ORDER BY ts DESC, id DESC LIMIT ?', (int(limit),))
    else:
        rows = db.query('SELECT * FROM watch_events WHERE page_id = ? ORDER BY ts DESC, id DESC LIMIT ?',
                        (int(page_id), int(limit)))
    return [dict(r) for r in rows]


async def check_now(page_id: int) -> dict:
    row = db.query_one('SELECT * FROM watch_pages WHERE id = ?', (int(page_id),))
    if not row:
        raise ValueError('页面不存在')
    async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT, follow_redirects=True) as client:
        return await check_page(row, client)
