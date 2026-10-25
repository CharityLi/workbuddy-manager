"""cache_rate.py - 缓存命中率统计（2026-10-23 新增）

背景:
    用户要求在「反代上游」卡片下方补一块命中率统计（那里正好有空白 ✓）

数据源为什么不是自己的库:
    `usage_daily` **没有缓存列** ✗（只有 requests / prompt / completion / credit）
    `request_logs` 有 cache_* 三列 ✓ 但只记经过 manager 自己的请求 ✗
    ⇒ 真正的全量数据在**网关的 JSONL**：
      <WB_UPSTREAM_DIR>/data/request-logs/requests-YYYY-MM-DD.jsonl
    每条含 `cache_hit_tokens` / `cache_miss_tokens` / `completion_tokens` ✓

口径:
    命中率 = 命中 / (命中 + 未命中)     ← 输入侧 ✓ 不含输出 ✓
    「未命中 + 命中」就是上游报的输入 token 总量 ✓
    ★ 没有 cache 字段的记录**不计入分母** ✗（老上游不返回该字段 ⇒ 算了会把命中率拉低 ✗）

★ 纯只读 ✓ 只读 JSONL ✓ 不写任何文件 ✓
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query

from .. import config, security

router = APIRouter(tags=['usage'])


def _logdir() -> Path:
    """网关请求日志目录（跟随 WB_UPSTREAM_DIR ✓）"""
    return Path(config.UPSTREAM_DIR) / 'data' / 'request-logs'


@router.get('/cache-rate')
def cache_rate(
    days: int = Query(1, ge=1, le=30, description='统计天数（1=今天 ✓）'),
    user: dict = Depends(security.current_user),
) -> dict:
    """按天聚合的缓存命中率 ✓

    返回:
        {
          "days": 1,
          "hit": 142534144,       # 命中 tokens
          "miss": 8353981,        # 未命中 tokens
          "output": 267661,       # 输出 tokens（独立展示，不进分母）
          "rate": 94.5,           # 命中率百分比（一位小数）
          "records": 314,         # 参与统计的记录数（有 cache 字段的）
          "skipped": 13,          # 无 cache 字段被跳过的记录数
          "found": true
        }
    """
    logdir = _logdir()
    if not logdir.is_dir():
        return {'days': days, 'hit': 0, 'miss': 0, 'output': 0,
                'rate': 0.0, 'records': 0, 'skipped': 0, 'found': False}

    hit = miss = output = 0
    records = skipped = 0
    files_seen = 0

    for i in range(days):
        d = (date.today() - timedelta(days=i)).isoformat()
        f = logdir / f'requests-{d}.jsonl'
        if not f.is_file():
            continue
        files_seen += 1
        try:
            with f.open(encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    h = rec.get('cache_hit_tokens')
                    m = rec.get('cache_miss_tokens')
                    # ★ 两者都缺 = 老上游没返回缓存字段 ⇒ 跳过，不进分母 ✓
                    if h is None and m is None:
                        skipped += 1
                        continue
                    hit += int(h or 0)
                    miss += int(m or 0)
                    output += int(rec.get('completion_tokens') or 0)
                    records += 1
        except Exception:
            continue

    den = hit + miss
    rate = round(hit / den * 100, 1) if den else 0.0

    return {
        'days': days,
        'hit': hit,
        'miss': miss,
        'output': output,
        'rate': rate,
        'records': records,
        'skipped': skipped,
        'found': files_seen > 0,
    }
