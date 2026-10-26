"""每日任务的分组遍历：默认分组也要跑、单组异常不拖垮其余（issue #274）。

本地原先这套用例钉在「只跑默认上游 + 领奖任务提示覆盖范围」的旧实现上；
上游 v2.11.0 把 `run_task` 改成按 `_task_groups()` 遍历所有分组，能力是旧实现的
超集（默认上游排在首位，仍然是 id=None）。这里按合并后的实现重写，保留原意：
**每个分组都要被执行，且结果里要能分清是哪一组**。
"""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from server.routers import accounts as accounts_router
from server.services import dailyplan


def _groups() -> list[dict]:
    return [
        {'id': None, 'name': '默认上游', 'auth_dir': '/tmp/a', 'is_default': True},
        {'id': 2, 'name': '流水账号', 'auth_dir': '/tmp/b', 'is_default': False},
    ]


class MultiGroupTest(unittest.TestCase):
    def test_no_group_short_circuits(self) -> None:
        """没有任何带 auth_dir 的分组时不开跑，也不要假装成功。"""
        with mock.patch.object(dailyplan, '_task_groups', lambda: []):
            ok, msg = asyncio.run(dailyplan.run_task('checkin'))
        self.assertFalse(ok)
        self.assertIn('没有配置本地账号目录', msg)

    def test_one_group_failing_does_not_stop_the_rest(self) -> None:
        seen: list = []

        async def fake(upstream_id=None, user=None):
            seen.append(upstream_id)
            if upstream_id == 2:
                raise RuntimeError('boom')
            return {'total': 3, 'succeeded': 3, 'already': 0}

        with mock.patch.object(dailyplan, '_task_groups', _groups), \
             mock.patch.object(accounts_router, 'checkin_all', fake):
            ok, msg = asyncio.run(dailyplan.run_task('checkin'))
        self.assertFalse(ok, '有分组异常时整体应报失败')
        self.assertEqual(seen, [None, 2], '第二个分组出错也要先跑到它')
        self.assertIn('默认上游 3/3', msg)
        self.assertIn('boom', msg, '异常原因要带出来，否则排障无从下手')

    def test_failed_count_is_surfaced(self) -> None:
        """签到失败的号要计数（本地「N 个失败」文案）。"""

        async def fake(upstream_id=None, user=None):
            return {'total': 5, 'succeeded': 4, 'already': 0, 'failed': 1}

        with mock.patch.object(dailyplan, '_task_groups', _groups), \
             mock.patch.object(accounts_router, 'checkin_all', fake):
            ok, msg = asyncio.run(dailyplan.run_task('checkin'))
        self.assertFalse(ok)
        self.assertIn('8/10', msg, '两个分组各 5 个 → 合计 8/10')
        self.assertIn('2 个失败', msg)

    def test_claim_touches_every_group(self) -> None:
        calls: list = []

        async def fake_start(mode, target='ALL', auth_dir=None, group_name=''):
            calls.append((mode, target, str(auth_dir), group_name))
            return True, 'started'

        async def fake_wait(poll_seconds=1.0, timeout_seconds=None):
            return True

        with mock.patch.object(dailyplan, '_task_groups', _groups), \
             mock.patch.object(dailyplan.taskrun, 'start_async', fake_start), \
             mock.patch.object(dailyplan.taskrun, 'wait_idle', fake_wait):
            ok, msg = asyncio.run(dailyplan.run_task('claim'))
        self.assertTrue(ok)
        self.assertEqual(calls, [
            ('claim', 'ALL', '/tmp/a', '默认上游'),
            ('claim', 'ALL', '/tmp/b', '流水账号'),
        ])
        self.assertIn('流水账号：已完成', msg)

    def test_unknown_task_rejected(self) -> None:
        with self.assertRaises(ValueError):
            asyncio.run(dailyplan.run_task('nope'))


if __name__ == '__main__':
    unittest.main()
