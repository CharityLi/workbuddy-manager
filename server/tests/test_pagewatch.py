"""页面监控：正文/资源指纹与变化判定。"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from server.services import pagewatch


class FingerprintTest(unittest.TestCase):
    def test_visible_text_drops_scripts_and_tags(self) -> None:
        html = '''<html><head><title>活动</title><style>.a{}</style></head>
        <body><h1>夏日活动</h1><script>var a=1;</script><p>领 100 积分</p><!--x--></body></html>'''
        text = pagewatch.visible_text(html)
        self.assertIn('夏日活动', text)
        self.assertIn('领 100 积分', text)
        self.assertNotIn('var a=1', text)
        self.assertNotIn('.a{}', text)
        self.assertEqual(pagewatch.title_of(html), '活动')

    def test_body_hash_is_stable_against_attribute_noise(self) -> None:
        a = '<div class="x">你好</div>'
        b = '<div data-foo="1" class="y">  你好  </div>'
        self.assertEqual(pagewatch.body_fingerprint(a), pagewatch.body_fingerprint(b))

    def test_body_hash_changes_with_text(self) -> None:
        self.assertNotEqual(pagewatch.body_fingerprint('<p>旧文案</p>'),
                            pagewatch.body_fingerprint('<p>新文案</p>'))

    def test_asset_urls_only_same_host_and_static(self) -> None:
        html = ('<script src="/a.js"></script><link href="https://cdn.other/b.css">'
                '<link href="/c.css"><a href="/page.html">x</a><script src="/a.js"></script>')
        urls = pagewatch.asset_urls(html, 'https://example.com/x')
        self.assertEqual(urls, ['https://example.com/a.js', 'https://example.com/c.css'])

    def test_asset_hash_depends_on_content(self) -> None:
        html = '<script src="/a.js"></script>'
        h1 = pagewatch.asset_fingerprint(html, 'https://e.com/', {'https://e.com/a.js': 'v1'})
        h2 = pagewatch.asset_fingerprint(html, 'https://e.com/', {'https://e.com/a.js': 'v2'})
        self.assertNotEqual(h1, h2)

    def test_diff_summary_reports_added_and_removed(self) -> None:
        out = pagewatch.diff_summary('旧活动\n不变', '新活动\n不变')
        self.assertIn('+1 行', out)
        self.assertIn('-1 行', out)


class _Resp:
    def __init__(self, text: str, status: int = 200) -> None:
        self.text = text
        self.status_code = status


class CheckPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from server import config, db
        cls._tmp = tempfile.TemporaryDirectory()
        cls._orig = config.DB_PATH
        config.DB_PATH = Path(cls._tmp.name) / 'watch.db'
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
        db.execute('DELETE FROM watch_pages')
        db.execute('DELETE FROM watch_events')

    def _add(self, **kw):
        from server import db
        import time
        now = int(time.time())
        qid = db.execute(
            'INSERT INTO watch_pages (name, url, enabled, interval_min, last_check, last_ok, last_error,'
            ' title, body_hash, asset_hash, last_text, created_at, updated_at)'
            " VALUES ('t', ?, 1, 30, 0, 0, '', '', ?, '', '', ?, ?)",
            (kw["url"], kw.get("body_hash", ""), now, now))
        return db.query_one('SELECT * FROM watch_pages WHERE id = ?', (qid,))

    def test_first_check_records_no_event(self) -> None:
        row = self._add(url='https://e.com/')
        client = mock.AsyncMock()
        client.get = mock.AsyncMock(return_value=_Resp('<p>你好</p>'))
        out = asyncio.run(pagewatch.check_page(row, client))
        self.assertTrue(out['ok'])
        self.assertFalse(out['changed'])
        from server import db
        self.assertEqual(db.query('SELECT * FROM watch_events'), [])

    def test_changed_text_records_body_event(self) -> None:
        row = self._add(url='https://e.com/', body_hash=pagewatch.body_fingerprint('<p>旧</p>'))
        client = mock.AsyncMock()
        client.get = mock.AsyncMock(return_value=_Resp('<p>新</p>'))
        out = asyncio.run(pagewatch.check_page(row, client))
        self.assertTrue(out['changed'])
        from server import db
        events = db.query('SELECT * FROM watch_events')
        self.assertEqual([e['kind'] for e in events], ['body'])

    def test_http_error_records_error_event(self) -> None:
        row = self._add(url='https://e.com/')
        client = mock.AsyncMock()
        client.get = mock.AsyncMock(return_value=_Resp('nope', 500))
        out = asyncio.run(pagewatch.check_page(row, client))
        self.assertFalse(out['ok'])
        from server import db
        self.assertEqual(db.query('SELECT kind FROM watch_events')[0]['kind'], 'error')


if __name__ == '__main__':
    unittest.main()
