"""上游日志定位（services/upstream_logs）与账号行探针的回归测试。

背景：「密钥 dsh 未显示消耗账号」的根因是**读错了文件**——上游把聊天请求行
（带账号）写 stdout，把任务行写 stderr，而两个采集器共用一个配置项。
这里锁定的不变量：
  1. 候选集里**内容命中**的文件优先于「配置里写的那个」；
  2. 都不命中时返回 None（旁路失败不抛异常）；
  3. 命中结果会缓存，但文件被删/清空时立即失效；
  4. `read_tail` 同时给出 mtime（原生行没有日期，要靠它还原 epoch）。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from server import config  # noqa: E402
from server.services import accountlog, upstream_logs  # noqa: E402

CHAT = ('| #097 | 19:31:36 | cn:deepseek-v4.1-flash     | stream | 200 | '
        '李知行@纷享销客(1f77b5e4) | TTFB=1819ms   | tok=1836   | 171.8tok/s  | '
        'total=12.5s | src=127.0.0.1 ua="deepseek-harness/0.2.1-rc.3" |')
TASK = '2026/10/26 10:00:09 travel L.(a23b6de8): claim ok record=29812479 reward=10'


class _Base(unittest.TestCase):
    def setUp(self):
        upstream_logs.forget()
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(upstream_logs.forget)

    def write(self, name: str, lines: list[str], age: float = 0.0) -> pathlib.Path:
        p = self.dir / name
        p.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        if age:
            stamp = time.time() - age
            __import__('os').utime(p, (stamp, stamp))
        return p


class CandidatesTest(_Base):
    def test_prefer_first_then_by_mtime(self):
        a = self.write('a.err.log', [TASK], age=100)
        b = self.write('b.out.log', [CHAT], age=10)
        self.write('ignored.txt', [CHAT])
        out = upstream_logs.candidates(a)
        self.assertEqual(out[0], a, '显式配置的路径排第一')
        self.assertEqual(out[1], b, '其余按 mtime 新→旧，且只收 *.log')
        self.assertNotIn('ignored.txt', [p.name for p in out])


class PickLogTest(_Base):
    def test_falls_back_to_file_that_actually_has_chat_lines(self):
        """配置指向任务日志时，不能就此认定「没有账号可回填」。"""
        task_log = self.write('gateway.err.log', [TASK] * 20, age=1)
        chat_log = self.write('gateway.out.log', [CHAT] * 5, age=5)
        with mock.patch.object(config, 'WB2API_LOG_FILE', task_log):
            picked = upstream_logs.pick_log('chat', accountlog._looks_like_chat,
                                            prefer=task_log)
        self.assertEqual(picked, chat_log)

    def test_prefers_configured_file_when_it_has_lines(self):
        chat_log = self.write('gateway.out.log', [CHAT] * 5, age=100)
        self.write('other.out.log', [CHAT] * 5, age=1)
        picked = upstream_logs.pick_log('chat', accountlog._looks_like_chat, prefer=chat_log)
        self.assertEqual(picked, chat_log, '配置对了就直接用它，不要被 mtime 带偏')

    def test_returns_none_when_nothing_matches(self):
        task_log = self.write('gateway.err.log', [TASK] * 20)
        with mock.patch('server.services.upstream_logs.logger') as log:
            picked = upstream_logs.pick_log('chat', accountlog._looks_like_chat,
                                            prefer=task_log)
            self.assertIsNone(picked)
            self.assertTrue(log.warning.called, '挑不到文件要留一条可见的告警')

    def test_caches_and_invalidates_on_empty_file(self):
        chat_log = self.write('gateway.out.log', [CHAT] * 3)
        real = upstream_logs._tail_lines
        with mock.patch.object(upstream_logs, '_tail_lines', side_effect=real) as probe:
            upstream_logs.pick_log('chat', accountlog._looks_like_chat, prefer=chat_log)
            first = probe.call_count
            upstream_logs.pick_log('chat', accountlog._looks_like_chat, prefer=chat_log)
            self.assertEqual(probe.call_count, first, 'TTL 内不该重复开文件')
            chat_log.write_text('', encoding='utf-8')
            upstream_logs.pick_log('chat', accountlog._looks_like_chat, prefer=chat_log)
            self.assertGreater(probe.call_count, first, '文件被清空后必须重新挑')

    def test_read_tail_returns_mtime(self):
        chat_log = self.write('gateway.out.log', [CHAT] * 3)
        lines, mtime = upstream_logs.read_tail(chat_log, 10)
        self.assertEqual(len(lines), 3)
        self.assertAlmostEqual(mtime, chat_log.stat().st_mtime, places=3)

    def test_read_tail_missing_file(self):
        self.assertEqual(upstream_logs.read_tail(self.dir / 'nope.log', 10), ([], None))


class ChatProbeTest(unittest.TestCase):
    def test_recognises_real_chat_line(self):
        self.assertTrue(accountlog._looks_like_chat(CHAT))

    def test_rejects_task_lines(self):
        self.assertFalse(accountlog._looks_like_chat(TASK))
        self.assertFalse(accountlog._looks_like_chat(''))

    def test_parses_account_from_chat_line(self):
        entries = accountlog.parse_request_lines_with_mtime([CHAT], 1793018142.0)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['account'], '李知行@纷享销客(1f77b5e4)')
        self.assertEqual(entries[0]['model'], 'cn:deepseek-v4.1-flash')


if __name__ == '__main__':
    unittest.main()
