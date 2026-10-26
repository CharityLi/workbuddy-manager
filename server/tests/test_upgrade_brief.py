"""`server/services/upgradebrief.py` 的单测。

简报的输入有三块：上游 Releases（网络，打桩）、本地 tag 与 git 状态（子进程，
打桩）、定制清单（仓库里的 md 文件，真读）。这里分别固定住，验证组装逻辑。
"""

from __future__ import annotations

import unittest
from unittest import mock

from server import config
from server.services import upgradebrief as ub


def _release(tag: str, date: str, body: str) -> dict:
    return {'tag': tag, 'date': date, 'body': body, 'prerelease': False}


RELEASES = [
    _release('v2.11.0', '2026-10-26', '### 新增\n- **页面监控**：定时抓取网页比较结构变化。\n'),
    _release('v2.10.0', '2026-10-25', '### 修复\n- **列表要横拖才能看全**：改成固定列宽。\n'),
    _release('v2.9.0', '2026-10-20', '### 修复\n- 老版本的内容。\n'),
]


class TagFilterTest(unittest.TestCase):
    def test_backup_tags_are_not_versions(self) -> None:
        """`backup/pre-rebase-1025` 这种运维 tag 不是版本，不能当比对基准。"""
        with mock.patch.object(ub, 'local_tags',
                               return_value={'2.9.0', '2.8.0', 'backup/pre-rebase-1025-225526'}):
            self.assertEqual(ub._latest_local_tag(), '2.9.0')

    def test_empty_when_no_version_tags(self) -> None:
        with mock.patch.object(ub, 'local_tags', return_value={'backup/x'}):
            self.assertEqual(ub._latest_local_tag(), '')


class PointsTest(unittest.TestCase):
    def test_limits_count_and_truncates(self) -> None:
        version = ub.upstreamwatch.versions_from_releases([RELEASES[0]])[0]
        points = ub._points(version)
        self.assertEqual(len(points), 1)                 # 这版只有 1 条
        self.assertTrue(points[0].startswith('新增：'))   # 带小节名
        long_version = {'sections': [{'title': '修复', 'items': [
            {'text': 'x' * (ub.MAX_POINT_CHARS + 40)}]}]}
        out = ub._points(long_version)
        self.assertTrue(out[0].endswith('…'))
        self.assertLessEqual(len(out[0]), ub.MAX_POINT_CHARS + len('修复：') + 1)

    def test_stops_at_limit(self) -> None:
        sections = [{'title': '新增', 'items': [{'text': f'条目{i}'} for i in range(10)]}]
        self.assertEqual(len(ub._points({'sections': sections})), ub.MAX_POINTS_PER_VERSION)


class CustomChecklistTest(unittest.TestCase):
    def test_reads_second_level_headings(self) -> None:
        with mock.patch.object(config, 'ROOT', config.ROOT):
            items = ub.custom_checklist()
        self.assertTrue(items, 'docs/local-customizations.md 应该有二级标题')
        self.assertTrue(any('多上游' in x for x in items))
        self.assertNotIn('# 本地定制清单（升级核对表）', items)   # 一级标题不算

    def test_missing_file_gives_empty(self) -> None:
        with mock.patch('pathlib.Path.read_text', side_effect=OSError('nope')):
            self.assertEqual(ub.custom_checklist(), [])


class BriefTest(unittest.TestCase):
    def _brief(self, local_tags: set[str], local_version: str = 'v2.9.0',
               git_out: dict | None = None) -> dict:
        git_out = git_out or {}

        def fake_git(*args: str, timeout: float = 8.0) -> str:
            if args[:1] == ('tag',):
                return '\n'.join(sorted(local_tags))
            if args[:1] == ('log',):
                return 'abc1234 feat: 本地独有提交\n'
            if args[:1] == ('status',):
                return git_out.get('status', '')
            return ''

        with mock.patch.object(ub, 'local_tags', return_value=local_tags), \
                mock.patch.object(ub, '_git', side_effect=fake_git), \
                mock.patch.object(ub.upstreamwatch, 'cached_releases',
                                  return_value=(RELEASES, 1793000000)), \
                mock.patch.object(ub.upstreamwatch, 'local_version', return_value=local_version):
            return ub.brief()

    def test_missing_excludes_local_tags_and_current_version(self) -> None:
        b = self._brief({'2.9.0', '2.8.0'})
        self.assertEqual([m['version'] for m in b['missing']], ['2.11.0', '2.10.0'])
        self.assertEqual(b['missing_count'], 2)
        self.assertEqual(b['latest'], '2.11.0')
        self.assertEqual(b['base_tag'], '2.9.0')

    def test_up_to_date_when_local_has_everything(self) -> None:
        b = self._brief({'2.11.0', '2.10.0', '2.9.0'}, local_version='v2.11.0')
        self.assertEqual(b['missing'], [])
        self.assertIn('无需升级', b['markdown'])

    def test_markdown_sections(self) -> None:
        b = self._brief({'2.9.0'}, git_out={'status': ' M web/a.tsx\n?? docs/b.md\n'})
        md = b['markdown']
        self.assertIn('落后 2 个发布版本', md)
        self.assertIn('## 上游有、本地没有的版本', md)
        self.assertIn('## 本地独有（上游没有的提交，升级时不要覆盖）', md)
        self.assertIn('## 本地定制核对清单（升级后逐条验证）', md)
        self.assertIn('abc1234 feat: 本地独有提交', md)
        self.assertIn('未提交改动 2 个文件', md)
        self.assertEqual(b['dirty_count'], 2)

    def test_clean_worktree_and_no_commits(self) -> None:
        with mock.patch.object(ub, 'local_tags', return_value={'2.9.0'}), \
                mock.patch.object(ub, '_git', lambda *a, **k: ''), \
                mock.patch.object(ub.upstreamwatch, 'cached_releases',
                                  return_value=(RELEASES[:1], 1)), \
                mock.patch.object(ub.upstreamwatch, 'local_version', return_value='v2.9.0'):
            b = ub.brief()
        self.assertEqual(b['dirty_count'], 0)
        self.assertEqual(b['local_commits'], [])
        self.assertIn('工作区干净', b['markdown'])
        self.assertIn('无（本地没有超出最新 tag 的提交）', b['markdown'])

    def test_no_releases_still_returns_local_side(self) -> None:
        with mock.patch.object(ub, 'local_tags', return_value={'2.9.0'}), \
                mock.patch.object(ub, '_git', lambda *a, **k: ''), \
                mock.patch.object(ub.upstreamwatch, 'cached_releases', return_value=([], 0)), \
                mock.patch.object(ub.upstreamwatch, 'local_version', return_value='v2.9.0'):
            b = ub.brief()
        self.assertEqual(b['latest'], '')
        self.assertIn('没能取到上游发布列表', b['markdown'])


if __name__ == '__main__':
    unittest.main()
