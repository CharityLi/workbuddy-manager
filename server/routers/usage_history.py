"""usage_history.py - 只读历史用量接口（2026-10-23 新增）

背景:
    manager 的仪表盘只统计"经过它自己的请求" ✗
    而网关早就有一份完整历史: <WB_UPSTREAM_DIR>/data/usage.json ✓
    ⇒ 这里把它读出来，按天聚合，供前端画趋势图 ✓

数据形态（实测 ✓）:
    {"version":4,"saved":"...","buckets":[
      {"s":"h:2026-10-13T22","r":"cn","u":"<uid>","m":"deepseek-v4.1-flash",
       "q":16,"cr":6.48,"t":9042766,"c":18898,...}, ...]}

口径:
    s  = 小时桶 "h:YYYY-MM-DDTHH"（本地时间 ✓）
    q  = 请求次数 ✓
    cr = 积分消耗 ✓
    t  = 总 token（含 in/out 汇总 ✓）   c = completion token ✓
    u  = 账号 uid ✓   m = 模型 ✓

★ 纯只读 ✓ 不写任何文件 ✓ 不改网关 ✓
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query

from .. import config, security

router = APIRouter(tags=['usage'])


def _usage_path() -> Path:
    """网关的 usage.json 位置（跟随 WB_UPSTREAM_DIR ✓）"""
    return Path(config.UPSTREAM_DIR) / 'data' / 'usage.json'


def _parse_bucket_ts(s: str) -> datetime | None:
    """把 "h:2026-10-13T22" 解析成 datetime（支持日桶 "d:" 前缀 ✓）"""
    if not s:
        return None
    raw = s.split(':', 1)[1] if ':' in s else s
    for fmt in ('%Y-%m-%dT%H', '%Y-%m-%d'):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


@router.get('/usage-history')
def usage_history(
    days: int = Query(14, ge=1, le=90, description='回看天数（含今天 ✓）'),
    user: dict = Depends(security.current_user),
) -> dict:
    """按天聚合的历史用量 ✓

    返回:
        {
          "days": 3,
          "series": [ {"date":"2026-10-21","requests":123,"credits":45.6,
                       "tokens":1234567,"accounts":5,"models":3}, ... ],
          "totals": {"requests":..., "credits":..., "tokens":...},
          "source": "<usage.json 路径>",
          "found": true|false
        }
    """
    path = _usage_path()
    if not path.exists():
        return {'days': days, 'series': [], 'totals': {}, 'source': str(path), 'found': False}

    try:
        doc = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:  # noqa: BLE001 - 读失败也要给前端可读信息 ✓
        return {'days': days, 'series': [], 'totals': {}, 'source': str(path),
                'found': False, 'error': f'读取失败: {exc}'}

    buckets = doc.get('buckets') or []
    cutoff = (datetime.now() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

    # 按天聚合：requests / credits / tokens / 账号集合 / 模型集合 ✓
    agg: dict[str, dict] = defaultdict(lambda: {
        'requests': 0, 'credits': 0.0, 'tokens': 0, 'accounts': set(), 'models': set()})

    for b in buckets:
        ts = _parse_bucket_ts(str(b.get('s') or ''))
        if ts is None or ts < cutoff:
            continue
        key = ts.strftime('%Y-%m-%d')
        row = agg[key]
        row['requests'] += int(b.get('q') or 0)          # 请求次数 ✓
        row['credits'] += float(b.get('cr') or 0)        # 积分消耗 ✓
        row['tokens'] += int(b.get('t') or 0)            # 总 token ✓
        if b.get('u'):
            row['accounts'].add(str(b['u']))
        if b.get('m'):
            row['models'].add(str(b['m']))

    series = []
    for i in range(days):
        d = (cutoff + timedelta(days=i)).strftime('%Y-%m-%d')
        row = agg.get(d) or {'requests': 0, 'credits': 0.0, 'tokens': 0, 'accounts': set(), 'models': set()}
        series.append({
            'date': d,
            'requests': row['requests'],
            'credits': round(row['credits'], 2),
            'tokens': row['tokens'],
            'accounts': len(row['accounts']),
            'models': len(row['models']),
        })

    totals = {
        'requests': sum(x['requests'] for x in series),
        'credits': round(sum(x['credits'] for x in series), 2),
        'tokens': sum(x['tokens'] for x in series),
    }
    return {'days': days, 'series': series, 'totals': totals, 'source': str(path), 'found': True}
