"""上游仓库巡检（services/upstreamwatch）的回归测试。

「上游」= 本项目自己的发布仓库 `ithtelab/workbuddy-manager` 的 Releases。

锁定的不变量：
  1. **只读**：巡检只请求 GitHub 的 `/releases` 接口，不下载产物、不写仓库文件；
  2. **版本比较按版本号**，不按字符串（v2.10.2 > v2.9.0，v1.0.87 不被当成「比
     v2 大」）；
  3. 只在 `min_tag`（默认 v1.0.87）以上列出版本，更早的不进界面；
  4. 首次巡检只把最新版记成基线，之后才比「新」；重复巡检不重复报；
  5. 失败要留痕（状态里带错误），不能伪装成「没有新版本」；
  6. Release 正文复用本地更新日志的解析器（版本 → 小节 → 条目）。
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from server import config, db  # noqa: E402
from server.services import upstreamwatch as uw  # noqa: E402


def _rel(tag: str, body: str = '### 修复\n\n- 修好了某处', date: str = '2026-10-20') -> dict:
    return {'tag_name': tag, 'name': tag, 'published_at': f'{date}T10:00:00Z',
            'body': body, 'draft': False, 'prerelease': False}


class VersionCompareTest(unittest.TestCase):
    def test_tag_tuple(self):
        self.assertEqual(uw._tag_tuple('v2.10.2'), (2, 10, 2))
        self.assertEqual(uw._tag_tuple('1.0.87'), (1, 0, 87))
        self.assertEqual(uw._tag_tuple(''), ())

    def test_newer_is_numeric_not_lexical(self):
        self.assertTrue(uw._newer('v2.10.2', 'v2.9.0'))
        self.assertTrue(uw._newer('v1.0.87', 'v1.0.86'))
        self.assertFalse(uw._newer('v1.0.87', 'v2.10.2'))
        self.assertFalse(uw._newer('v2.10.2', 'v2.10.2'))

    def test_repo_and_min_tag_env_override(self):
        with mock.patch.dict('os.environ', {'WB_UPSTREAM_REPO': 'a/b',
                                            'WB_UPSTREAM_MIN_TAG': 'v1.2.3'}, clear=False):
            self.assertEqual(uw.repo(), 'a/b')
            self.assertEqual(uw.min_tag(), 'v1.2.3')
        with mock.patch.dict('os.environ', {}, clear=True):
            self.assertEqual(uw.repo(), uw.DEFAULT_REPO)
            self.assertEqual(uw.min_tag(), uw.DEFAULT_MIN_TAG)


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.calls: list[str] = []

    def _patch(self, pages: dict[int, list[dict]]):
        def fake_get(path: str):
            self.calls.append(path)
            page = int(path.rsplit('page=', 1)[-1])
            return pages.get(page, [])

        p = mock.patch.object(uw, '_get', side_effect=fake_get)
        p.start()
        self.addCleanup(p.stop)

    def test_stops_at_min_tag(self):
        self._patch({1: [_rel('v2.10.2'), _rel('v1.0.87'), _rel('v1.0.86')]})
        out = uw.fetch_releases()
        self.assertEqual([r['tag'] for r in out], ['v2.10.2', 'v1.0.87'])

    def test_only_releases_endpoint_touched(self):
        """只读保证：巡检唯一碰的网络路径就是 releases 列表。"""
        self._patch({1: [_rel('v2.10.2')]})
        uw.fetch_releases()
        self.assertTrue(self.calls)
        for path in self.calls:
            self.assertTrue(path.startswith('/releases?'), path)

    def test_paginates_until_floor(self):
        page1 = [_rel(f'v2.{i}.0') for i in range(100, 0, -1)]
        self._patch({1: page1, 2: [_rel('v1.5.0'), _rel('v1.0.80')]})
        out = uw.fetch_releases()
        self.assertEqual(out[-1]['tag'], 'v1.5.0')
        self.assertEqual(len(self.calls), 2, '到 v1.0.80 就该停，不该翻第三页')

    def test_sorted_desc(self):
        self._patch({1: [_rel('v1.0.90'), _rel('v2.10.2'), _rel('v2.9.0')]})
        out = uw.fetch_releases()
        self.assertEqual([r['tag'] for r in out], ['v2.10.2', 'v2.9.0', 'v1.0.90'])

    def test_drafts_are_skipped(self):
        draft = _rel('v2.11.0')
        draft['draft'] = True
        self._patch({1: [draft, _rel('v2.10.2')]})
        self.assertEqual([r['tag'] for r in uw.fetch_releases()], ['v2.10.2'])


class _Base(unittest.TestCase):
    """把 DB 指到临时目录（不碰真实 data/）。"""

    def setUp(self):
        self._tmp = __import__('tempfile').TemporaryDirectory()
        self._orig_db = config.DB_PATH
        config.DB_PATH = pathlib.Path(self._tmp.name) / 'upstream-watch-test.db'
        db.query('SELECT 1')  # 建表/迁移是惰性的：先碰一下库
        db.set_setting(uw.STATE_KEY, {})
        db.set_setting(uw.CACHE_KEY, {})

    def tearDown(self):
        config.DB_PATH = self._orig_db
        self._tmp.cleanup()


class CacheTest(_Base):
    def test_uses_cache_when_fresh(self):
        with mock.patch.object(uw, 'fetch_releases', return_value=[{'tag': 'v2.10.2',
                                                                   'name': 'v2.10.2',
                                                                   'date': '2026-10-26',
                                                                   'body': ''}]) as f:
            uw.cached_releases(force=True)
            self.assertEqual(f.call_count, 1)
            releases, _ = uw.cached_releases()
            self.assertEqual(f.call_count, 1, '缓存新鲜时不该再联网')
            self.assertEqual(releases[0]['tag'], 'v2.10.2')

    def test_force_refetches(self):
        with mock.patch.object(uw, 'fetch_releases', return_value=[]) as f:
            uw.cached_releases(force=True)
            uw.cached_releases(force=True)
            self.assertEqual(f.call_count, 2)

    def test_repo_change_invalidates_cache(self):
        with mock.patch.object(uw, 'fetch_releases', return_value=[]) as f:
            uw.cached_releases(force=True)
            with mock.patch.dict('os.environ', {'WB_UPSTREAM_REPO': 'other/repo'}, clear=False):
                uw.cached_releases()
            self.assertEqual(f.call_count, 2, '换了仓库还拿旧缓存会显示错版本')


class CheckTest(_Base):
    def test_first_run_sets_baseline_only(self):
        rels = [{'tag': 'v2.10.2', 'name': 'v2.10.2', 'date': '2026-10-26', 'body': ''},
                {'tag': 'v2.10.1', 'name': 'v2.10.1', 'date': '2026-10-26', 'body': ''}]
        with mock.patch.object(uw, 'fetch_releases', return_value=rels):
            out = uw.check(force=True)
        self.assertTrue(out['ok'])
        self.assertTrue(out['baseline'])
        self.assertEqual(out['new_tags'], ['v2.10.2'], '首次只记最新一版')
        self.assertEqual(uw.state()['latest_tag'], 'v2.10.2')

    def test_new_release_detected(self):
        with mock.patch.object(uw, 'fetch_releases',
                               return_value=[{'tag': 'v2.10.2', 'name': 'v2.10.2',
                                              'date': '2026-10-26', 'body': ''}]):
            uw.check(force=True)
        rels = [{'tag': 'v2.11.0', 'name': 'v2.11.0', 'date': '2026-10-27', 'body': ''},
                {'tag': 'v2.10.2', 'name': 'v2.10.2', 'date': '2026-10-26', 'body': ''}]
        with mock.patch.object(uw, 'fetch_releases', return_value=rels):
            out = uw.check(force=True)
        self.assertFalse(out['baseline'])
        self.assertEqual(out['new_tags'], ['v2.11.0'])
        self.assertEqual(uw.state()['latest_tag'], 'v2.11.0')

    def test_no_change_reports_nothing(self):
        rels = [{'tag': 'v2.10.2', 'name': 'v2.10.2', 'date': '2026-10-26', 'body': ''}]
        with mock.patch.object(uw, 'fetch_releases', return_value=rels):
            uw.check(force=True)
            out = uw.check(force=True)
        self.assertEqual(out['new_tags'], [])

    def test_ttl_skips_without_network(self):
        with mock.patch.object(uw, 'fetch_releases',
                               return_value=[{'tag': 'v2.10.2', 'name': 'v2.10.2',
                                              'date': '2026-10-26', 'body': ''}]):
            uw.check(force=True)
        with mock.patch.object(uw, 'fetch_releases') as f:
            out = uw.check()
            self.assertTrue(out['skipped'])
            self.assertEqual(f.call_count, 0)

    def test_failure_is_recorded(self):
        with mock.patch.object(uw, 'fetch_releases', side_effect=RuntimeError('限流')):
            out = uw.check(force=True)
        self.assertFalse(out['ok'])
        self.assertIn('限流', out['error'])
        self.assertIn('限流', uw.state()['error'])
        self.assertTrue(uw.state()['checked_at'])


class VersionsTest(unittest.TestCase):
    def test_body_becomes_sections(self):
        rels = [{'tag': 'v2.10.2', 'name': 'v2.10.2', 'date': '2026-10-26',
                 'body': '### 修复\n\n- 修好了甲\n- 修好了乙\n\n### 新增\n\n- 加了个功能'}]
        v = uw.versions_from_releases(rels)[0]
        self.assertEqual(v['version'], '2.10.2')
        self.assertEqual(v['date'], '2026-10-26')
        self.assertFalse(v['unreleased'])
        titles = [s['title'] for s in v['sections']]
        self.assertEqual(titles, ['修复', '新增'])
        self.assertEqual(len(v['sections'][0]['items']), 2)
        self.assertIn('修好了甲', v['sections'][0]['items'][0]['text'])

    def test_empty_body_yields_empty_sections(self):
        v = uw.versions_from_releases([{'tag': 'v1.0.87', 'name': 'v1.0.87',
                                        'date': '2026-10-14', 'body': ''}])[0]
        self.assertEqual(v['version'], '1.0.87')
        self.assertEqual(v['sections'], [])


class LoadLogTest(_Base):
    def test_reports_meta_and_versions(self):
        rels = [{'tag': 'v2.10.2', 'name': 'v2.10.2', 'date': '2026-10-26',
                 'body': '### 修复\n\n- 某处'}]
        with mock.patch.object(uw, 'fetch_releases', return_value=rels):
            uw.check(force=True)
            out = uw.load_log()
        self.assertTrue(out['available'])
        self.assertEqual(out['repo'], uw.DEFAULT_REPO)
        self.assertEqual(out['latest'], 'v2.10.2')
        self.assertTrue(out['never_follow'])
        self.assertEqual(out['versions'][0]['version'], '2.10.2')
        self.assertIn('behind', out, '要能告诉界面本机落后于上游')

    def test_local_version_read_from_dot_version(self):
        with mock.patch.object(config, 'ROOT', pathlib.Path('/nonexistent-root')):
            self.assertEqual(uw.local_version(), '')

    def test_network_failure_surfaces_error(self):
        with mock.patch.object(uw, 'fetch_releases', side_effect=RuntimeError('offline')):
            out = uw.load_log()
        self.assertFalse(out['available'])
        self.assertIn('offline', out['error'])


class ScheduleTest(unittest.TestCase):
    def test_next_run_before_and_after_hour(self):
        with mock.patch.object(uw, 'WATCH_HOUR', 10):
            early = dt.datetime(2026, 10, 26, 9, 30)
            late = dt.datetime(2026, 10, 26, 10, 0, 1)
            self.assertEqual(uw.next_run(early), dt.datetime(2026, 10, 26, 10, 0))
            self.assertEqual(uw.next_run(late), dt.datetime(2026, 10, 27, 10, 0))


if __name__ == '__main__':
    unittest.main()
