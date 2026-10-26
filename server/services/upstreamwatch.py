"""上游仓库每日巡检：**只记录，不跟随**。

为什么要有这个：本机跑的是 workbuddy2api 面板的一个 fork（`WB_UPSTREAM_DIR`），
本地有自己的改动、且刻意不跟上游同步。上游什么时候又发了新东西，原先只能人肉
去 GitHub 翻。这里每天 10:00 查一次远端 main，把「本地还没有的提交」追加进
**本地的** `CHANGELOG-UPSTREAM.md`，界面上与本地版本日志分开显示。

三条硬约束（改动前先读）：

1. **只记录不跟随**：不 fetch、不 pull、不 merge、不碰上游的工作区。远端信息
   全部走 GitHub 只读 API；判断「本地有没有这个提交」用 `git cat-file -e`
   这类只读查询。是不是要升级、什么时候升，由人决定。
2. **不污染上游的更新日志**：记录写进我们自己的 `CHANGELOG-UPSTREAM.md`
   （本仓库文件），上游仓库里的任何文件都不写。上游本身也没有 CHANGELOG。
3. **与本地日志分开显示**：`CHANGELOG.md` 是本仓库的版本日志（发布时写），
   `CHANGELOG-UPSTREAM.md` 是「上游有新提交了」的检测流水，两者互不覆盖。

为什么用 GitHub API 而不是 `git ls-remote`：ls-remote 只给 SHA，拿不到提交标题，
写出来的日志会是一串裸 SHA，没法看。API 不可用时才退化成 ls-remote（此时只在
状态里报告远端 SHA，不写日志条目）。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import re
import subprocess
import time
from typing import Any

from .. import config, db
from . import changelog as changelog_service
from .errtext import err_text

logger = logging.getLogger(__name__)

# 记录文件（本仓库根目录）。刻意与本地 `CHANGELOG.md` 分开。
LOG_PATH = config.ROOT / 'CHANGELOG-UPSTREAM.md'
STATE_KEY = 'upstream_watch'
# 巡检间隔：一天一次。手动「立即检查」会绕过它。
CHECK_TTL = 6 * 3600
# 每天几点跑（本地时区）。0–23，默认 10 点。
WATCH_HOUR = max(0, min(23, int((os.environ.get('WB_UPSTREAM_WATCH_HOUR') or '10'))))
# 一次最多看多少条远端提交：够看清「上游又动了什么」，也不至于把长历史刷进日志。
COMMIT_WINDOW = 30
_UA = 'workbuddy-manager-upstream-watch'

# 日志条目里用来去重的短 SHA（```abc12345```）
_SHORT_RE = re.compile(r'`([0-9a-f]{7,40})`')


# ── 本地只读查询 ────────────────────────────────────────────
def _git(args: list[str], timeout: int = 6) -> str | None:
    """在上游目录里跑一条**只读** git 命令；失败返回 None。

    只允许查询类命令（log/config/cat-file/rev-parse）。这里不做任何写操作：
    fetch/checkout/merge 会让上游工作区变样，违背「只记录不跟随」。
    """
    try:
        out = subprocess.run(
            ['git', '-C', str(config.UPSTREAM_DIR), *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def local_head() -> dict:
    """本地 HEAD 的 短SHA / 日期 / 标题；不是 git 仓库时返回空字典。"""
    out = _git(['log', '-1', '--format=%H%x00%ad%x00%s', '--date=short'])
    if not out:
        return {}
    parts = out.split('\x00')
    if len(parts) < 3:
        return {}
    sha, date, subject = parts[0], parts[1], parts[2]
    return {'sha': sha, 'short': sha[:8], 'date': date, 'subject': subject}


def app_version() -> str:
    """上游二进制的版本号（`cmd/server/main.go` 里的 `appVersion`）。

    为什么读源码而不问进程：上游没有 `--version`，`/status` 也不带版本。
    这是唯一能便宜拿到的版本标识，读不到就返回空串（界面显示「—」）。
    """
    path = config.UPSTREAM_DIR / 'cmd' / 'server' / 'main.go'
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ''
    m = re.search(r'appVersion\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else ''


def repo_slug() -> str:
    """远端仓库的 `owner/repo`：环境变量优先，其次从 origin 地址里解析。"""
    explicit = (os.environ.get('WB_UPSTREAM_REPO') or '').strip()
    if explicit:
        return parse_repo_slug(explicit) or explicit
    url = _git(['config', '--get', 'remote.origin.url']) or ''
    return parse_repo_slug(url)


def parse_repo_slug(url: str) -> str:
    """把各种写法的 git 地址归一成 `owner/repo`（HTTP / SSH / scp 风格都吃）。"""
    text = (url or '').strip()
    if not text:
        return ''
    m = re.search(r'github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/*$', text)
    if m:
        return f'{m.group(1)}/{m.group(2)}'
    m = re.fullmatch(r'([\w.-]+)/([\w.-]+)', text)
    return f'{m.group(1)}/{m.group(2)}' if m else ''


def in_local_history(shas: list[str]) -> set[str]:
    """这批 SHA 里，已经在本地 HEAD 历史上的那些（只读 `merge-base --is-ancestor`）。

    刻意不用 `cat-file -e`：本机是 fork，远端提交的对象很可能早就 fetch 到了
    （对象存在 ≠ 已合入）。曾经踩过——用「对象存在」判断，上游那个
    `docs: 停止维护声明` 被当成「本地已有」而漏报。这里问的是「它在不在
    HEAD 的祖先链上」，这才是「本地跟没跟上」的真正含义。
    """
    return {sha for sha in shas
            if _git(['merge-base', '--is-ancestor', sha, 'HEAD']) is not None}


# ── 远端查询 ────────────────────────────────────────────────
def remote_commits(limit: int = COMMIT_WINDOW) -> list[dict]:
    """远端 main 最近的提交（GitHub 只读 API）。失败抛 RuntimeError。"""
    slug = repo_slug()
    if not slug:
        raise RuntimeError('认不出上游仓库地址（看 WB_UPSTREAM_DIR 的 origin，'
                           '或设 WB_UPSTREAM_REPO=owner/repo）')
    from . import updater  # 复用它的镜像/Token/UA 处理，避免两套实现漂移
    url = (f'https://api.github.com/repos/{slug}/commits'
           f'?per_page={max(1, min(100, limit))}')
    try:
        data = updater._gh_get(url, timeout=12)  # noqa: SLF001 —— 同包复用
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f'拉取上游提交失败（{slug}）：{err_text(exc)}') from exc
    if not isinstance(data, list):
        raise RuntimeError(f'上游返回的不是提交列表（{slug}）')
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        sha = str(item.get('sha') or '')
        commit = item.get('commit') or {}
        if not sha or not isinstance(commit, dict):
            continue
        author = commit.get('author') or {}
        message = str(commit.get('message') or '').strip()
        out.append({
            'sha': sha,
            'short': sha[:8],
            'date': str(author.get('date') or '')[:10],
            'subject': message.splitlines()[0] if message else '(无标题)',
            'author': str(author.get('name') or ''),
        })
    return out


def remote_head_sha() -> str:
    """只问远端 main 的 SHA（API 不通时的降级路径，不发写操作）。"""
    out = _git(['ls-remote', 'origin', 'refs/heads/main'], timeout=12)
    if not out:
        return ''
    return out.split()[0] if out.split() else ''


# ── 状态 ────────────────────────────────────────────────────
def state() -> dict:
    raw = db.get_setting(STATE_KEY, {}) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _save_state(**kw: Any) -> dict:
    st = state()
    st.update(kw)
    db.set_setting(STATE_KEY, st)
    return st


# ── 写日志（只写我们自己的文件）──────────────────────────────
def _logged_shas() -> set[str]:
    """日志里已经记过的 SHA（去重靠它，重复巡检不会刷屏）。"""
    try:
        text = LOG_PATH.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return set()
    return {m.group(1) for m in _SHORT_RE.finditer(text)}


def _append_block(commits: list[dict], remote_sha: str) -> bool:
    """把一批新提交追加成一段，格式与 `CHANGELOG.md` 一致（复用同一个解析器）。

    小节标题用「上游提交」：界面上的分类配色表里没有这一项，会落到中性样式——
    这是有意的，上游提交不该被染成「修复」或「新增」。
    """
    if not commits:
        return False
    day = dt.date.today().isoformat()
    head = f'## [上游 {day}] - {day}'
    lines = [head, f'### 上游提交']
    for c in commits:
        who = f'（{c["author"]}）' if c.get('author') else ''
        lines.append(f'- `{c["short"]}` {c["date"]} {c["subject"]}{who}')
    block = '\n'.join(lines) + '\n'

    try:
        existed = LOG_PATH.is_file()
        with LOG_PATH.open('a', encoding='utf-8') as fh:
            if not existed:
                fh.write(
                    '# 上游仓库更新日志（只记录，不跟随）\n\n'
                    '> 本文件由管理端每日巡检自动追加：只记录「上游有本地还没有的提交」。\n'
                    '> 它**不影响**本机运行的版本，也**不会**写进上游仓库的任何文件。\n'
                    '> 本地自己的版本日志见 `CHANGELOG.md`。\n\n')
            fh.write(block)
    except OSError as exc:
        logger.warning('写上游更新日志失败：%s', exc)
        return False
    logger.info('上游更新日志 +%d 条（远端 %s）', len(commits), remote_sha[:8])
    return True


# ── 巡检 ────────────────────────────────────────────────────
def check(force: bool = False) -> dict:
    """查一次上游。返回摘要，同时把状态落库、把新提交追加进本地日志。

    「新」的判定是**相对上次巡检**，不是相对本地历史：本机是 fork，本地历史里
    天然缺一堆上游的老提交（实测：`merge-base --is-ancestor` 对两条 10-21 的
    合并提交都返回 128，因为对象根本没 fetch 到）。若按「本地没有就算新」，
    第一次巡检就会把上游半年历史一次性倒进日志。所以：

      * 首次巡检（没有基线）：只记远端 HEAD 这一条，作为基线；
      * 之后：拿上次记的 `last_remote_sha` 在本次窗口里定位，它之后的才是新增；
        窗口里找不到（上游一次推了 30 条以上）就退化成「窗口内全部」，宁可多记。
    """
    st = state()
    now = int(time.time())
    age = now - int(st.get('checked_at') or 0)
    if not force and age < CHECK_TTL:
        return {'ok': True, 'skipped': True, 'checked_at': st.get('checked_at'),
                'repo': st.get('repo') or repo_slug(), 'new': 0,
                'message': '距上次检查不足 6 小时，跳过'}

    head = local_head()
    slug = repo_slug()
    try:
        commits = remote_commits()
    except RuntimeError as exc:
        # 网络/限流不该让功能"看起来没更新"：把错误留在状态里，界面照实显示
        sha = remote_head_sha()
        _save_state(checked_at=now, repo=slug, error=str(exc),
                    remote_sha=sha or st.get('remote_sha') or '',
                    local_sha=head.get('sha') or '')
        logger.warning('上游巡检失败：%s', exc)
        return {'ok': False, 'error': str(exc), 'repo': slug, 'checked_at': now,
                'remote_sha': sha, 'local_sha': head.get('sha') or '', 'new': 0}

    remote_sha = commits[0]['sha'] if commits else ''
    last_sha = str(st.get('last_remote_sha') or '')
    baseline = not last_sha

    if not commits:
        fresh: list[dict] = []
    elif baseline:
        fresh = commits[:1]              # 建基线：只记当前 HEAD
    else:
        idx = next((i for i, c in enumerate(commits) if c['sha'] == last_sha), None)
        fresh = commits if idx is None else commits[:idx]
        fresh = fresh[:10]               # 一次最多记 10 条，剩下的下次巡检接着记
    # 本地历史里已有的不必再记（例如我们后来自己合过上游那条）
    already = _logged_shas()
    hist = in_local_history([c['sha'] for c in fresh])
    fresh = [c for c in fresh
             if c['sha'] not in hist and c['short'] not in already and c['sha'] not in already]

    appended = _append_block(fresh, remote_sha)
    remote_newer = remote_sha not in in_local_history([remote_sha]) if remote_sha else None
    _save_state(checked_at=now, repo=slug, error='', remote_sha=remote_sha,
                last_remote_sha=remote_sha or last_sha,
                local_sha=head.get('sha') or '', local_subject=head.get('subject') or '',
                remote_subject=commits[0]['subject'] if commits else '',
                remote_date=commits[0]['date'] if commits else '',
                remote_newer=remote_newer, window=len(commits),
                appended_at=now if appended else st.get('appended_at', 0))
    return {'ok': True, 'skipped': False, 'repo': slug, 'checked_at': now,
            'remote_sha': remote_sha, 'local_sha': head.get('sha') or '',
            'new': len(fresh), 'appended': appended, 'baseline': baseline,
            'remote_newer': remote_newer, 'window': len(commits)}


def load_log() -> dict:
    """给界面用：本地记录的流水 + 本次检查的状态（**不含**任何上游文件内容）。"""
    st = state()
    head = local_head()
    try:
        text = LOG_PATH.read_text(encoding='utf-8', errors='replace')
    except OSError:
        text = ''
    versions = changelog_service.parse_changelog(text) if text else []
    total = len(versions)
    return {
        'available': bool(text),
        'path': str(LOG_PATH),
        'repo': st.get('repo') or repo_slug(),
        'checked_at': st.get('checked_at') or 0,
        'error': st.get('error') or '',
        'remote_sha': st.get('remote_sha') or '',
        'remote_short': (st.get('remote_sha') or '')[:8],
        'remote_subject': st.get('remote_subject') or '',
        'remote_date': st.get('remote_date') or '',
        'remote_newer': st.get('remote_newer'),
        'local_sha': head.get('sha') or '',
        'local_short': head.get('short') or '',
        'local_date': head.get('date') or '',
        'local_subject': head.get('subject') or '',
        'app_version': app_version(),
        'window': st.get('window') or 0,
        'watch_hour': WATCH_HOUR,
        # 界面据此写「只记录不跟随」的说明，避免用户以为它会自动升级
        'never_follow': True,
        'total': total,
        'versions': versions[:20],
        'truncated': total > 20,
    }


# ── 每日调度 ────────────────────────────────────────────────
def next_run(now: dt.datetime | None = None) -> dt.datetime:
    """下一次该跑的时刻（本地时区的 WATCH_HOUR 点整）。"""
    now = now or dt.datetime.now()
    target = now.replace(hour=WATCH_HOUR, minute=0, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return target


_scheduler_task: asyncio.Task | None = None


async def _loop() -> None:
    """启动后先补一次检查（可能已过 10 点），然后每天 WATCH_HOUR 跑。"""
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
