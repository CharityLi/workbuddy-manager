"""上游用量台账（data/usage.json）汇总的回归测试。

「今日消耗积分」的口径完全落在这里：哪个桶算今天、哪类桶算「不知道」而不算 0、
日桶与小时桶要不要叠加。这些判断错了，界面上显示的数字看起来正常但含义是错的
（把收入算进来、把没观测到的请求算成 0 消耗、跨过折叠边界后数字突然变小）。
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server import config  # noqa: E402
from server.services import usage_ledger  # noqa: E402

DAY = '2026-10-26'
UID_A = 'aaaaaaaa-1111-2222-3333-444444444444'
UID_B = 'bbbbbbbb-1111-2222-3333-444444444444'


def _bucket(scope: str, uid: str, cr: float, cn: int, q: int = 1) -> dict:
    return {'s': scope, 'r': 'cn', 'u': uid, 'm': 'hunyuan', 'q': q, 'e': 0,
            'p': 10, 'c': 5, 't': 15, 'cr': cr, 'cn': cn}


class UsageLedgerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / 'usage.json'
        self._old = getattr(config, 'WB2API_USAGE_FILE', None)
        config.WB2API_USAGE_FILE = self.path
        # 分组台账默认留空：本组用例只验证单文件口径，多分组另有用例显式打桩。
        self._group_patch = mock.patch.object(
            usage_ledger, '_group_usage_paths', return_value=[])
        self._group_patch.start()
        usage_ledger._cache.clear()

    def tearDown(self) -> None:
        self._group_patch.stop()
        if self._old is None:
            del config.WB2API_USAGE_FILE
        else:
            config.WB2API_USAGE_FILE = self._old
        usage_ledger._cache.clear()
        self.dir.cleanup()

    def _write(self, buckets: list) -> None:
        self.path.write_text(json.dumps({'version': 4, 'saved': '', 'buckets': buckets}),
                             encoding='utf-8')

    def _today_ts(self) -> float:
        """今天正午的时间戳（避免测试恰好跨过零点/flaky）。"""
        lt = time.localtime()
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 12, 0, 0, 0, 0, -1))

    def test_hour_buckets_summed_per_uid(self) -> None:
        day = usage_ledger.today_of(self._today_ts())
        self._write([
            _bucket(f'h:{day}T09', UID_A, 12.5, 3, q=4),
            _bucket(f'h:{day}T10', UID_A, 7.25, 2, q=2),
            _bucket(f'h:{day}T10', UID_B, 1.0, 1, q=1),
        ])
        usage = usage_ledger.today_usage(self._today_ts())
        assert usage is not None
        self.assertAlmostEqual(usage['credits'][UID_A], 19.75)
        self.assertAlmostEqual(usage['credits'][UID_B], 1.0)
        self.assertEqual(usage['requests'][UID_A], 6)
        self.assertAlmostEqual(usage['total'], 20.75)

    def test_day_bucket_counts_too(self) -> None:
        """小时桶过 90 天会折叠成日桶；只认小时桶会让老日期凭空少一截。"""
        day = usage_ledger.today_of(self._today_ts())
        self._write([
            _bucket(f'd:{day}', UID_A, 30.0, 5, q=5),
            _bucket(f'h:{day}T08', UID_A, 5.0, 1, q=1),
        ])
        usage = usage_ledger.today_usage(self._today_ts())
        assert usage is not None
        self.assertAlmostEqual(usage['credits'][UID_A], 35.0)

    def test_other_days_ignored(self) -> None:
        day = usage_ledger.today_of(self._today_ts())
        self._write([
            _bucket('h:2020-01-01T09', UID_A, 99.0, 3),
            _bucket('d:2020-01-01', UID_A, 99.0, 3),
            _bucket(f'h:{day}T09', UID_A, 2.0, 1),
        ])
        usage = usage_ledger.today_usage(self._today_ts())
        assert usage is not None
        self.assertAlmostEqual(usage['credits'][UID_A], 2.0)

    def test_no_credit_samples_is_not_zero(self) -> None:
        """`cn=0` = 上游没回过 usage.credit（观测缺失），不能伪造成「消耗 0」，
        但请求数照记 —— 否则界面上「跑了 40 次、消耗 0」会被当成免费。"""
        day = usage_ledger.today_of(self._today_ts())
        self._write([
            _bucket(f'h:{day}T09', UID_A, 0.0, 0, q=40),
            _bucket(f'h:{day}T10', UID_B, 3.5, 2, q=2),
        ])
        usage = usage_ledger.today_usage(self._today_ts())
        assert usage is not None
        self.assertNotIn(UID_A, usage['credits'])
        self.assertEqual(usage['requests'][UID_A], 40)
        self.assertAlmostEqual(usage['total'], 3.5)

    def test_missing_file_returns_none(self) -> None:
        """台账缺失必须回 None（界面显示「—」），不能回空字典冒充「今天没消耗」。"""
        self.assertIsNone(usage_ledger.today_usage(self._today_ts()))
        self.assertIsNone(usage_ledger.today_credit_by_uid(self._today_ts()))

    def test_broken_json_returns_empty_not_crash(self) -> None:
        self.path.write_text('{"buckets": [', encoding='utf-8')
        usage = usage_ledger.today_usage(self._today_ts())
        assert usage is not None
        self.assertEqual(usage['credits'], {})
        self.assertEqual(usage['total'], 0.0)

    def test_unknown_shape_is_tolerated(self) -> None:
        self.path.write_text(json.dumps({'version': 4, 'buckets': [None, 3, {'s': 5}]}),
                             encoding='utf-8')
        usage = usage_ledger.today_usage(self._today_ts())
        assert usage is not None
        self.assertEqual(usage['credits'], {})

    def test_cache_invalidated_on_rewrite(self) -> None:
        day = usage_ledger.today_of(self._today_ts())
        self._write([_bucket(f'h:{day}T09', UID_A, 1.0, 1)])
        first = usage_ledger.today_usage(self._today_ts())
        assert first is not None
        self.assertAlmostEqual(first['credits'][UID_A], 1.0)
        self._write([_bucket(f'h:{day}T09', UID_A, 1.0, 1),
                     _bucket(f'h:{day}T11', UID_A, 4.0, 1)])
        second = usage_ledger.today_usage(self._today_ts())
        assert second is not None
        self.assertAlmostEqual(second['credits'][UID_A], 5.0)

    def test_default_path_follows_log_file_dir(self) -> None:
        """未显式配置时取网关日志同目录 —— 两个文件本来就并排放着。"""
        del config.WB2API_USAGE_FILE
        old_log = config.WB2API_LOG_FILE
        try:
            config.WB2API_LOG_FILE = Path('/tmp/wb2api-test/gateway.err.log')
            self.assertEqual(usage_ledger._usage_path(),
                             Path('/tmp/wb2api-test/usage.json'))
        finally:
            config.WB2API_LOG_FILE = old_log

    # ---- 多分组：多个 usage.json 合并（账号页「今日消耗」跨分组） -------------

    def _write_at(self, path: Path, buckets: list) -> Path:
        path.write_text(json.dumps({'version': 4, 'saved': '', 'buckets': buckets}),
                        encoding='utf-8')
        return path

    def test_group_ledgers_are_merged_per_uid(self) -> None:
        day = usage_ledger.today_of(self._today_ts())
        self._write([_bucket(f'h:{day}T09', UID_A, 12.5, 3, q=4)])
        group = self._write_at(Path(self.dir.name) / 'usage-liushui.json',
                               [_bucket(f'h:{day}T10', UID_B, 7.25, 2, q=2)])
        with mock.patch.object(usage_ledger, '_group_usage_paths', return_value=[group]):
            usage = usage_ledger.today_usage(self._today_ts())
        assert usage is not None
        self.assertEqual(usage['credits'], {UID_A: 12.5, UID_B: 7.25})
        self.assertEqual(usage['requests'], {UID_A: 4, UID_B: 2})
        self.assertEqual(usage['total'], 19.75)
        self.assertEqual(usage['sources'], 2)

    def test_missing_group_file_is_skipped_not_zeroed(self) -> None:
        """分组台账还没生成（新实例没跑过请求）不能把整表打成「读不到」。"""
        day = usage_ledger.today_of(self._today_ts())
        self._write([_bucket(f'h:{day}T09', UID_A, 5.0, 1)])
        ghost = Path(self.dir.name) / 'nope.json'
        with mock.patch.object(usage_ledger, '_group_usage_paths', return_value=[ghost]):
            usage = usage_ledger.today_usage(self._today_ts())
        assert usage is not None
        self.assertEqual(usage['sources'], 1)
        self.assertEqual(usage['credits'], {UID_A: 5.0})

    def test_no_ledger_at_all_returns_none(self) -> None:
        with mock.patch.object(usage_ledger, '_group_usage_paths',
                               return_value=[Path(self.dir.name) / 'nope.json']):
            self.assertIsNone(usage_ledger.today_usage(self._today_ts()))

    def test_duplicate_paths_counted_once(self) -> None:
        """同一份文件被配了两次（默认组与分组指到同一路径）不能重复累加。"""
        day = usage_ledger.today_of(self._today_ts())
        self._write([_bucket(f'h:{day}T09', UID_A, 8.0, 1)])
        with mock.patch.object(usage_ledger, '_group_usage_paths', return_value=[self.path]):
            usage = usage_ledger.today_usage(self._today_ts())
        assert usage is not None
        self.assertEqual(usage['credits'], {UID_A: 8.0})
        self.assertEqual(usage['sources'], 1)

    def test_group_paths_only_from_enabled_upstreams_with_usage_file(self) -> None:
        rows = [
            {'id': 1, 'enabled': True, 'usage_file': '/tmp/a/usage.json'},
            {'id': 2, 'enabled': False, 'usage_file': '/tmp/b/usage.json'},
            {'id': 3, 'enabled': True, 'usage_file': ''},
        ]
        # setUp 把 _group_usage_paths 打桩成空列表；这里要验真实逻辑，先撤桩
        self._group_patch.stop()
        with mock.patch('server.upstreamsvc.list_upstreams', return_value=rows):
            paths = usage_ledger._group_usage_paths()
        self._group_patch.start()  # 交回 setUp 的打桩，tearDown 统一 stop
        self.assertEqual([str(x) for x in paths], ['/tmp/a/usage.json'])


if __name__ == '__main__':
    unittest.main()
