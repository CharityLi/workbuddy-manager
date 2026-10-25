'use client';

import {useCallback, useEffect, useState} from 'react';

import {Dock} from '@/components/dock/dock';

/**
 * Dock 演示页（2026-10-24 ✓ 第六版：带自检面板）
 *
 * ★ 为什么加自检：
 *   前几版"滑块无效 / 长按无效"无法远程复现 ✗
 *   ⇒ 直接在页面上显示运行时真相：
 *      · Dock 元素找没找到
 *      · 它的 computed backgroundColor 是多少（拖滑块时应该变）
 *      · --dock-bg-a 变量实际值
 *      · 指示球的 left 值
 *   这样一眼能分清是「Dock 没渲染」「变量没写进去」还是「写进去了但 CSS 没生效」
 */

const SAVE_KEY = 'dock-tune-v1';

function hexToTriplet(hex: string): string {
  const h = hex.replace('#', '');
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  const n = parseInt(full, 16);
  if (Number.isNaN(n)) return '0 0 0';
  return `${(n >> 16) & 255} ${(n >> 8) & 255} ${n & 255}`;
}

type Tune = {bgHex: string; bgA: number; pillHex: string; pillA: number};
const DEFAULTS: Tune = {bgHex: '#151f2a', bgA: 0.42, pillHex: '#3390ec', pillA: 0.2};

