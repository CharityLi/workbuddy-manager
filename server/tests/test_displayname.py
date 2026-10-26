"""账号显示名（备注优先、昵称兜底）的回归测试。

规则本身一句话，但错法都不报错、只是界面上的名字不对：

- 备注覆盖了昵称却**没留原名** ⇒ 账号页（唯一以昵称为主的地方）再也显示不出真昵称；
- 反过来没覆盖 ⇒ 用户刚起的备注在仪表盘 / 签到记录 / 代理绑定里看不到；
- 上游日志那批行只有 uid 前 8 位、昵称靠前缀解析，**先覆盖后解析**会把前缀当昵称。

所以这里把「覆盖 + 留原名 + 兜底顺序」逐条钉住。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from server.services import displayname  # noqa: E402

UID = 'aaaaaaaa-1111-2222-3333-444444444444'


class DisplayNameCase(unittest.TestCase):
    """把备注表打成桩，**不碰 sqlite 连接**。

    早先这组用例开临时库验证「真的能读到备注」，结果踩了两个坑：db 把连接缓存在
    模块级 `_conn` 里，换库要手工把它清掉，清完又会影响后面的用例
    （`test_docker_deploy` 的 connect 报错用例假定 `_conn is None`，直接报
    「RuntimeError not raised」）。备注的存取本身有 `test_account_notes` 覆盖，
    这里只验证「显示名怎么算」，所以只把 `db.account_notes` 打桩。
    """

    def setUp(self) -> None:
        self.notes: dict[str, str] = {}
        self._patch = mock.patch.object(displayname.db, 'account_notes',
                                        lambda: dict(self.notes))
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()


class DisplayNamePureTest(unittest.TestCase):
    """纯函数：不碰库，备注直接以 mapping 传入。"""

    def test_note_wins_over_nickname(self) -> None:
        self.assertEqual(
            displayname.display_name(UID, '上游昵称', {UID: '主号'}), '主号')

    def test_falls_back_to_nickname(self) -> None:
        self.assertEqual(displayname.display_name(UID, '上游昵称', {}), '上游昵称')

    def test_falls_back_to_uid_prefix(self) -> None:
        self.assertEqual(displayname.display_name(UID, '', {}), UID[:8])

    def test_nickname_is_trimmed(self) -> None:
        # 上游昵称可能带首尾空格；空备注时空串必须落到兜底，而不是显示一片空白
        self.assertEqual(displayname.display_name(UID, '   ', {}), UID[:8])


class ApplyTest(DisplayNameCase):
    def test_overwrites_name_and_keeps_raw(self) -> None:
        self.notes[UID] = '主号'
        rows = [{'uid': UID, 'nickname': '上游昵称'}]
        displayname.apply(rows)
        self.assertEqual(rows[0]['nickname'], '主号')
        self.assertEqual(rows[0]['nickname_raw'], '上游昵称')
        self.assertEqual(rows[0]['note'], '主号')

    def test_empty_note_keeps_nickname(self) -> None:
        rows = [{'uid': UID, 'nickname': '上游昵称'}]
        displayname.apply(rows)
        self.assertEqual(rows[0]['nickname'], '上游昵称')
        self.assertEqual(rows[0]['note'], '')

    def test_notes_map_is_reused_across_rows(self) -> None:
        rows = [{'uid': 'u1', 'nickname': 'n1'}, {'uid': 'u2', 'nickname': 'n2'}]
        displayname.apply(rows, notes_map={'u1': '备注一'})
        self.assertEqual([r['nickname'] for r in rows], ['备注一', 'n2'])

    def test_missing_nickname_keeps_empty_by_default(self) -> None:
        # 日志类记录不兜底 uid 片段：认不出是谁就别编个名字出来
        rows = [{'uid': UID}]
        displayname.apply(rows)
        self.assertEqual(rows[0]['nickname'], '')

    def test_accounts_list_may_fall_back_to_uid_prefix(self) -> None:
        rows = [{'uid': UID}]
        displayname.apply(rows, fallback_uid=True)
        self.assertEqual(rows[0]['nickname'], UID[:8])

    def test_custom_name_key(self) -> None:
        rows = [{'uid': UID, 'display_name': '旧名'}]
        displayname.apply(rows, name_key='display_name', notes_map={UID: '备注'})
        self.assertEqual(rows[0]['display_name'], '备注')
        self.assertEqual(rows[0]['nickname_raw'], '旧名')

    def test_empty_rows_is_noop(self) -> None:
        displayname.apply([])  # 不炸即可

    def test_note_map_reads_db(self) -> None:
        self.notes[UID] = '库里的备注'
        self.assertEqual(displayname.notes().get(UID), '库里的备注')


class QuestionOrderTest(unittest.TestCase):
    """签到 / 任务记录：先解析昵称、再套备注。顺序反了就是这条能抓到。"""

    def test_resolve_then_apply(self) -> None:
        row = {'uid': 'c1bbbf42', 'nickname': ''}
        # 模拟 _account_resolver 里 resolve 的解析结果
        row['nickname'] = '190****5260'
        displayname.apply([row], notes_map={'c1bbbf42': '流水号「3」'})
        self.assertEqual(row['nickname'], '流水号「3」')
        self.assertEqual(row['nickname_raw'], '190****5260')


if __name__ == '__main__':
    unittest.main()
