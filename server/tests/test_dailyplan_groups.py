"""每日任务要覆盖所有分组，而不是只跑默认上游。"""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from server.services import dailyplan


class MultiGroupTest(unittest.TestCase):
    def test_runs_every_group(self) -> None:
        calls: list[int | None] = []

        async def fake_run_one(task, upstream_id):
            calls.append(upstream_id)
            return True, f'{task} ok'

        ups = [{'id': None, 'name': '默认上游'}, {'id': 2, 'name': '流水账号'}]
        with mock.patch.object(dailyplan.upstreamsvc, 'list_upstreams', return_value=ups), \
             mock.patch.object(dailyplan, '_run_one', new=fake_run_one):
            ok, msg = asyncio.run(dailyplan.run_task('checkin'))
        self.assertTrue(ok)
        # 默认上游（None）也要跑，且顺序与 list_upstreams 一致
        self.assertEqual(calls, [None, 2])
        self.assertIn('默认上游', msg)
        self.assertIn('流水账号', msg)

    def test_one_group_failing_marks_whole_run_failed(self) -> None:
        async def fake_run_one(task, upstream_id):
            return (upstream_id is None), ('ok' if upstream_id is None else '2 个失败')

        ups = [{'id': None, 'name': '默认上游'}, {'id': 2, 'name': '流水账号'}]
        with mock.patch.object(dailyplan.upstreamsvc, 'list_upstreams', return_value=ups), \
             mock.patch.object(dailyplan, '_run_one', new=fake_run_one):
            ok, msg = asyncio.run(dailyplan.run_task('checkin'))
        self.assertFalse(ok)
        self.assertIn('流水账号: 2 个失败', msg)

    def test_exception_in_one_group_does_not_stop_others(self) -> None:
        async def fake_run_one(task, upstream_id):
            if upstream_id == 2:
                raise RuntimeError('boom')
            return True, 'ok'

        ups = [{'id': None, 'name': '默认上游'}, {'id': 2, 'name': '流水账号'}]
        with mock.patch.object(dailyplan.upstreamsvc, 'list_upstreams', return_value=ups), \
             mock.patch.object(dailyplan, '_run_one', new=fake_run_one):
            ok, msg = asyncio.run(dailyplan.run_task('checkin'))
        self.assertFalse(ok)
        self.assertIn('boom', msg)

    def test_claim_notes_default_only_when_multiple_groups(self) -> None:
        async def fake_run_one(task, upstream_id):
            return True, 'claim ok'

        ups = [{'id': None, 'name': '默认上游'}, {'id': 2, 'name': '流水账号'}]
        with mock.patch.object(dailyplan.upstreamsvc, 'list_upstreams', return_value=ups), \
             mock.patch.object(dailyplan, '_run_one', new=fake_run_one):
            _ok, msg = asyncio.run(dailyplan.run_task('claim'))
        self.assertIn('只覆盖默认上游', msg)

    def test_unknown_task_rejected(self) -> None:
        with self.assertRaises(ValueError):
            asyncio.run(dailyplan.run_task('nope'))


if __name__ == '__main__':
    unittest.main()
