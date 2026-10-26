"""分组实例的一键启用（services/upstream_service）：生成什么、幂等吗、会不会动别人的数据。

为什么要测：这条路径上「看起来成功」和「其实没起来」长得几乎一样 ——
`launchctl bootstrap` 成功但进程没起（端口被占）、config.json 写对了但 auths 链接
指到别处、plist 写了但 WorkingDirectory 不对（state 落到默认分组头上）。
这些都只在**下一次请求打过来**时才暴露。所以这里把生成的产物逐项钉住。

launchd 一律 mock：真的 bootstrap 会在跑测试的机器上多起一个上游进程。
"""
from __future__ import annotations

import json
import pathlib
import plistlib
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))

from server import config  # noqa: E402
from server.services import upstream_service as us  # noqa: E402

UPSTREAM = {
    'id': 1,
    'name': '流水账号',
    'base_url': 'http://127.0.0.1:7865',
    'api_key': 'sk-group',
    'auth_dir': '',           # 各测试自己填
    'usage_file': '',
    'is_default': False,
    'enabled': True,
}

TEMPLATE = {
    'api_key': 'sk-default',
    'listen': '0.0.0.0:7863',
    'auth_dir': './auths',
    'state_file': './data/state.json',
    'upstream': {'timeout_sec': 120},
}


