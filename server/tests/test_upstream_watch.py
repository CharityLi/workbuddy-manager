"""上游仓库每日巡检（services/upstreamwatch）的回归测试。

锁定的不变量：
  1. **只记录不跟随**：全程没有任何写上游仓库的动作；`check()` 只调用远端只读
     查询与本地只读 git 查询（测试用 mock 拦住，断言没有调用写入类命令）。
  2. **不污染本地版本日志**：写入目标是 `CHANGELOG-UPSTREAM.md`，与
     `CHANGELOG.md` 必须是两个不同文件。
  3. **首次巡检不倾倒历史**：没有基线时只记远端 HEAD 一条；之后只记「比上次
     记录更新」的提交，重复巡检不重复写。
  4. 失败要留痕：网络/限流时报错进状态，不伪装成「没有更新」。
  5. 「本地有没有」用祖先判定（`merge-base --is-ancestor`），不是「对象存在」。
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from server import config, db  # noqa: E402
from server.services import upstreamwatch as uw  # noqa: E402


def _commit(sha: str, subject: str = 'x', date: str = '2026-10-21') -> dict:
    return {'sha': sha, 'short': sha[:8], 'date': date, 'subject': subject,
            'author': 'linguo2625469'}


class RepoSlugTest(unittest.TestCase):
    def test_parses_common_url_shapes(self):
        cases = {
            'https://github.com/linguo2625469/workbuddy2api-panel.git':
                'linguo2625469/workbuddy2api-panel',
            'https://github.com/linguo2625469/workbuddy2api-panel':
                'linguo2625469/workbuddy2api-panel',
            'git@github.com:linguo2625469/workbuddy2api-panel.git':
                'linguo2625469/workbuddy2api-panel',
            'git@github.com:linguo2625469/workbuddy2api-panel':
                'linguo2625469/workbuddy2api-panel',
            'linguo2625469/workbuddy2api-panel':
                'linguo2625469/workbuddy2api-panel',
        }
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(uw.parse_repo_slug(raw), want)

    def test_rejects_garbage(self):
        for raw in ('', '   ', 'not a url', 'https://example.com/x/y'):
            with self.subTest(raw=raw):
                self.assertEqual(uw.parse_repo_slug(raw), '')

    def test_env_override_wins(self):
        with mock.patch.dict('os.environ', {'WB_UPSTREAM_REPO': 'someone/fork'}, clear=False):
            with mock.patch.object(uw, '_git', return_value='https://github.com/a/b.git'):
                self.assertEqual(uw.repo_slug(), 'someone/fork')


class NextRunTest(unittest.TestCase):
    def test_before_hour_is_today(self):
        now = dt.datetime(2026, 10, 26, 9, 30)
        with mock.patch.object(uw, 'WATCH_HOUR', 10):
            self.assertEqual(uw.next_run(now), dt.datetime(2026, 10, 26, 10, 0))

    def test_after_hour_rolls_to_tomorrow(self):
        now = dt.datetime(2026, 10, 26, 10, 0, 1)
        with mock.patch.object(uw, 'WATCH_HOUR', 10):
            self.assertEqual(uw.next_run(now), dt.datetime(2026, 10, 27, 10, 0))

    def test_exactly_on_hour_rolls_forward(self):
        # 同一时刻不该返回自己（否则循环会在那一刻原地空转）
        now = dt.datetime(2026, 10, 26, 10, 0, 0)
        with mock.patch.object(uw, 'WATCH_HOUR', 10):
            self.assertEqual(uw.next_run(now), dt.datetime(2026, 10, 27, 10, 0))


class _Base(unittest.TestCase):
    """把 DB 与日志文件都指到临时目录（不碰真实 data/ 与仓库根）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = pathlib.Path(self._tmp.name)
        self._orig_db = config.DB_PATH
        config.DB_PATH = base / 'upstream-watch-test.db'
        self._orig_log = uw.LOG_PATH
        uw.LOG_PATH = base / 'CHANGELOG-UPSTREAM.md'
        db.query('SELECT 1')  # 建表/迁移是惰性的：先碰一下库
        # 每个用例都从干净状态开始
        db.set_setting(uw.STATE_KEY, {})

    def tearDown(self):
        config.DB_PATH = self._orig_db
        uw.LOG_PATH = self._orig_log
        self._tmp.cleanup()

    def setUpPatches(self, commits, local_history=(), local_head=None, slug='o/r'):
        patches = [
            mock.patch.object(uw, 'remote_commits', return_value=list(commits)),
            mock.patch.object(uw, 'in_local_history',
                              side_effect=lambda shas: {s for s in shas if s in set(local_history)}),
            mock.patch.object(uw, 'repo_slug', return_value=slug),
            mock.patch.object(uw, 'local_head',
                              return_value=local_head or {'sha': 'f' * 40, 'short': 'ffffffff',
                                                          'date': '2026-10-26',
                                                          'subject': 'local work'}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class AppendTest(_Base):
    def test_header_written_once_and_blocks_append(self):
        self.assertTrue(uw._append_block([_commit('a' * 40, 'first')], 'a' * 40))
        self.assertTrue(uw._append_block([_commit('b' * 40, 'second')], 'b' * 40))
        text = uw.LOG_PATH.read_text(encoding='utf-8')
        self.assertEqual(text.count('# 上游仓库更新日志'), 1)
        self.assertEqual(text.count('## [上游'), 2)
        self.assertIn('aaaaaaaa', text)
        self.assertIn('bbbbbbbb', text)

    def test_empty_batch_writes_nothing(self):
        self.assertFalse(uw._append_block([], 'a' * 40))
        self.assertFalse(uw.LOG_PATH.exists())

    def test_logged_shas_reads_back(self):
        uw._append_block([_commit('c' * 40, 'x')], 'c' * 40)
        self.assertIn('cccccccc', uw._logged_shas())

    def test_log_is_not_the_local_changelog(self):
        """这条是整个功能的分界线：本地版本日志与上游流水必须是两个文件。"""
        self.assertNotEqual(uw.LOG_PATH.name, 'CHANGELOG.md')
        self.assertEqual(uw.LOG_PATH.name, 'CHANGELOG-UPSTREAM.md')


class CheckTest(_Base):
    def test_first_run_records_only_head(self):
        commits = [_commit('a' * 40, 'newest'), _commit('b' * 40, 'older'),
                   _commit('c' * 40, 'oldest')]
        self.setUpPatches(commits)
        out = uw.check(force=True)
        self.assertTrue(out['ok'])
        self.assertTrue(out['baseline'])
        self.assertEqual(out['new'], 1, '首次巡检只记远端 HEAD，不能把历史倒进来')
        self.assertTrue(out['remote_newer'])
        text = uw.LOG_PATH.read_text(encoding='utf-8')
        self.assertIn('aaaaaaaa', text)
        self.assertNotIn('bbbbbbbb', text)

    def test_second_run_within_ttl_skips(self):
        self.setUpPatches([_commit('a' * 40, 'newest')])
        uw.check(force=True)
        out = uw.check()
        self.assertTrue(out['skipped'])
        self.assertEqual(out['new'], 0)

    def test_incremental_only_logs_newer_commits(self):
        base = [_commit('a' * 40, 'base')]
        self.setUpPatches(base)
        uw.check(force=True)
        # 上游又推了两条：d、e（e 最新）
        newer = [_commit('e' * 40, 'newest'), _commit('d' * 40, 'new'),
                 _commit('a' * 40, 'base')]
        with mock.patch.object(uw, 'remote_commits', return_value=newer):
            out = uw.check(force=True)
        self.assertEqual(out['new'], 2)
        text = uw.LOG_PATH.read_text(encoding='utf-8')
        self.assertIn('eeeeeeee', text)
        self.assertIn('dddddddd', text)
        self.assertEqual(text.count('aaaaaaaa'), 1, '基线那条不该重复写')

    def test_unchanged_upstream_writes_nothing(self):
        commits = [_commit('a' * 40, 'base')]
        self.setUpPatches(commits)
        uw.check(force=True)
        before = uw.LOG_PATH.read_text(encoding='utf-8')
        out = uw.check(force=True)
        self.assertEqual(out['new'], 0)
        self.assertEqual(uw.LOG_PATH.read_text(encoding='utf-8'), before)

    def test_commits_already_in_local_history_are_not_logged(self):
        commits = [_commit('a' * 40, 'x'), _commit('b' * 40, 'y')]
        self.setUpPatches(commits, local_history=['a' * 40, 'b' * 40])
        out = uw.check(force=True)
        self.assertEqual(out['new'], 0)
        self.assertFalse(uw.LOG_PATH.exists())
        self.assertFalse(out['remote_newer'], '远端 HEAD 已在本地历史里 ⇒ 不算落后')

    def test_failure_is_recorded_not_swallowed(self):
        with mock.patch.object(uw, 'remote_commits', side_effect=RuntimeError('限流')):
            with mock.patch.object(uw, 'repo_slug', return_value='o/r'):
                with mock.patch.object(uw, 'local_head', return_value={}):
                    with mock.patch.object(uw, 'remote_head_sha', return_value=''):
                        out = uw.check(force=True)
        self.assertFalse(out['ok'])
        self.assertIn('限流', out['error'])
        self.assertFalse(uw.LOG_PATH.exists())
        st = uw.state()
        self.assertIn('限流', st['error'])
        self.assertTrue(st['checked_at'])

    def test_state_carries_both_heads(self):
        self.setUpPatches([_commit('a' * 40, 'newest')])
        uw.check(force=True)
        st = uw.state()
        self.assertEqual(st['remote_sha'], 'a' * 40)
        self.assertEqual(st['local_sha'], 'f' * 40)
        self.assertEqual(st['repo'], 'o/r')

    def test_check_never_writes_to_upstream_repo(self):
        """只读保证：巡检期间不允许出现 git 写命令（fetch/pull/merge/checkout）。"""
        calls: list[list[str]] = []

        def fake_git(args, timeout=6):
            calls.append(list(args))
            return None

        self.setUpPatches([_commit('a' * 40, 'newest')])
        with mock.patch.object(uw, '_git', side_effect=fake_git):
            uw.check(force=True)
        forbidden = {'fetch', 'pull', 'merge', 'checkout', 'reset', 'clean', 'rebase'}
        for args in calls:
            self.assertFalse(forbidden & set(args), f'出现了写操作：{args}')


class LoadLogTest(_Base):
    def test_unavailable_without_file(self):
        out = uw.load_log()
        self.assertFalse(out['available'])
        self.assertEqual(out['versions'], [])
        self.assertTrue(out['never_follow'])
        self.assertEqual(out['watch_hour'], uw.WATCH_HOUR)

    def test_reports_versions_and_meta(self):
        self.setUpPatches([_commit('a' * 40, 'docs: 停止维护声明')])
        uw.check(force=True)
        out = uw.load_log()
        self.assertTrue(out['available'])
        self.assertEqual(out['repo'], 'o/r')
        self.assertEqual(out['remote_short'], 'aaaaaaaa')
        self.assertEqual(out['local_short'], 'ffffffff')
        self.assertTrue(out['versions'])
        self.assertTrue(out['versions'][0]['version'].startswith('上游'))
        self.assertEqual(out['versions'][0]['sections'][0]['title'], '上游提交')

    def test_parse_shares_format_with_local_changelog(self):
        """与 CHANGELOG.md 同一套解析：版本 → 小节 → 条目。"""
        uw._append_block([_commit('a' * 40, 'feat: 某功能')], 'a' * 40)
        v = uw.load_log()['versions'][0]
        self.assertTrue(v['date'])
        self.assertEqual(len(v['sections'][0]['items']), 1)
        self.assertIn('某功能', v['sections'][0]['items'][0]['text'])


class AppVersionTest(unittest.TestCase):
    def test_reads_app_version_from_go_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / 'cmd' / 'server').mkdir(parents=True)
            (root / 'cmd' / 'server' / 'main.go').write_text(
                'package main\n\nconst appVersion = "1.13.0-panel"\n', encoding='utf-8')
            with mock.patch.object(config, 'UPSTREAM_DIR', root):
                self.assertEqual(uw.app_version(), '1.13.0-panel')

    def test_missing_source_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(config, 'UPSTREAM_DIR', pathlib.Path(tmp)):
                self.assertEqual(uw.app_version(), '')


if __name__ == '__main__':
    unittest.main()
