"""请求日志的「思考强度」列。

只验证数据链路：`add_request_log(reasoning_effort=...)` 能落库、能读回，
没传时是 NULL（不是空串，也不是默认档）——界面上据此显示「—」。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


class EffortColumnTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from server import config, db
        cls._tmp = tempfile.TemporaryDirectory()
        cls._orig = config.DB_PATH
        config.DB_PATH = Path(cls._tmp.name) / 'effort.db'
        db._conn = None
        db.connect()

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
        db.execute('DELETE FROM request_logs')

    def _last(self):
        from server import db
        return db.query_one('SELECT reasoning_effort FROM request_logs ORDER BY id DESC LIMIT 1')

    def test_value_roundtrip(self) -> None:
        from server import db
        db.add_request_log(ts=1, key_id=None, ip='1.1.1.1', model='m', status=200,
                           reasoning_effort='high')
        self.assertEqual(self._last()['reasoning_effort'], 'high')

    def test_absent_is_null(self) -> None:
        from server import db
        db.add_request_log(ts=1, key_id=None, ip='1.1.1.1', model='m', status=200)
        self.assertIsNone(self._last()['reasoning_effort'])

    def test_column_exists_on_fresh_db(self) -> None:
        from server import db
        cols = {r[1] for r in db.query('PRAGMA table_info(request_logs)')}
        self.assertIn('reasoning_effort', cols)


if __name__ == '__main__':
    unittest.main()
