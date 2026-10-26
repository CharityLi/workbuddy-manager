"""定位「上游日志到底写在哪个文件」。

为什么需要这一层
----------------
上游（workbuddy2api）把两类日志写到**不同的流**：

  * **聊天请求行**（带账号的那一行）走 **stdout** → launchd 的 `StandardOutPath`
    （实测 `data/gateway.out.log`）；
  * **自动任务行**（travel / checkin / blackcat …）与启动报错走 **stderr**
    → `StandardErrorPath`（实测 `data/gateway.err.log`）。

而本端过去只有 `WB2API_LOG_FILE` 一个配置项，两个采集器都读它 —— 于是某一边
必然一行都读不到（账号回填就整列空白，见 issue「密钥 dsh 未显示消耗账号」）。
这类问题很难从界面反推：日志文件存在、大小在涨、日志界面别处也正常，只是
**这一类行不在这个文件里**。

做法
----
不做「猜文件全名」的硬编码，而是在**候选集**里按「内容命中」挑选：
候选 = 显式配置的路径 + 同目录下的 `*.log`，按 mtime 降序逐个探一小段，
第一个真的含该类行的就用它。命中结果缓存一小段时间，避免每次轮询都开文件。

失败一律返回 None：采集是旁路，定位不到就当这次没有数据，绝不能抛到转发链路上。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from .. import config

logger = logging.getLogger('workbuddy.upstream')

#: 探测时读多少行（够验证「这个文件里有没有这类行」，又不必读全文件）
_PEEK_LINES = 400

#: 命中结果缓存多久（秒）。上游重建日志文件时会换文件，60 秒足以跟上。
_TTL = 60

#: kind → (path, 命中时刻, probe 标识)
_cache: dict[str, tuple[Path | None, float, str]] = {}


def _tail_lines(path: Path, limit: int) -> list[str]:
    """读文件尾部若干非空行；读不了就返回空列表。"""
    from collections import deque

    try:
        with path.open('r', encoding='utf-8', errors='replace') as fh:
            return [ln.rstrip('\r\n') for ln in deque(fh, maxlen=limit) if ln.strip()]
    except OSError:
        return []


def candidates(prefer: Path | None = None) -> list[Path]:
    """候选日志文件：显式路径优先，其后是同目录的 `*.log`（按 mtime 降序）。

    同目录扫描是关键：上游文件名随部署方式变（`gateway.out.log` / `server.err.log`
    / `wb2api.log` …），但**目录**是稳定的（都在 `data/` 下）。
    """
    seen: list[Path] = []
    if prefer is not None:
        seen.append(prefer)
    base = prefer.parent if prefer is not None else config.WB2API_LOG_FILE.parent
    try:
        found = [p for p in base.glob('*.log') if p.is_file()]
    except OSError:
        found = []
    found.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0.0, reverse=True)
    for path in found:
        if path not in seen:
            seen.append(path)
    return seen


def pick_log(kind: str, probe: Callable[[str], bool],
             prefer: Path | None = None) -> Path | None:
    """挑一个「真的含这类行」的日志文件；找不到返回 None。

    `probe(line)` 判断一行是否属于这一类（聊天行 / 任务行各传各的）。
    `kind` 只用于缓存分桶与日志，取值如 `'chat'`、`'task'`。
    """
    prefer = prefer or config.WB2API_LOG_FILE
    probe_id = getattr(probe, '__name__', 'probe')
    cached = _cache.get(kind)
    if cached and cached[2] == probe_id and time.time() - cached[1] < _TTL:
        path = cached[0]
        # 缓存里的文件被删/被换成空文件时不能继续用
        if path is None or (path.exists() and path.stat().st_size > 0):
            return path

    for path in candidates(prefer):
        lines = _tail_lines(path, _PEEK_LINES)
        if not lines:
            continue
        hits = sum(1 for line in lines if probe(line))
        if hits:
            _cache[kind] = (path, time.time(), probe_id)
            logger.debug('上游 %s 日志命中：%s（%d 行）', kind, path, hits)
            return path

    _cache[kind] = (None, time.time(), probe_id)
    logger.warning('没有找到含 %s 行的上游日志（候选：%s）',
                   kind, ', '.join(str(p) for p in candidates(prefer)[:6]) or '无')
    return None


def read_tail(path: Path, limit: int) -> tuple[list[str], float | None]:
    """读日志尾部，并返回 mtime（原生行只有时钟、没有日期，需要它做锚点）。"""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return [], None
    lines = _tail_lines(path, max(1, min(5000, limit)))
    return lines, mtime


def forget() -> None:
    """清空缓存（测试与「立即刷新」用）。"""
    _cache.clear()
