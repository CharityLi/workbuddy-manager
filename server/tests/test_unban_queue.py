"""解禁队列：入队/去重/到期/撤销，以及 state.json 的解禁写回。"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


class QueueTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from server import config, db
        cls._tmp = tempfile.TemporaryDirectory()
        cls._orig = config.DB_PATH
        config.DB_PATH = Path(cls._tmp.name) / 'unban.db'
        db._conn = None
        db.connect()
        from server.services import unbanqueue
        cls.q = unbanqueue

    @classmethod
    def tearDownClass(cls) -> None:
        from server import config, db
        if db._conn is not None:
            db._conn.close()
            db._conn = None
        config.DB_PATH = cls._orig
        cls._tmp.cleanup()

    def setUp(self) -> None:
        from server import db
        db.execute('DELETE FROM unban_queue')

    def test_add_then_due(self) -> None:
        self.q.add('u1', file='a.json', reason='12153', unlock_at=int(time.time()) - 1)
        rows = self.q.due()
        self.assertEqual([r['uid'] for r in rows], ['u1'])

    def test_future_item_not_due(self) -> None:
        self.q.add('u2', unlock_at=int(time.time()) + 600)
        self.assertEqual(self.q.due(), [])

    def test_same_uid_reuses_pending_row(self) -> None:
        first = self.q.add('u3', unlock_at=int(time.time()) + 60)
        second = self.q.add('u3', unlock_at=int(time.time()) - 1)
        self.assertEqual(first['id'], second['id'])
        self.assertTrue(second['updated'])
        self.assertEqual(len(self.q.items()), 1)

    def test_remove_only_pending(self) -> None:
        saved = self.q.add('u4', unlock_at=int(time.time()) + 60)
        self.assertTrue(self.q.remove(saved['id']))
        from server import db
        db.execute("UPDATE unban_queue SET status = 'done' WHERE id = ?", (saved['id'],))
        # 已完成的行不能再被删掉（要留审计）
        self.assertFalse(self.q.remove(saved['id']))

    def test_run_due_marks_done(self) -> None:
        self.q.add('u5', unlock_at=int(time.time()) - 1)
        with mock.patch.object(self.q.wb2api, 'force_enable_account',
                               new=mock.AsyncMock(return_value=(True, '已解禁', {}))):
            import asyncio
            out = asyncio.run(self.q.run_due())
        self.assertTrue(out['ok'])
        row = self.q.items()[0]
        self.assertEqual(row['status'], 'done')
        self.assertIn('已解禁', row['note'])

    def test_run_due_failure_recorded(self) -> None:
        self.q.add('u6', unlock_at=int(time.time()) - 1)
        with mock.patch.object(self.q.wb2api, 'force_enable_account',
                               new=mock.AsyncMock(return_value=(False, '上游未就绪', {}))):
            import asyncio
            out = asyncio.run(self.q.run_due())
        self.assertFalse(out['ok'])
        self.assertEqual(self.q.items()[0]['status'], 'failed')


class EnableStateTest(unittest.TestCase):
    def test_clears_all_blocking_flags(self) -> None:
        from server.services import wb2api
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / 'state.json'
        path.write_text(json.dumps({'accounts': {'u1': {
            'disabled': True,
            'manual_disabled': True,
            'until': '2099-12-31T23:59:59Z',
            'reason': '手动永久冷却',
            'manual_reason': '手动冷却 至 2099',
            'session_dead_fails': 3,
            'consecutive_fails': 5,
            'credits': 6616,
        }}}), encoding='utf-8')
        with mock.patch.object(wb2api, 'upstream_state_file', return_value=path):
            out = wb2api.set_account_enabled_state('u1')
        self.assertEqual(out['uid'], 'u1')
        entry = json.loads(path.read_text(encoding='utf-8'))['accounts']['u1']
        self.assertFalse(entry['disabled'])
        self.assertFalse(entry['manual_disabled'])
        self.assertEqual(entry['until'], '0001-01-01T00:00:00Z')
        self.assertNotIn('manual_reason', entry)
        self.assertEqual(entry['session_dead_fails'], 0)
        self.assertEqual(entry['consecutive_fails'], 0)
        # 解禁不能清掉积分等正交状态
        self.assertEqual(entry['credits'], 6616)


if __name__ == '__main__':
    unittest.main()