class _Fs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)
        self.upstream_root = self.root / 'panel'
        self.upstream_root.mkdir()
        (self.upstream_root / 'wb2api').write_text('#!/bin/sh\n', encoding='utf-8')
        self.template = self.upstream_root / 'config.json'
        self.template.write_text(json.dumps(TEMPLATE), encoding='utf-8')
        self.auth_dir = self.root / 'auths-流水账号'
        self.auth_dir.mkdir()
        (self.auth_dir / 'a.json').write_text('{}', encoding='utf-8')
        self.agents = self.root / 'Library' / 'LaunchAgents'
        self.agents.mkdir(parents=True, exist_ok=True)

        patches = [
            mock.patch.object(config, 'UPSTREAM_DIR', self.upstream_root),
            mock.patch.object(config, 'UPSTREAM_CONFIG', self.template),
            mock.patch.object(us.Path, 'home', staticmethod(lambda: self.root)),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def upstream(self, **over) -> dict:
        data = dict(UPSTREAM)
        data['auth_dir'] = str(self.auth_dir)
        data.update(over)
        return data


class PureTest(unittest.TestCase):
    def test_port_of(self) -> None:
        self.assertEqual(us.port_of('http://127.0.0.1:7865'), 7865)
        self.assertEqual(us.port_of('http://10.0.0.2:7865/'), 7865)
        with self.assertRaises(us.ServiceError):
            us.port_of('http://127.0.0.1')
        with self.assertRaises(us.ServiceError):
            us.port_of('http://127.0.0.1:99999')

    def test_slug_from_existing_usage_file(self) -> None:
        """已有实例不能被改名：slug 从 usage_file 反推。"""
        up = dict(UPSTREAM, usage_file='/x/instance-liushui/data/usage.json')
        self.assertEqual(us.slug_for(up), 'liushui')
        self.assertEqual(us.instance_dir_for(up), pathlib.Path('/x/instance-liushui'))

    def test_slug_falls_back_to_id(self) -> None:
        up = dict(UPSTREAM, id=7, name='中文分组名', usage_file='')
        self.assertEqual(us.slug_for(up), 'g7')
        self.assertEqual(us.label_for(up), 'com.wb2api.gateway.g7')

    def test_build_config_only_touches_what_it_must(self) -> None:
        cfg = us.build_config(dict(UPSTREAM, auth_dir='/tmp/a', api_key='sk-group'),
                             TEMPLATE, port=7865, instance_dir=pathlib.Path('/x'))
        self.assertEqual(cfg['listen'], '0.0.0.0:7865')
        self.assertEqual(cfg['auth_dir'], '/tmp/a')
        self.assertEqual(cfg['state_file'], './data/state.json')
        self.assertEqual(cfg['api_key'], 'sk-group')
        # 模板里其余部署参数必须原样保留（猜出来的实例行为和默认分组不一致更难查）
        self.assertEqual(cfg['upstream'], {'timeout_sec': 120})

    def test_masked_api_key_is_not_written(self) -> None:
        """接口回的是脱敏值；写进 config 会让上游拿 `******` 去鉴权。"""
        cfg = us.build_config(dict(UPSTREAM, auth_dir='/tmp/a', api_key='******'),
                             {'api_key': 'sk-default'}, port=7865,
                             instance_dir=pathlib.Path('/x'))
        self.assertEqual(cfg['api_key'], 'sk-default')

    def test_build_config_requires_auth_dir(self) -> None:
        with self.assertRaises(us.ServiceError):
            us.build_config(dict(UPSTREAM, auth_dir=''), TEMPLATE, port=7865,
                            instance_dir=pathlib.Path('/x'))

    def test_plist_shape(self) -> None:
        up = dict(UPSTREAM, id=3)
        payload = us.build_plist(up, instance_dir=pathlib.Path('/x/instance-g3'))
        self.assertEqual(payload['Label'], 'com.wb2api.gateway.g3')
        self.assertEqual(payload['ProgramArguments'],
                         ['/x/instance-g3/wb2api', '-config', '/x/instance-g3/config.json'])
        self.assertEqual(payload['WorkingDirectory'], '/x/instance-g3')
        self.assertEqual(payload['StandardOutPath'], '/x/instance-g3/data/gateway.out.log')
        # 开机自启就靠这两项：RunAtLoad 拉起一次，KeepAlive 崩了也拉回
        self.assertTrue(payload['RunAtLoad'])
        self.assertTrue(payload['KeepAlive'])


class InstanceTest(_Fs):
    def test_ensure_instance_creates_links_and_config(self) -> None:
        up = self.upstream()
        result = us.ensure_instance(up)
        instance = self.upstream_root / 'instance-g1'
        self.assertEqual(result['instance_dir'], str(instance))
        self.assertEqual(result['port'], 7865)
        self.assertTrue((instance / 'config.json').exists())
        self.assertTrue((instance / 'data').is_dir())
        # 二进制用链接指回仓库那份：升级二进制时所有实例一起生效
        self.assertTrue((instance / 'wb2api').is_symlink())
        self.assertEqual((instance / 'wb2api').resolve(),
                         (self.upstream_root / 'wb2api').resolve())
        # 账号目录链接到分组自己的 auths
        self.assertTrue((instance / 'auths').is_symlink())
        self.assertEqual((instance / 'auths').resolve(), self.auth_dir.resolve())
        cfg = json.loads((instance / 'config.json').read_text(encoding='utf-8'))
        self.assertEqual(cfg['listen'], '0.0.0.0:7865')
        self.assertEqual(cfg['auth_dir'], str(self.auth_dir))

    def test_second_run_is_idempotent(self) -> None:
        up = self.upstream()
        us.ensure_instance(up)
        result = us.ensure_instance(up)
        joined = ' '.join(result['steps'])
        self.assertIn('skip config.json', joined, '配置一致时不该反复重写')

    def test_existing_real_directory_is_never_overwritten(self) -> None:
        """auths 位置要是真目录（不是链接），绝不能删——那是账号数据。"""
        up = self.upstream()
        instance = self.upstream_root / 'instance-g1'
        instance.mkdir()
        (instance / 'auths').mkdir()
        (instance / 'auths' / 'keep.json').write_text('{}', encoding='utf-8')
        us.ensure_instance(up)
        self.assertFalse((instance / 'auths').is_symlink())
        self.assertTrue((instance / 'auths' / 'keep.json').exists())

    def test_missing_auth_dir_is_reported(self) -> None:
        up = self.upstream(auth_dir=str(self.root / '不存在'))
        with self.assertRaises(us.ServiceError):
            us.ensure_instance(up)

    def test_hand_written_plist_counts_as_unchanged(self) -> None:
        """已存在的 plist 是手写的（缩进/键序不同）：语义相同就不能判成「变了」。"""
        up = self.upstream()
        path = self.agents / 'com.wb2api.gateway.g1.plist'
        payload = us.build_plist(up, instance_dir=us.instance_dir_for(up))
        # 故意用「键序相反、不排序」写出（模仿手工编辑过的文件）
        manual = dict(reversed(list(payload.items())))
        path.write_bytes(plistlib.dumps(manual, sort_keys=False))
        self.assertNotEqual(path.read_bytes(), plistlib.dumps(payload, sort_keys=True),
                            '前提：两份字节确实不同')
        _, changed = us.write_plist(up)
        self.assertFalse(changed, '语义相同不该触发重启')

    def test_write_plist_reports_change(self) -> None:
        up = self.upstream()
        path, changed = us.write_plist(up)
        self.assertTrue(changed)
        self.assertEqual(path, self.agents / 'com.wb2api.gateway.g1.plist')
        payload = plistlib.loads(path.read_bytes())
        self.assertEqual(payload['Label'], 'com.wb2api.gateway.g1')
        _, again = us.write_plist(up)
        self.assertFalse(again, '内容没变就不该重写 plist')


class StartTest(_Fs):
    def test_start_reports_pid_and_passes_through_steps(self) -> None:
        up = self.upstream()
        calls: list[list[str]] = []

        prints = {'n': 0}

        def fake_run(cmd, timeout=20):
            calls.append(cmd)
            if cmd[:2] == ['launchctl', 'print']:
                # 第一次问「是否已加载」→ 没加载；bootstrap 之后再问 → 有 pid
                prints['n'] += 1
                if prints['n'] > 1:
                    return 0, 'pid = 4321'
                return 1, 'not found'
            return 0, ''

        with mock.patch.object(us, '_run', side_effect=fake_run):
            result = us.start(up)

        self.assertTrue(result['ok'])
        self.assertEqual(result['pid'], 4321)
        self.assertEqual(result['port'], 7865)
        self.assertIn('bootstrap 完成', ' '.join(result['steps']))
        self.assertTrue(any(c[:2] == ['launchctl', 'bootstrap'] for c in calls),
                        '必须真的调用 bootstrap（kickstart -k 在这台机器上无效）')

    def test_running_instance_with_unchanged_plist_is_not_bounced(self) -> None:
        """已经在跑且配置没变：点「启动服务」不能把正在服务的实例重启掉。"""
        up = self.upstream()
        us.ensure_instance(up)
        us.write_plist(up)  # 先让 plist 与期望内容一致
        calls: list[list[str]] = []

        def fake_run(cmd, timeout=20):
            calls.append(cmd)
            if cmd[:2] == ['launchctl', 'print']:
                return 0, 'pid = 999'
            return 0, ''

        with mock.patch.object(us, '_run', side_effect=fake_run):
            result = us.start(up)

        self.assertEqual(result['pid'], 999)
        self.assertIn('跳过重启', ' '.join(result['steps']))
        self.assertFalse(any(c[:2] == ['launchctl', 'bootout'] for c in calls),
                         '配置没变时不该 bootout，否则等于无谓重启')
        self.assertFalse(any(c[:2] == ['launchctl', 'bootstrap'] for c in calls))

    def test_start_fails_loudly_when_process_does_not_appear(self) -> None:
        """bootstrap 成功但没进程 = 端口被占。必须报错，不能当成功。"""
        up = self.upstream()

        def fake_run(cmd, timeout=20):
            if cmd[:2] == ['launchctl', 'print']:
                return 1, 'not found'
            return 0, ''

        with mock.patch.object(us, '_run', side_effect=fake_run):
            with self.assertRaises(us.ServiceError) as ctx:
                us.start(up)
        self.assertIn('端口', str(ctx.exception))

    def test_occupied_port_is_reported_not_ignored(self) -> None:
        """端口被别人占着时必须报错：否则 bootstrap 会进 10 秒一次的崩溃循环。"""
        up = self.upstream()

        def fake_run(cmd, timeout=20):
            if cmd[:2] == ['launchctl', 'print']:
                return 1, 'not found'
            if cmd[0] == 'lsof':
                return 0, '4321'
            if cmd[0] == 'ps':
                return 0, '/usr/bin/somethingelse --listen 7865'
            return 0, ''

        with mock.patch.object(us, '_run', side_effect=fake_run):
            with self.assertRaises(us.ServiceError) as ctx:
                us.start(up)
        self.assertIn('4321', str(ctx.exception))

    def test_takeover_kills_own_orphan_then_bootstraps(self) -> None:
        """本分组目录起的孤儿进程：takeover 时 kill -TERM 后接管。"""
        up = self.upstream()
        instance = us.instance_dir_for(up)
        kills: list[list[str]] = []
        prints = {'n': 0}

        def fake_run(cmd, timeout=20):
            if cmd[:2] == ['launchctl', 'print']:
                prints['n'] += 1
                return (0, 'pid = 5555') if prints['n'] > 1 else (1, 'not found')
            if cmd[0] == 'lsof':
                return 0, '4321'
            if cmd[0] == 'ps':
                return 0, f'{instance}/wb2api -config {instance}/config.json'
            if cmd[:2] == ['kill', '-TERM']:
                kills.append(cmd)
            return 0, ''

        with mock.patch.object(us, '_run', side_effect=fake_run):
            result = us.start(up, takeover=True)

        self.assertEqual(result['pid'], 5555)
        self.assertEqual(kills, [['kill', '-TERM', '4321']])
        self.assertIn('接管', ' '.join(result['steps']))

    def test_default_upstream_is_rejected(self) -> None:
        up = self.upstream(is_default=True)
        with self.assertRaises(us.ServiceError):
            us.ensure_instance(up)


if __name__ == '__main__':
    unittest.main()
