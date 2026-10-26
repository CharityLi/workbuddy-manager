"""上游仓库每日巡检（GitHub Releases）。

「上游」指本项目自己的发布仓库 `ithtelab/workbuddy-manager` 的 Releases ——
正式发布的版本都在那边，本机跑的是从它派生出来的部署。

做的事只有一件：**每天定时去 Releases 拉一次列表，缓存起来给界面的「上游更新」
一栏用**；发现比上次记录更新的版本时，把这件事记进状态（界面顶部据此提示）。

边界（重要）：
  * **只读**：只调 GitHub 的 releases 列表接口（复用 updater 的镜像/Token 逻辑），
    不下载任何产物、不升级、不改动任何本地文件；
  * **不另抄一份更新日志**：上游版本的正文就来自 Release 本身，本地再存一份只会
    出现两份会互相漂移的副本。本地 `CHANGELOG.md` 只放还没发布的改动；
  * **不跟随升级**：是否升级由用户点「设置 → 系统更新」决定。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os

from .. import config, db
from . import changelog, updater

logger = logging.getLogger(__name__)

#: 默认上游仓库（可用 `WB_UPSTREAM_REPO` 覆盖，便于自建镜像/分支试跑）
DEFAULT_REPO = 'ithtelab/workbuddy-manager'

#: 界面上最早列到哪一版。v1.0.87 是「取消 Release 签名」的分界版，再往前的版本
#: 只对旧更新器有意义（界面已无处可点），列出来只会淹没最近的改动。
DEFAULT_MIN_TAG = 'v1.0.87'

#: 缓存多久算新鲜（秒）。页面打开时只读缓存，不在请求里干等 GitHub。
CACHE_TTL = 6 * 3600

#: 巡检时刻（本地时间，整数小时）。启动后会先补一次检查。
WATCH_HOUR = int(os.environ.get('WB_UPSTREAM_WATCH_HOUR', '10') or 10)

#: 一页 100 条；最多翻几页（够覆盖到 min_tag 就行，防上游刷版本时无限翻页）
_PER_PAGE = 100
_MAX_PAGES = 4

STATE_KEY = 'upstream_watch'
CACHE_KEY = 'upstream_releases'


def repo() -> str:
    """上游仓库（`owner/name`）。"""
    return (os.environ.get('WB_UPSTREAM_REPO') or DEFAULT_REPO).strip().strip('/')


def min_tag() -> str:
    """界面列出的最早版本。"""
    return (os.environ.get('WB_UPSTREAM_MIN_TAG') or DEFAULT_MIN_TAG).strip()


def _tag_tuple(tag: str) -> tuple:
    """`v2.10.2` → (2, 10, 2)。解析不出数字就返回空元组。"""
    raw = (tag or '').strip().lstrip('vV')
    parts = []
    for piece in raw.split('.'):
        digits = ''.join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _newer(a: str, b: str) -> bool:
    """a 是否比 b 新（按版本号比，不按字符串比：v2.10.2 > v2.9.0）。"""
    ta, tb = _tag_tuple(a), _tag_tuple(b)
    if not ta or not tb:
        return False
    return ta > tb


def _get(path: str):
    """取 GitHub JSON，走 updater 的镜像/Token/UA 逻辑（全仓一套口径）。"""
    return updater._gh_get(f'https://api.github.com/repos/{repo()}{path}')  # noqa: SLF001


def fetch_releases() -> list[dict]:
    """拉 Releases 列表（新的在前），拉到 `min_tag` 为止。

    失败向上抛：调用方要么把错误写进状态，要么沿用旧缓存 —— 不能装作「没有更新」。
    """
    floor = _tag_tuple(min_tag())
    out: list[dict] = []
    for page in range(1, _MAX_PAGES + 1):
        data = _get(f'/releases?per_page={_PER_PAGE}&page={page}')
        if not isinstance(data, list) or not data:
            break
        stop = False
        for rel in data:
            tag = (rel.get('tag_name') or '').strip()
            if not tag or rel.get('draft'):
                continue
            t = _tag_tuple(tag)
            if floor and t and t < floor:
                stop = True
                break
            out.append({
                'tag': tag,
                'name': (rel.get('name') or tag).strip(),
                'date': (rel.get('published_at') or rel.get('created_at') or '')[:10],
                'body': rel.get('body') or '',
                'prerelease': bool(rel.get('prerelease')),
            })
        if stop or len(data) < _PER_PAGE:
            break
    out.sort(key=lambda r: _tag_tuple(r['tag']), reverse=True)
    return out


def _cache() -> dict:
    raw = db.get_setting(CACHE_KEY) or {}
    return raw if isinstance(raw, dict) else {}


def _save_cache(releases: list[dict]) -> None:
    db.set_setting(CACHE_KEY, {'repo': repo(), 'min_tag': min_tag(),
                               'fetched_at': int(dt.datetime.now().timestamp()),
                               'releases': releases})


def cached_releases(force: bool = False) -> tuple[list[dict], int]:
    """返回 (releases, fetched_at)。缓存新鲜就不联网；`force` 强制刷新。

    仓库或下限改过时缓存作废（否则换了仓库还在显示上一家的版本）。
    """
    c = _cache()
    stamp = int(c.get('fetched_at') or 0)
    fresh = (c.get('repo') == repo() and c.get('min_tag') == min_tag()
             and stamp > 0 and int(dt.datetime.now().timestamp()) - stamp < CACHE_TTL)
    if fresh and not force and c.get('releases'):
        return list(c['releases']), stamp
    releases = fetch_releases()
    _save_cache(releases)
    return releases, int(dt.datetime.now().timestamp())


def versions_from_releases(releases: list[dict]) -> list[dict]:
    """把 Release 正文喂给本地更新日志的**同一个解析器**，得到界面能直接渲染的结构。

    Release 正文本身就是「小节 + 条目」的写法（`### 修复` / `- …`），与 CHANGELOG
    同构；补一行版本标题就能复用 `parse_changelog`，不必为上游再造一套渲染。
    """
    versions: list[dict] = []
    for rel in releases:
        tag = rel['tag'].lstrip('vV')
        text = f'## [{tag}] - {rel["date"]}\n{rel.get("body") or ""}'
        parsed = changelog.parse_changelog(text)
        if parsed:
            v = dict(parsed[0])
        else:
            v = {'version': tag, 'date': rel['date'], 'unreleased': False, 'sections': []}
        v['version'] = tag
        v['date'] = rel['date']
        v['unreleased'] = False
        v['prerelease'] = bool(rel.get('prerelease'))
        versions.append(v)
    return versions


def local_version() -> str:
    """本机运行的版本（`.version`，形如 `v2.9.0`）。"""
    try:
        return (config.ROOT / '.version').read_text(encoding='utf-8').strip()
    except OSError:
        return ''


def state() -> dict:
    raw = db.get_setting(STATE_KEY) or {}
    return raw if isinstance(raw, dict) else {}


def _save_state(**kw) -> None:
    st = state()
    st.update({k: v for k, v in kw.items() if v is not None})
    db.set_setting(STATE_KEY, st)


def check(force: bool = False) -> dict:
    """巡检一次：刷新 Releases 缓存，发现新版本就把事件记进状态。

    「新」是**相对上次记录**：首次巡检只把当前最新版记成基线，不会把历史版本
    全报一遍。
    """
    st = state()
    now = int(dt.datetime.now().timestamp())
    if not force and int(st.get('checked_at') or 0) and now - int(st['checked_at']) < CACHE_TTL:
        return {'ok': True, 'skipped': True, 'checked_at': int(st['checked_at']),
                'latest': st.get('latest_tag', ''), 'new_tags': []}
    try:
        releases, fetched_at = cached_releases(force=True)
    except Exception as exc:  # noqa: BLE001 —— 巡检失败不能拖垮进程
        logger.warning('上游巡检失败：%s', exc)
        _save_state(checked_at=now, error=str(exc), repo=repo())
        return {'ok': False, 'skipped': False, 'checked_at': now,
                'error': str(exc), 'latest': st.get('latest_tag', ''), 'new_tags': []}

    latest = releases[0]['tag'] if releases else ''
    seen = (st.get('latest_tag') or '').strip()
    if not seen:
        new_tags = [latest] if latest else []          # 基线：只记最新一版
        baseline = True
    else:
        new_tags = [r['tag'] for r in releases if _newer(r['tag'], seen)]
        baseline = False
        new_tags.reverse()                              # 按时间正序，便于阅读
    _save_state(checked_at=now, error='', repo=repo(), latest_tag=latest or seen,
                fetched_at=fetched_at,
                baseline_at=(now if baseline else st.get('baseline_at')),
                new_at=(now if new_tags and not baseline else st.get('new_at')),
                new_tags=new_tags, total=len(releases))
    if new_tags and not baseline:
        logger.info('上游有新版本：%s', '、'.join(new_tags))
    return {'ok': True, 'skipped': False, 'checked_at': now, 'latest': latest,
            'baseline': baseline, 'new_tags': new_tags, 'total': len(releases)}


def load_log() -> dict:
    """给界面用：上游版本列表 + 巡检状态 + 本机当前版本。"""
    st = state()
    releases = list(_cache().get('releases') or [])
    error = ''
    if not releases:
        # 一次都没拉过（新装/刚清缓存）：这里联网一次，失败就把错误如实带回界面
        try:
            releases, _ = cached_releases(force=True)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
    versions = versions_from_releases(releases)
    latest = releases[0]['tag'] if releases else st.get('latest_tag', '')
    return {
        'available': bool(releases),
        'repo': repo(),
        'error': error or st.get('error', ''),
        'checked_at': int(st.get('checked_at') or 0),
        'fetched_at': int(_cache().get('fetched_at') or 0),
        'latest': latest,
        'local_version': local_version(),
        'behind': _newer(latest, local_version()) if latest else None,
        'min_tag': min_tag(),
        'watch_hour': WATCH_HOUR,
        'never_follow': True,
        'new_tags': list(st.get('new_tags') or []),
        'total': len(versions),
        'versions': versions,
    }


# ── 每日调度 ────────────────────────────────────────────────


def next_run(now: dt.datetime | None = None) -> dt.datetime:
    """下一次巡检时刻：今天 WATCH_HOUR 点；已过点则明天。"""
    now = now or dt.datetime.now()
    target = now.replace(hour=WATCH_HOUR, minute=0, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return target


_scheduler_task: asyncio.Task | None = None


async def _loop() -> None:
    """启动后先补一次检查（可能已过点），然后每天 WATCH_HOUR 跑。"""
    await asyncio.sleep(30)          # 避开启动期其他初始化
    while True:
        try:
            await asyncio.to_thread(check)
        except asyncio.CancelledError:
            raise
        except Exception as exc:      # noqa: BLE001 —— 巡检失败不能拖垮进程
            logger.warning('上游巡检异常：%s', exc)
        delay = (next_run() - dt.datetime.now()).total_seconds()
        await asyncio.sleep(max(60.0, delay))


def start_scheduler() -> bool:
    """启动每日巡检（幂等）。没有事件循环时返回 False。"""
    global _scheduler_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = loop.create_task(_loop())
    return True


def stop_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
    _scheduler_task = None
