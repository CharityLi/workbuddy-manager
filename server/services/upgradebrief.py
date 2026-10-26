"""升级对比简报：把「本地比上游最新发布少了什么、本地又多了什么」拼成一段人话。

用途是让维护者（或 AI 助手）在决定升级前，一眼看清三件事：

1. **上游有哪些版本本地还没有** —— 本地 tag 集合与 GitHub Releases 对比得出，
   而不是只看 `.version`：本地可能打了 tag 却没改 `.version`，反之亦然。
2. **本地有哪些上游没有的提交** —— `git log <本地最新 tag>..HEAD`。这些就是
   「升级时要小心别覆盖掉」的部分。
3. **本地定制清单** —— `docs/local-customizations.md` 里人工维护的条目，逐条写着
   「升级后怎么验证它还在」。清单本身不参与升级动作，只作为核对表。

只读：本模块不 fetch、不 checkout、不写仓库，唯一副作用是可能刷新 Releases
缓存（与「更新日志」页共用 `upstreamwatch` 的缓存）。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .. import config
from . import changelog, upstreamwatch

#: 简报里最多列多少个「上游有、本地没有」的版本（再多也没人看）。
MAX_MISSING = 12
#: 每个版本最多摘多少条要点。
MAX_POINTS_PER_VERSION = 3
#: 本地领先的提交最多列多少条。
MAX_LOCAL_COMMITS = 20
#: 定制清单最多列多少条。
MAX_CUSTOM_ITEMS = 40
#: 单条要点最多显示多少字（Release 正文是整段的话，全抄进来简报就没法看了）。
MAX_POINT_CHARS = 72
#: 版本 tag 的形状：只认 `v1.2.3` 这种，`backup/pre-rebase-1025` 之类不是版本。
_VERSION_TAG = re.compile(r'^v?\d+(?:\.\d+)*$')


def _git(*args: str, timeout: float = 8.0) -> str:
    """跑一条只读 git 命令；失败返回空串（简报不该因为 git 抽风而 500）。"""
    try:
        out = subprocess.run(
            ['git', '-C', str(config.ROOT), *args],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ''
    return out.stdout if out.returncode == 0 else ''


def local_tags() -> set[str]:
    """本地仓库里存在的版本 tag（去掉 `v` 前缀，便于与 Releases 的 tag 比对）。"""
    text = _git('tag', '--list')
    return {line.strip().lstrip('vV') for line in text.splitlines() if line.strip()}


def _norm(tag: str) -> str:
    return (tag or '').strip().lstrip('vV')


def _points(version: dict) -> list[str]:
    """从一版解析结果里摘几条要点（跨小节取，带小节名）。"""
    points: list[str] = []
    for section in version.get('sections') or []:
        for item in section.get('items') or []:
            text = (item.get('text') or '').strip()
            if not text:
                continue
            title = (section.get('title') or '').strip()
            if len(text) > MAX_POINT_CHARS:
                text = text[:MAX_POINT_CHARS].rstrip() + '…'
            points.append(f'{title}：{text}' if title else text)
            if len(points) >= MAX_POINTS_PER_VERSION:
                return points
    return points


def custom_checklist() -> list[str]:
    """`docs/local-customizations.md` 里的二级标题 —— 就是「升级后要逐条验证」的清单。"""
    path = config.ROOT / 'docs' / 'local-customizations.md'
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return []
    items: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith('## '):
            continue
        title = line[3:].strip()
        if not title:
            continue
        items.append(title)
        if len(items) >= MAX_CUSTOM_ITEMS:
            break
    return items


def _local_commits(base_tag: str) -> list[str]:
    if not base_tag:
        return []
    text = _git('log', '--oneline', f'-{MAX_LOCAL_COMMITS}', f'v{base_tag}..HEAD')
    return [line.strip() for line in text.splitlines() if line.strip()]


def _dirty() -> list[str]:
    text = _git('status', '--short')
    return [line.rstrip() for line in text.splitlines() if line.strip()]


def _latest_local_tag() -> str:
    """本地最新的版本 tag（按版本号比大小，不看 `git describe`）。"""
    numbered = []
    for tag in local_tags():
        if not _VERSION_TAG.match(tag):
            continue        # backup/... 之类的运维 tag 不是版本
        numbered.append((upstreamwatch._tag_tuple(tag), tag))  # noqa: SLF001
    numbered = [pair for pair in numbered if pair[0]]
    if not numbered:
        return ''
    return max(numbered)[1]


def brief(force: bool = False) -> dict:
    """生成对比简报（结构化字段 + 一段可直接粘贴的 markdown）。"""
    releases, fetched_at = upstreamwatch.cached_releases(force)
    versions = upstreamwatch.versions_from_releases(releases)
    local = _norm(upstreamwatch.local_version())
    latest = _norm(releases[0]['tag']) if releases else ''
    tags = local_tags()
    # 「上游有、本地没有」：本地既没有 tag、也不是 `.version` 里那版。
    missing = [v for v in versions if _norm(v['version']) not in tags and _norm(v['version']) != local]
    base = _latest_local_tag()
    commits = _local_commits(base)
    dirty = _dirty()
    checklist = custom_checklist()

    lines: list[str] = []
    if latest:
        relation = '已是最新发布' if not missing else f'落后 {len(missing)} 个发布版本'
        lines.append(f'本地 {("v" + local) if local else "(未知)"} / 上游最新 v{latest}：{relation}。')
    else:
        lines.append('没能取到上游发布列表（网络或限额问题），下面是本地侧的信息。')
    lines.append('')

    if missing:
        lines.append('## 上游有、本地没有的版本')
        for v in missing[:MAX_MISSING]:
            lines.append(f'- **v{v["version"]}**（{v.get("date") or "日期未知"}）')
            for point in _points(v):
                lines.append(f'  - {point}')
        extra = len(missing) - MAX_MISSING
        if extra > 0:
            lines.append(f'- 另有 {extra} 个更早的版本未列出。')
        lines.append('')

    lines.append('## 本地独有（上游没有的提交，升级时不要覆盖）')
    if commits:
        lines.extend(f'- {c}' for c in commits)
    else:
        lines.append('- 无（本地没有超出最新 tag 的提交）。')
    if dirty:
        lines.append(f'- 未提交改动 {len(dirty)} 个文件：' + '、'.join(d[:60] for d in dirty[:8]))
        if len(dirty) > 8:
            lines[-1] += ' 等'
    else:
        lines.append('- 工作区干净（没有未提交改动）。')
    lines.append('')

    if checklist:
        lines.append('## 本地定制核对清单（升级后逐条验证）')
        lines.extend(f'- {item}' for item in checklist)
        lines.append('')

    if missing:
        lines.append('## 结论')
        lines.append(
            f'可以更新：上游共有 {len(missing)} 个发布版本本地没有，'
            '但升级前请先按上面的清单确认本地定制功能不会丢。'
        )
    else:
        lines.append('## 结论')
        lines.append('本地已包含上游全部发布版本，无需升级。')

    return {
        'ok': True,
        'repo': upstreamwatch.repo(),
        'local_version': local,
        'latest': latest,
        'fetched_at': fetched_at,
        'missing': [
            {'version': v['version'], 'date': v.get('date'), 'points': _points(v)}
            for v in missing[:MAX_MISSING]
        ],
        'missing_count': len(missing),
        'local_commits': commits,
        'base_tag': base,
        'dirty_count': len(dirty),
        'checklist': checklist,
        'markdown': '\n'.join(lines).strip() + '\n',
    }


def markdown_path() -> Path:
    """简报里 `docs/local-customizations.md` 的路径（给前端提示「在哪维护清单」）。"""
    return config.ROOT / 'docs' / 'local-customizations.md'
