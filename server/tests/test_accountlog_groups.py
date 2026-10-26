"""多上游分组下的账号回填：**每个分组只用自己的日志、只填自己的密钥**。

为什么必须单独立测试：这一步错了两边都看不出来。

  * 分组实例的聊天行写在各自的 `data/gateway.out.log` 里（实测 dsh 密钥绑在
    「流水账号」分组 → 行落在 `instance-liushui/data/gateway.out.log`）。只读默认
    上游日志的话，该密钥的「消耗账号」永远是空的。
  * 反过来更危险：两侧模型名常常一模一样（都是 `cn:deepseek-v4.1-flash`），
    若回填时不按密钥限定，分组实例的账号会被填到默认上游的请求行上 —— 界面上
    显示一个像模像样的名字，但它根本不是这一次的那一个。

所以这里钉住四条不变量：
  1. `_group_targets()` 为「默认上游」与「每个启用的分组」各出一条采集目标；
  2. 没有 `usage_file` 的分组（推不出 data/ 目录）跳过，不猜路径；
  3. `attach_request_accounts(key_ids=[7])` 只动 key_id=7 的行；
  4. `key_ids=[]`（分组没密钥）直接不回填，**不**退化成「不过滤」。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from server import config, db  # noqa: E402
from server.services import accountlog  # noqa: E402

MODEL = 'cn:deepseek-v4.1-flash'
TS = 1793018277


class _DbCase(unittest.TestCase):
    """每个测试一个临时库：分组/密钥是表数据，用真库会互相污染。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = pathlib.Path(tmp.name)

        self._patches = [
            mock.patch.object(config, 'DB_PATH', self.dir / 'manager.db'),
            mock.patch.object(config, 'DATA_DIR', self.dir),
        ]
        for patch in self._patches:
            patch.start()
            self.addCleanup(patch.stop)

        self._drop_conn()
        self.addCleanup(self._drop_conn)
        db.connect()

    @staticmethod
    def _drop_conn() -> None:
        # db 是模块级单例连接：换库时必须先断开，否则仍连在上一个测试的库上
        if db._conn is not None:  # noqa: SLF001
            try:
                db._conn.close()  # noqa: SLF001
            except Exception:  # noqa: BLE001
                pass
        db._conn = None  # noqa: SLF001

    def add_key(self, name: str, upstream_id: int | None, enabled: int = 1) -> int:
        db.execute(
            'INSERT INTO api_keys (name, key_hash, prefix, enabled, created_at, '
            'upstream_id) VALUES (?,?,?,?,?,?)',
            (name, f'hash-{name}', f'sk-{name}', enabled, TS, upstream_id),
        )
        return int(db.query_one('SELECT last_insert_rowid() AS id')['id'])

    def add_upstream(self, name: str, usage_file: str, enabled: int = 1) -> int:
        db.execute(
            'INSERT INTO upstreams (name, base_url, enabled, created_at, updated_at, '
            'usage_file) VALUES (?,?,?,?,?,?)',
            (name, f'http://127.0.0.1:7865', enabled, TS, TS, usage_file),
        )
        return int(db.query_one('SELECT last_insert_rowid() AS id')['id'])

    def add_request(self, key_id: int, ts: int = TS, account: str | None = None) -> int:
        db.execute(
            'INSERT INTO request_logs (ts, key_id, model, mapped_model, status, account) '
            'VALUES (?,?,?,?,?,?)',
            (ts, key_id, MODEL, MODEL, 200, account),
        )
        return int(db.query_one('SELECT last_insert_rowid() AS id')['id'])

    def account_of(self, row_id: int) -> str | None:
        return db.query_one('SELECT account FROM request_logs WHERE id = ?',
                            (row_id,))['account']


class GroupTargetsTest(_DbCase):
    def test_default_and_group_each_get_a_target(self) -> None:
        """默认上游 + 一个启用的分组 → 两条目标，各自带自己的密钥。"""
        default_key = self.add_key('manager', None)
        group_key = self.add_key('dsh', 1)
        usage = str(self.dir / 'instance-liushui' / 'data' / 'usage.json')
        self.add_upstream('流水账号', usage)

        targets = accountlog._group_targets()  # noqa: SLF001

        self.assertEqual(len(targets), 2, '默认上游与分组必须各采一次')
        by_group = {gid: (prefer, keys) for gid, prefer, keys in targets}
        self.assertEqual(by_group[None][1], [default_key])
        self.assertEqual(by_group[1][1], [group_key])
        # 分组日志目录由 usage_file 的父目录推导
        self.assertEqual(by_group[1][0], pathlib.Path(usage).parent / 'gateway.out.log')

    def test_disabled_key_or_group_is_skipped(self) -> None:
        self.add_key('disabled', 2, enabled=0)
        self.add_upstream('停用分组', str(self.dir / 'data' / 'usage.json'), enabled=0)
        self.assertEqual(accountlog._group_targets(), [],  # noqa: SLF001
                         '停用的密钥/分组不该产生采集目标')

    def test_group_without_usage_file_is_skipped(self) -> None:
        self.add_key('dsh', 3)
        self.add_upstream('无台账', '')
        self.assertEqual(accountlog._group_targets(), [],  # noqa: SLF001
                         '推不出 data/ 目录时跳过，不要猜路径')

    def test_default_upstream_only_when_it_has_keys(self) -> None:
        gid = self.add_upstream('某分组', str(self.dir / 'data' / 'usage.json'))
        self.add_key('dsh', gid)
        self.assertEqual([g for g, _, _ in accountlog._group_targets()], [gid],  # noqa: SLF001
                         '默认上游没有密钥就不必去读它的日志')


class ScopedFillTest(_DbCase):
    def test_key_scope_prevents_cross_group_fill(self) -> None:
        """**核心**：同模型、同一秒的两行，只能填自己那把密钥的。"""
        default_key = self.add_key('manager', None)
        group_key = self.add_key('dsh', 1)
        src = self.add_request(group_key)
        other = self.add_request(default_key)

        entries = [{'ts': TS, 'model': MODEL, 'account': '190****5260(c1bbbf42)'}]
        filled = db.attach_request_accounts(entries, key_ids=[group_key])

        self.assertEqual(filled, 1)
        self.assertEqual(self.account_of(src), '190****5260(c1bbbf42)')
        self.assertIsNone(self.account_of(other), '别的分组的请求行不能被填')

    def test_empty_key_scope_fills_nothing(self) -> None:
        key = self.add_key('manager', None)
        row = self.add_request(key)
        entries = [{'ts': TS, 'model': MODEL, 'account': 'x(12345678)'}]
        self.assertEqual(db.attach_request_accounts(entries, key_ids=[]), 0,
                         '空集合表示「这组没有密钥」，不能退化成不过滤')
        self.assertIsNone(self.account_of(row))

    def test_no_scope_keeps_legacy_behaviour(self) -> None:
        key = self.add_key('manager', None)
        row = self.add_request(key)
        entries = [{'ts': TS, 'model': MODEL, 'account': 'x(12345678)'}]
        self.assertEqual(db.attach_request_accounts(entries), 1)
        self.assertEqual(self.account_of(row), 'x(12345678)')


if __name__ == '__main__':
    unittest.main()
