"""登录与请求体加固的回归测试（2026-10-26，对齐上游 2.10.2 安全修复）。

覆盖三件事：
  1. 用户名不存在时也要付出与真实校验相同的计算成本（防用户名枚举）；
  2. 按用户名的锁定只拦「自己也失败过该用户名」的来源（防匿名锁死管理员）；
  3. `/api/*` 请求体上限在**读取层**生效，不能靠不声明 Content-Length 绕过。
  外加上游 2.11.0 的「CORS 配成 `*` 拒绝启动」。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi.testclient import TestClient  # noqa: E402

from server import security  # noqa: E402
from server.main import MAX_API_BODY_BYTES, app, check_cors_origins  # noqa: E402


def _reset_lock_state() -> None:
    security._fail.clear()
    security._user_fail.clear()
    security._user_ips.clear()


class DummyHashTest(unittest.TestCase):
    def setUp(self) -> None:
        _reset_lock_state()
        self.c = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        _reset_lock_state()

    def test_unknown_user_still_runs_password_hash(self) -> None:
        """用户名不存在时 verify_pwd 必须被调用且拿到假哈希，否则响应时间可枚举用户。"""
        with mock.patch.object(security, 'load_users', return_value={'users': []}), \
                mock.patch.object(security, 'verify_pwd',
                                  wraps=security.verify_pwd) as spy:
            r = self.c.post('/api/login', json={'username': 'ghost', 'password': 'Whatever123!'})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(spy.call_count, 1, '用户不存在时也必须走一次密码校验')
        self.assertEqual(spy.call_args[0][1], security.DUMMY_PWD_HASH)

    def test_dummy_hash_is_never_a_match(self) -> None:
        for pwd in ('', 'admin', 'Whatever123!', 'password', '12345678'):
            self.assertFalse(security.verify_pwd(pwd, security.DUMMY_PWD_HASH))


class UsernameLockScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        _reset_lock_state()

    def tearDown(self) -> None:
        _reset_lock_state()

    def test_other_ip_not_blocked_by_someone_elses_failures(self) -> None:
        """攻击者从 A 连试 admin 把用户名计数打满，不能把干净 IP B 一起锁死。"""
        for _ in range(security.MAX_FAILS):
            security.record_fail('10.0.0.1', 'admin')
        self.assertTrue(security.login_blocked('10.0.0.1', 'admin'))
        self.assertFalse(security.login_blocked('10.0.0.2', 'admin'),
                         '干净来源不该被别人的失败次数牵连')

    def test_own_failures_still_lock_that_ip(self) -> None:
        for _ in range(security.MAX_FAILS):
            security.record_fail('10.0.0.3', 'admin')
        self.assertTrue(security.login_blocked('10.0.0.3', 'admin'))

    def test_clear_fail_releases_username_scope(self) -> None:
        for _ in range(security.MAX_FAILS):
            security.record_fail('10.0.0.4', 'admin')
        security.clear_fail('10.0.0.4', 'admin')
        self.assertFalse(security.login_blocked('10.0.0.4', 'admin'))


class CorsStartupTest(unittest.TestCase):
    def test_wildcard_is_refused(self) -> None:
        with self.assertRaises(RuntimeError):
            check_cors_origins(['*'])
        with self.assertRaises(RuntimeError):
            check_cors_origins(['https://a.example', ' * '])

    def test_explicit_origins_pass(self) -> None:
        check_cors_origins(['http://127.0.0.1:3000', 'https://panel.example'])


class BodyLimitReadLayerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.c = TestClient(app, raise_server_exceptions=False)

    def _chunked(self, total: int, chunk: int = 1024 * 1024):
        sent = 0
        while sent < total:
            size = min(chunk, total - sent)
            sent += size
            yield b'x' * size

    def test_chunked_body_over_limit_is_413(self) -> None:
        """不带 Content-Length 的分块请求也要被拦 —— 只在声明长度上设限是绕得过的。"""
        total = MAX_API_BODY_BYTES + 1024 * 1024
        r = self.c.post('/api/login', content=self._chunked(total),
                        headers={'Content-Type': 'application/json'})
        self.assertEqual(r.status_code, 413)

    def test_declared_length_over_limit_is_413(self) -> None:
        r = self.c.post('/api/login', content=b'{}',
                        headers={'Content-Type': 'application/json',
                                 'Content-Length': str(MAX_API_BODY_BYTES + 1)})
        self.assertEqual(r.status_code, 413)

    def test_small_body_still_parsed(self) -> None:
        r = self.c.post('/api/login', json={'username': 'ghost', 'password': 'Whatever123!'})
        self.assertEqual(r.status_code, 401, '小请求体应正常走到业务逻辑')


if __name__ == '__main__':
    unittest.main()