export default function DockDemoPage() {
  const [bgHex, setBgHex] = useState(DEFAULTS.bgHex);
  const [bgA, setBgA] = useState(DEFAULTS.bgA);
  const [pillHex, setPillHex] = useState(DEFAULTS.pillHex);
  const [pillA, setPillA] = useState(DEFAULTS.pillA);
  const [msg, setMsg] = useState('');

  /* ═══ 自检：每 400ms 读一次真实 DOM 值 ═══ */
  const [probe, setProbe] = useState({
    found: false,
    bg: '—',
    bgVar: '—',
    left: '—',
    dragging: '—',
  });

  useEffect(() => {
    const id = window.setInterval(() => {
      const dock = document.querySelector('[data-slot="tg-dock"]') as HTMLElement | null;
      const ind = document.querySelector('[data-slot="dock-indicator"]') as HTMLElement | null;
      if (!dock) {
        setProbe({found: false, bg: '—', bgVar: '—', left: '—', dragging: '—'});
        return;
      }
      const cs = getComputedStyle(dock);
      setProbe({
        found: true,
        bg: cs.backgroundColor,
        bgVar: cs.getPropertyValue('--dock-bg-a').trim() || '（空）',
        left: ind ? getComputedStyle(ind).left : '—',
        dragging: dock.dataset.dragging ?? '—',
      });
    }, 400);
    return () => window.clearInterval(id);
  }, []);

  // 读上次保存
  useEffect(() => {
    try {
      const raw = localStorage.getItem(SAVE_KEY);
      if (!raw) return;
      const t = JSON.parse(raw) as Tune;
      if (typeof t.bgHex === 'string') setBgHex(t.bgHex);
      if (typeof t.bgA === 'number') setBgA(t.bgA);
      if (typeof t.pillHex === 'string') setPillHex(t.pillHex);
      if (typeof t.pillA === 'number') setPillA(t.pillA);
      setMsg('已恢复上次保存');
    } catch {
      /* ignore */
    }
  }, []);

  const flash = useCallback((text: string) => {
    setMsg(text);
    window.setTimeout(() => setMsg(''), 1800);
  }, []);

  const save = () => {
    try {
      localStorage.setItem(SAVE_KEY, JSON.stringify({bgHex, bgA, pillHex, pillA}));
      flash('已保存 ✓');
    } catch {
      flash('保存失败');
    }
  };

  const reset = () => {
    setBgHex(DEFAULTS.bgHex);
    setBgA(DEFAULTS.bgA);
    setPillHex(DEFAULTS.pillHex);
    setPillA(DEFAULTS.pillA);
    flash('已回默认');
  };

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-medium">Dock 预览</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          容器 62 / 圆角 31 · 图标 26 + 文字 11px · 4 参数 + 长按拖选
        </p>
      </div>

      {/* ═══ 自检面板（关键 ✓）═══ */}
      <div className="rounded-2xl border-2 border-amber-500/40 bg-amber-500/10 p-4">
        <div className="mb-2 flex items-center gap-2 text-sm font-medium text-amber-700 dark:text-amber-300">
          运行时自检
          <span className="text-[10px] font-normal text-muted-foreground">（每 400ms 刷新）</span>
        </div>
        <div className="grid grid-cols-1 gap-1.5 font-mono text-[11px] sm:grid-cols-2">
          <div>
            Dock 元素：<b>{probe.found ? '✓ 找到' : '✗ 没找到'}</b>
          </div>
          <div>
            计算背景色：<b>{probe.bg}</b>
          </div>
          <div>
            --dock-bg-a：<b>{probe.bgVar}</b>
          </div>
          <div>
            指示球 left：<b>{probe.left}</b>
          </div>
          <div>
            data-dragging：<b>{probe.dragging}</b>
          </div>
          <div>
            当前传入 alpha：<b>{bgA.toFixed(2)}</b>
          </div>
        </div>
        <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
          拖动「Dock 不透明度」滑块时，上面「计算背景色」和「--dock-bg-a」应该实时变化。
          <br />
          长按时「data-dragging」会从 false 变 true（长按 260ms 后）。
        </p>
      </div>

      {/* ═══ 控制面板 ═══ */}
      <div className="rounded-2xl bg-muted p-4">
        <div className="mb-4 flex items-center justify-between">
          <div className="text-sm font-medium">手动调节</div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={save}
              className="rounded-full bg-foreground px-3 py-1 text-xs font-medium text-background"
            >
              保存
            </button>
            <button
              type="button"
              onClick={reset}
              className="rounded-full border border-border px-3 py-1 text-xs"
            >
              恢复默认
            </button>
          </div>
        </div>

        {msg && <div className="mb-3 rounded-lg bg-background/70 px-3 py-1.5 text-xs">{msg}</div>}

        <div className="grid gap-5 sm:grid-cols-2">
          <div className="rounded-xl bg-background/60 p-3">
            <div className="mb-2 text-xs font-medium">① Dock 底色</div>
            <div className="flex items-center gap-2">
              <input
                type="color"
                value={bgHex}
                onChange={(e) => setBgHex(e.target.value)}
                className="h-9 w-14 cursor-pointer rounded border border-border"
                aria-label="Dock 底色"
              />
              <code className="text-[11px] text-muted-foreground">{hexToTriplet(bgHex)}</code>
            </div>
          </div>

          <div className="rounded-xl bg-background/60 p-3">
            <div className="mb-2 flex justify-between text-xs font-medium">
              <span>② Dock 不透明度</span>
              <span className="font-mono text-muted-foreground">{bgA.toFixed(2)}</span>
            </div>
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={bgA}
              onChange={(e) => setBgA(Number(e.target.value))}
              className="w-full"
              aria-label="Dock 不透明度"
            />
          </div>

          <div className="rounded-xl bg-background/60 p-3">
            <div className="mb-2 text-xs font-medium">③ 选中底色</div>
            <div className="flex items-center gap-2">
              <input
                type="color"
                value={pillHex}
                onChange={(e) => setPillHex(e.target.value)}
                className="h-9 w-14 cursor-pointer rounded border border-border"
                aria-label="选中底色"
              />
              <code className="text-[11px] text-muted-foreground">{hexToTriplet(pillHex)}</code>
            </div>
          </div>

          <div className="rounded-xl bg-background/60 p-3">
            <div className="mb-2 flex justify-between text-xs font-medium">
              <span>④ 选中不透明度</span>
              <span className="font-mono text-muted-foreground">{pillA.toFixed(2)}</span>
            </div>
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={pillA}
              onChange={(e) => setPillA(Number(e.target.value))}
              className="w-full"
              aria-label="选中不透明度"
            />
          </div>
        </div>
      </div>

      {/* ═══ 手机模拟 ═══ */}
      <div className="relative mx-auto h-[400px] w-full max-w-[420px] overflow-hidden rounded-[30px] border border-border/60">
        <div
          className="absolute inset-0"
          style={{
            background:
              'linear-gradient(160deg, #7c3aed 0%, #2563eb 38%, #06b6d4 68%, #10b981 100%)',
          }}
        />
        <div className="absolute -left-10 top-12 h-40 w-40 rounded-full bg-pink-400/60 blur-2xl" />
        <div className="absolute right-0 top-40 h-52 w-52 rounded-full bg-amber-300/50 blur-2xl" />

        <div className="absolute inset-0 flex flex-col gap-3 p-5">
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              className="flex items-center gap-3 rounded-2xl bg-white/15 p-2.5 backdrop-blur-sm"
            >
              <div className="h-9 w-9 shrink-0 rounded-full bg-white/40" />
              <div className="flex-1 space-y-1.5">
                <div className="h-2.5 w-1/3 rounded bg-white/50" />
                <div className="h-2 w-2/3 rounded bg-white/30" />
              </div>
            </div>
          ))}
        </div>

        <Dock
          unreadChats={2}
          bgRgb={hexToTriplet(bgHex)}
          bgAlpha={bgA}
          pillRgb={hexToTriplet(pillHex)}
          pillAlpha={pillA}
        />
      </div>

      <div className="rounded-2xl bg-muted p-4 text-xs leading-relaxed text-muted-foreground">
        <div className="mb-1 font-medium text-foreground">操作</div>
        <ul className="space-y-1">
          <li>· 拖 4 个控件 ⇒ 上方自检的「计算背景色」「--dock-bg-a」应实时变化</li>
          <li>· 在 Dock 上<b>按住 260ms</b> 再横拖 ⇒ 「data-dragging」变 true，高亮块跟手</li>
          <li>· 掠过 tab ⇒ 该 tab 出现半透明玻璃高亮，离开淡出</li>
          <li>· 抬手 ⇒ 吸附到当前项</li>
        </ul>
      </div>
    </div>
  );
}
