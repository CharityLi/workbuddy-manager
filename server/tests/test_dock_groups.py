"""底栏（浮动 Dock）的分组语义（前端逻辑，借 Node 执行）+ 渲染层的结构不变式。

为什么值得有：这一批改的是**信息架构**，做错了不会报错，只是底栏看起来不对——

  · 分组切错（同名分组跨距离被合并 / 同组散落被切开）→ 组名与组内内容对不上；
  · 分隔线不带组名 → 用户只能猜那条竖线分开的是什么（这正是这一批要修的）；
  · 手机端不画分组 → 11 项平铺，分组语义在手机上完全消失（原来就是靠一句
    `if (item.title === 'divider') return null` 硬跳过的）；
  · 引导气泡没有关闭入口、也不响应「点别处」→ 它会一直挂在底栏正上方，而底栏
    可以被拖到页面中部，于是正好压住正文（用户反馈「反复出现在页面中部」）；
  · 切到「固定底部」后拖动没真的关掉 → 先跟手走一段、松手才归位，看起来像拖动坏了；
  · 引导里留一条当前模式下**做不到**的话（固定模式下还说「长按可拖动」）→
    用户会反复长按然后怀疑是自己手法不对。

第一条由 `web/lib/dock-groups.test.mjs` 的行为测试盖住（跑的是真实实现，不是复制
一份逻辑）；其余几条只能在**源码形状**上断言 —— 行为测试证明不了「渲染层真的用了
那个分组结果」，而这几条恰恰都是「算了却没用在分支上」型的错。

为什么用 Python 包一层：本仓库的测试套件是 Python 的（`pytest server/tests/`），
而这段逻辑在前端。没有 Node（或版本太旧）时**跳过**而不是失败。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / 'web' / 'lib' / 'dock-groups.test.mjs'
_PURE = _ROOT / 'web' / 'lib' / 'dock-groups.ts'
_DOCK_GONE = _ROOT / 'web' / 'components' / 'ui' / 'floating-dock.tsx'  # 2026-10-26 已删（死代码）
_NAV = _ROOT / 'web' / 'components' / 'dock' / 'dock-nav.tsx'
_BAR = _ROOT / 'web' / 'components' / 'common' / 'layout' / 'ManagementBar.tsx'
_LOCALES = _ROOT / 'web' / 'lib' / 'i18n' / 'locales'
_NODE = shutil.which('node')

# 只跑 .mjs，不碰 .ts —— Node 的 type stripping 从 22.6 起才有
_MIN_MAJOR = 22


def _node_major() -> int | None:
    if not _NODE:
        return None
    try:
        out = subprocess.run([_NODE, '--version'], capture_output=True,
                             text=True, timeout=15).stdout.strip()
        return int(out.lstrip('v').split('.')[0])
    except Exception:  # noqa: BLE001
        return None


_MAJOR = _node_major()


def _code(path: Path) -> str:
    """读出源码并**剥掉注释**（实现里正解释着「不要这么写」，搜全文会误判）。"""
    src = path.read_text(encoding='utf-8')
    src = re.sub(r'/\*.*?\*/', '', src, flags=re.S)
    src = re.sub(r'\{/\*.*?\*/\}', '', src, flags=re.S)
    return re.sub(r'//[^\n]*', '', src)


def _dock_items_block(code: str) -> str:
    """切出 `const dockItems = [ ... ]` 这一段，供「每一项都带分组键」这类断言用。

    不能直接搜全文数 `groupKey:`：那个词在注释与别处也会出现，数出来的数对不上
    条目数时，报错信息会指向一个根本不存在的问题。
    """
    start = code.index('const dockItems = [')
    end = code.index('\n  ];', start)
    return code[start:end]


class DockGroupsBehaviourTest(unittest.TestCase):
    """跑 Node 侧的行为测试：分组切分（`splitByGroup`）的真实实现。"""

    @unittest.skipUnless(_NODE, '未安装 node，跳过前端逻辑测试')
    @unittest.skipUnless(_MAJOR is not None and _MAJOR >= _MIN_MAJOR,
                         f'需要 node ≥ {_MIN_MAJOR}（type stripping），当前 {_MAJOR}')
    def test_split_by_group(self) -> None:
        self.assertTrue(_SCRIPT.is_file(), f'缺少测试脚本: {_SCRIPT}')

        def run_once() -> subprocess.CompletedProcess:
            return subprocess.run(
                [_NODE, '--experimental-strip-types', str(_SCRIPT)],
                capture_output=True, text=True, timeout=120, cwd=str(_ROOT),
            )

        proc = run_once()
        # Node 偶发进程级崩溃（整套测试并跑时遇到过 0xC0000409）：不是断言失败，也与
        # 被测逻辑无关，却会把整批测试染红。只对异常退出码重试一次；rc=1（脚本自己
        # 判失败）不重试，免得这条守卫变成「跑两遍总能绿」的空转。
        # （与 `test_account_list.py` / `test_account_status.py` 同一处理。）
        if proc.returncode not in (0, 1):
            proc = run_once()
        out = (proc.stdout or '') + (proc.stderr or '')
        self.assertEqual(proc.returncode, 0, f'底栏分组逻辑不符合预期：\n{out}')
        self.assertIn('all passed', out, f'脚本没有跑到通过：\n{out}')


class DockGroupsInvariantTest(unittest.TestCase):
    """行为测试盖不到的前提与结构不变式。"""

    def test_module_stays_runnable_without_node_modules(self) -> None:
        """`dock-groups.ts` 不得出现**运行时** import。

        这是上面那条 Node 测试能跑起来的前提：`.mjs` 直接 `import './dock-groups.ts'`，
        而 Node 的 ESM 解析**要求带扩展名**，仓库的 app 代码却一律不写扩展名。
        一旦本模块 import 了别的模块的**值**，解析就会失败——症状是「Cannot find
        module」，看起来像路径写错。
        """
        offenders = [
            line.strip() for line in _code(_PURE).splitlines()
            if line.strip().startswith('import ')
            and not line.strip().startswith('import type')
        ]
        self.assertEqual(
            offenders, [],
            'dock-groups.ts 出现了运行时 import：\n  ' + '\n  '.join(offenders)
            + '\n本模块被 web/lib/dock-groups.test.mjs 直接 import，引入运行时 import '
              '会让那条测试以「找不到模块」失败。',
        )

    def test_divider_sentinel_is_gone(self) -> None:
        """哨兵条目 `{title: 'divider'}` 必须彻底消失，且旧桌面底栏不能回来。

        这是这一批的**根因**：分组曾经靠一个混在 `items` 里的假条目表达，
        于是每个渲染分支都得记得跳过它——移动端就是靠
        `if (item.title === 'divider') return null` 硬跳过的，代价是手机上
        **完全没有分组语义**。

        2026-10-26 起底栏只剩 `components/dock/dock-nav.tsx` 一份实现（桌面/移动
        共用，分隔线挂在条目的 `dividerBefore` 上），旧的
        `components/ui/floating-dock.tsx` 整块删除 —— 那条 `false &&` 死分支
        留着只会让人以为还存在两套渲染。所以这里改成双保险：
        哨兵不许回来，**旧文件也不许回来**。
        """
        bar = _code(_BAR)
        self.assertNotIn(
            "'divider'", bar,
            'ManagementBar.tsx 里又出现了 divider 哨兵条目——分组语义应当挂在条目的 '
            'groupKey 上，不要再往 items 里塞假条目。',
        )
        self.assertNotIn(
            'floating-dock', bar,
            'ManagementBar.tsx 又在 import/render 旧桌面底栏了——底栏只应有一份实现'
            '（components/dock/dock-nav.tsx），两套并存必然漂移。',
        )
        self.assertFalse(
            _DOCK_GONE.exists(),
            'components/ui/floating-dock.tsx 又回来了：它已于 2026-10-26 作为死代码删除'
            '（`{false && …}` 分支），恢复它等于把两套底栏渲染重新引进仓库。',
        )

    def test_dock_has_a_single_implementation_for_both_layouts(self) -> None:
        """桌面与手机**共用**同一个 DockNav 实例，不许再分两套渲染分支。

        这条守的是 2026-10-26 的收敛：原来桌面走 `FloatingDock`（放大、分组竖线、
        mousemove 量矩形），手机走 `DockNav`，两套几何与交互完全不同，改一处必然
        漂移一处。现在只有 `<DockNav … scale={isMobileDock ? 1.2 : 1} />`，
        差别只剩缩放比。
        """
        bar, nav = _code(_BAR), _code(_NAV)
        self.assertEqual(
            bar.count('<DockNav'), 1,
            'ManagementBar 里出现了不止一个 <DockNav>：底栏只应有单实例，'
            '两处渲染会让两套几何各自演进。',
        )
        self.assertRegex(
            bar, r'scale=\{isMobileDock \? [\d.]+ : [\d.]+\}',
            '两端差异没有收敛到 scale 一个参数上——又有人在按布局分叉渲染了？',
        )
        # 分隔线仍在（这是当年移动端「11 项平铺」的修复不能回退的部分）
        self.assertIn(
            'dividerBefore', nav,
            'dock-nav 不再支持 dividerBefore —— 分隔线没了，底栏又变成一整排平铺。',
        )
        self.assertIn(
            'data-slot="dock-divider"', nav,
            '分隔线没有标记：验收脚本与测试都定位不到它。',
        )

    def test_dividers_come_from_item_flags(self) -> None:
        """分隔线只能由条目上的布尔标记驱动，不能由「按 title 猜」或 hover 决定。

        旧实现里桌面端的分隔线靠 `onMouseEnter` 显隐，而底栏会放大、`translateX(-50%)`
        居中 ⇒ 分隔线会被图标从光标底下推走（实测挪 8~30px，线本身只有 9px 宽），
        组名「亮一下又灭」。那套机制随 `floating-dock.tsx` 一起删除后，判据只剩一条：
        分隔线是**数据**（`dividerBefore`），不再是交互态。
        """
        nav = _code(_NAV)
        self.assertRegex(
            nav, r'\{it\.dividerBefore && \(',
            '分隔线不再由 `it.dividerBefore` 驱动——小心又退回「按 title 猜分隔线」的写法。',
        )
        self.assertNotIn(
            "title === 'divider'", nav,
            'dock-nav 里又出现了按 title 判断分隔线的写法：那正是移动端分组语义消失的原因。',
        )

    def test_tip_can_always_be_dismissed(self) -> None:
        """引导气泡必须**随时关得掉**：点别处能关、也有明确的关闭按钮。

        原实现只能靠「把几条都点完」关掉，于是它会一直挂在底栏正上方——而底栏可以
        被拖到页面中部，于是它正好压住正文（用户反馈「反复出现在页面中部」）。
        """
        bar = _code(_BAR)
        self.assertIn(
            "closest('[data-slot=dock-tip]')", bar,
            '引导气泡没有「点别处就关掉」的处理：它会一直挂着，而底栏可以被拖到'
            '页面中部，正好压住正文。',
        )
        self.assertRegex(
            bar, r"addEventListener\('pointerdown',\s*handlePointerDown,\s*true\)",
            '「点别处关掉」要用**捕获**阶段监听：气泡挂在底栏内部，底栏自己的'
            'pointerdown 冒泡到 document 之前可能已被处理，气泡会关不掉。',
        )
        self.assertIn(
            'data-slot="dock-tip"', bar,
            '引导气泡没有标记，验收脚本定位不到它',
        )
        self.assertRegex(
            bar, r"onClick=\{handleDismissDockTip\}",
            '引导气泡没有明确的关闭按钮——用户不想看时只能一条条点完',
        )

    def test_tip_index_is_clamped(self) -> None:
        """引导步骤的索引要夹回范围内。

        切换「固定底部 / 悬浮」会让步骤数变化（固定模式下「长按可拖动」那一条被
        去掉）。停在最后一步时切过去，索引就越界了，直接取会渲染出 `undefined`
        ——气泡里出现空白，而且不报错。
        """
        bar = _code(_BAR)
        self.assertRegex(
            bar, r'dockTipSteps\[Math\.min\(dockTipStep,\s*dockTipSteps\.length - 1\)\]',
            '引导气泡直接用了 dockTipStep 取步骤：切换停靠模式会让步骤数变少，'
            '索引越界时渲染出 undefined（气泡里一片空白，且不报错）。',
        )

    def test_pinned_mode_really_disables_dragging(self) -> None:
        """「固定底部」模式下拖动要**彻底关掉**，而不是拖完再弹回来。

        后者会先跟手走一段、松手才归位，看起来像拖动坏了。同时引导里那条
        「长按空白区域可拖动」也必须跟着消失——留一句当前模式下做不到的话，
        用户会反复长按然后怀疑是自己手法不对。
        """
        bar = _code(_BAR)
        self.assertRegex(
            bar, r"if \(dockMode !== 'floating'\) return;",
            '「固定底部」模式下没有把拖动关掉：底栏会先跟手走一段、松手才弹回原位，'
            '看起来像拖动坏了。',
        )
        for step in ('dock.mobile2', 'dock.desktop1'):
            self.assertIn(
                f"dockMode === 'floating' ? [t('{step}')]", bar,
                f'引导里 {step} 那条（「长按可拖动」）没有跟着停靠模式去掉：'
                '固定底部时拖动已被禁用，留一句做不到的话比不写更糟。',
            )

    def test_dock_mode_is_remembered(self) -> None:
        """停靠模式要**写进也读回** localStorage。

        只写不读（或只读不写）都会让这个开关看起来是坏的：刷新一次就回到默认，
        用户每次打开面板都要重设一遍，而界面上没有任何提示说明原因。
        两个方向都要断言——只查「这个键出现过」的话，删掉写或读任一侧仍然是绿的。
        """
        bar = _code(_BAR)
        self.assertRegex(
            bar, r'localStorage\.setItem\(DOCK_MODE_STORAGE_KEY',
            '停靠模式没有写进 localStorage：刷新一次就回到默认',
        )
        self.assertRegex(
            bar, r'localStorage\.getItem\(DOCK_MODE_STORAGE_KEY',
            '停靠模式没有从 localStorage 读回：存了也不生效，刷新后仍是默认',
        )

    def test_group_keys_exist_in_all_locales(self) -> None:
        """底栏新增的文案键必须在**每个**语言包里都存在。

        缺了不会报错：页面上直接显示裸键名（`nav.groupOps`），而且
        `test_web_i18n` 只查「各语言键集是否一致」，**不查引用是否存在**——
        只有这条能发现「代码引用了没人翻译的键」。
        """
        needed = ['nav.groupOverview', 'nav.groupOps', 'nav.groupGovernance',
                  'dock.tipDismiss', 'dock.tipNext',
                  'profile.dockLabel', 'profile.dockFloating', 'profile.dockPinned']
        self.assertTrue(_LOCALES.is_dir(), f'找不到语言包目录: {_LOCALES}')
        missing: list[str] = []
        for path in sorted(_LOCALES.glob('*.json')):
            data = json.loads(path.read_text(encoding='utf-8'))
            for dotted in needed:
                head, _, tail = dotted.partition('.')
                node = data.get(head)
                if not isinstance(node, dict) or not isinstance(node.get(tail), str) \
                        or not node.get(tail).strip():
                    missing.append(f'{path.name}: {dotted}')
        self.assertEqual(
            missing, [],
            '以下语言包缺少底栏这一批用到的键，页面上会直接显示裸键名：\n  '
            + '\n  '.join(missing),
        )


if __name__ == '__main__':
    unittest.main()
