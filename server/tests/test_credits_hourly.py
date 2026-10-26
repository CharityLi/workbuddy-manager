"""积分收支的「今日」小时视图（对齐上游 2.10.0 的 days=1 小时桶）。"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server import config, db  # noqa: E402
from server.routers import stats  # noqa: E402


def _today_start() -> int:
    return db.day_start_ts(time.strftime('%Y-%m-%d'))


def _add_income(ts: int, credits: float, uid: str = 'u1') -> None:
    db.execute(
        'INSERT INTO task_logs(ts,kind,uid,level,credits,message,dedup_key) '
        'VALUES(?,?,?,?,?,?,?)',
        (ts, 'credit', uid, 'info', credits, '签到 +积分', f'k{ts}{credits}'),
    )


def _add_usage(day: str, hour: int, credit: float) -> None:
    db.execute(
        'INSERT INTO usage_hourly(day,hour,key_id,model,requests,prompt_tokens,'
        'completion_tokens,credit,realm) VALUES(?,?,?,?,?,?,?,?,?)',
        (day, hour, 1, 'm', 1, 10, 5, credit, 'cn'),
    )


class CreditsHourlyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = config.DB_PATH
        config.DB_PATH = Path(self._tmp.name) / 'h.db'
        db._conn = None
        db.connect()

    def tearDown(self) -> None:
        if db._conn is not None:
            db._conn.close()
        db._conn = None
        config.DB_PATH = self._orig
        self._tmp.cleanup()

    def test_days_one_returns_24_hourly_buckets(self) -> None:
        t0 = _today_start()
        _add_income(t0 + 10 * 3600, 12.5)
        _add_usage(time.strftime('%Y-%m-%d'), 14, 3.5)
        out = stats.credits_flow(days=1, realm=None, user={'role': 'admin'})
        self.assertEqual(len(out['hourly']), 24, '今日视图必须固定 24 个桶')
        self.assertAlmostEqual(out['hourly'][10]['income'], 12.5)
        self.assertAlmostEqual(out['hourly'][14]['expense'], 3.5)
        self.assertAlmostEqual(out['hourly'][0]['income'], 0.0)

    def test_days_multi_has_no_hourly(self) -> None:
        # 上游 v2.11.0 的契约：多日视图**不带** hourly 键（配 granularity='day'），
        # 而不是带一个空列表——前端按 `Array.isArray` 兜底，两种都不炸，以较新的为准。
        out = stats.credits_flow(days=7, realm=None, user={'role': 'admin'})
        self.assertNotIn('hourly', out, '多天视图不返回小时桶')
        self.assertEqual(out['granularity'], 'day')

    def test_hourly_totals_match_daily_total(self) -> None:
        t0 = _today_start()
        _add_income(t0 + 9 * 3600, 7.0)
        _add_income(t0 + 21 * 3600, 4.0)
        out = stats.credits_flow(days=1, realm=None, user={'role': 'admin'})
        hi = sum(r['income'] for r in out['hourly'])
        self.assertAlmostEqual(hi, out['today']['income'],
                               msg='小时桶合计必须等于今日合计（否则两处数字会对不上）')


if __name__ == '__main__':
    unittest.main()
