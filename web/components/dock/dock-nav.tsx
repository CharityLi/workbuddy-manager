'use client';

import * as React from 'react';

import {cn} from '@/lib/utils';

/**
 * 通用 Dock 导航（2026-10-24 ✓）
 *
 * 把 `/dev-dock` 里验证过的视觉与交互原样搬过来，但**项数与内容由外部传入** ✓
 *
 * ── 几何（与 demo 一致，按项数换算）──────────────
 *   每个 tab 宽度 = 100% / n
 *   选中块宽度   = 上述宽度 − 6px（左右各 3px）
 *   选中块左偏移 = index × (100%/n) + 3px
 *   ⇒ n=4 时就是 demo 里那套：25% − 6px、+3px ✓
 *
 * ── 交互（与 demo 一致）────────────────────────
 *   长按 120ms + 位移 4px ⇒ 跟手（连续像素，可停在两格之间）
 *   松手 ⇒ 按「与哪个 tab 的重叠宽度更大」吸附
 *   点按 ⇒ 立即切换并重播放大动画（pulse 计数保证重复点也播）
 *
 * ── 为什么不用 floating-dock.tsx ──────────────
 *   那个组件是纯图标、无文字、无选中块，结构写死 ✗
 *   改它会影响所有用它的地方 ✗
 *   ⇒ 新写一个，旧组件原样保留 ⇒ 出问题可一行切回 ✓
 */

export type DockNavItem = {
  /** 稳定 key（用于 React 列表与动画重挂载） */
  key: string;
  /** 图标下方文字（2~3 字最佳） */
  label: string;
  /** 图标节点（建议 20~26px） */
  icon: React.ReactNode;
  /** 是否当前页 */
  active: boolean;
  onClick: () => void;
  /** 未读/提醒数 */
  badge?: number;
  /** 该项左侧画一条分组竖线（2026-10-24 ✓ 恢复旧 Dock 的分组分隔）*/
  dividerBefore?: boolean;
};

export function DockNav({
  items,
  className,
  scale = 1,
}: {
  items: DockNavItem[];
  /** 追加到根容器的类（用于定位覆盖） */
  className?: string;
  /** 整体等比缩放（2026-10-24 ✓）：1 = 62px 基准，1.5 = 93px 高 ✓
      圆角、选中块边距、文字字号全部按同一系数缩放 ⇒ 视觉比例不变 ✓ */
  scale?: number;
}) {
  const n = Math.max(1, items.length);

  /* ★ 每项的宽度与选中块的连续位置都要按 n 算 ✗ 不能写死 25% ✓ */
  const STEP = `${(100 / n).toFixed(6)}%`;

  const dockRef = React.useRef<HTMLDivElement>(null);
  const pressRef = React.useRef<{x: number; y: number; t: number} | null>(null);
  const [dragging, setDragging] = React.useState(false);
  const [pillLeft, setPillLeft] = React.useState<number | null>(null);
  const [pulse, setPulse] = React.useState(0);
  /* ★★★ 2026-10-24 修复崩溃 + 卡顿 ✗
     旧实现：`onPointerMove` 里直接 `items[idx].onClick()` ⇒ 每帧一次 `router.push` ✗
     ⇒ ① 每帧触发 Next 路由跳转 ⇒ 主线程被反复打断 ⇒ **帧率极低** ✓
       ② 页面组件反复卸载/挂载 ⇒ **client-side exception 崩溃** ✓
     ⇒ 新实现：拖动只改 `previewIdx`（纯视觉）✓ **不跳转** ✓
              松手时才调一次 `onClick` ✓ */
  const [previewIdx, setPreviewIdx] = React.useState<number | null>(null);
  /* ★★★ 2026-10-24 修复「选中后闪烁 / 块回退」✗
     现象：点一下 tab ⇒ 选中块先弹回旧位置，再跳到新位置 ⇒ 视觉上闪一下 ✓
     原因：`select()` 里把 `pillLeft` 清成 null ⇒ 块立刻按**旧的** activeIndex 定位 ✗
           而真正的 activeIndex 要等 `router.push` 完成、新页面渲染后才会变 ✓
           ⇒ 中间有一帧（或几帧）停在旧位置 ✓
     ⇒ 用 `pendingIdx` 占住目标位置 ✓ 直到 `activeIndex` 追上来才释放 ✓ */
  const [pendingIdx, setPendingIdx] = React.useState<number | null>(null);

  const EDGE = 3;

  const geometry = () => {
    const el = dockRef.current;
    if (!el) return null;
    const w = el.getBoundingClientRect().width;
    return {w, blockW: w / n - EDGE * 2, tabW: w / n};
  };

  const activeIndex = Math.max(0, items.findIndex((it) => it.active));
  /* 拖动中用 previewIdx 驱动视觉 ✓ 松手后回落到真实 activeIndex ✓ */
  const effectiveActive = previewIdx ?? pendingIdx ?? activeIndex;

  /* 路由追上目标后释放 pending ✓（否则新旧状态会长期并存） */
  React.useEffect(() => {
    if (pendingIdx !== null && activeIndex === pendingIdx) {
      setPendingIdx(null);
    }
  }, [activeIndex, pendingIdx]);

  const select = (i: number) => {
    /* ★ 不再把 pillLeft 清成 null ✗
       清空 ⇒ 块立刻按**旧** activeIndex 定位 ⇒ 一闪 ✓
       改为：先占住目标位置（pendingIdx）✓ 再清 pillLeft ✓
       ⇒ 块从当前位置平滑滑到目标 ✓ 不回退 ✓ */
    setPendingIdx(i);
    setPillLeft(null);
    setPulse((p) => p + 1);     // 每次点击都重播放大 ✓
    items[i]?.onClick();
  };

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    pressRef.current = {x: e.clientX, y: e.clientY, t: Date.now()};
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const p = pressRef.current;
    if (!p) return;

    if (!dragging) {
      const dist = Math.hypot(e.clientX - p.x, e.clientY - p.y);
      const held = Date.now() - p.t;
      if (held >= 120 && dist >= 4) {
        setDragging(true);
        try {
          (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
        } catch {
          /* ignore */
        }
      } else if (dist > 40 && held < 120) {
        pressRef.current = null;   // 用户在滑动页面 ✓ 不接管
        return;
      } else {
        return;
      }
    }

    const el = dockRef.current;
    if (!el) return;
    const g = geometry();
    if (!g) return;
    const r = el.getBoundingClientRect();
    const x = e.clientX - r.left;
    const idx = Math.max(0, Math.min(n - 1, Math.floor(x / g.tabW)));
    setPreviewIdx(idx);          // ★ 只改视觉，不跳转 ✓（松手才跳）

    const raw = x - g.blockW / 2;
    const max = g.w - EDGE - g.blockW;
    setPillLeft(Math.max(EDGE, Math.min(max, raw)));   // ★ 连续值 ✓
    e.preventDefault();
  };

  const endDrag = () => {
    pressRef.current = null;
    if (!dragging) return;
    setDragging(false);

    /* 松手：按重叠宽度吸附 ✓ */
    if (pillLeft !== null) {
      const g = geometry();
      if (g) {
        const bl = pillLeft;
        const br = pillLeft + g.blockW;
        let best = 0;
        let bestOverlap = -1;
        for (let i = 0; i < n; i++) {
          const tabL = i * g.tabW;
          const tabR = tabL + g.tabW;
          const overlap = Math.min(br, tabR) - Math.max(bl, tabL);
          if (overlap > bestOverlap) {
            bestOverlap = overlap;
            best = i;
          }
        }
        setPendingIdx(best);       // ★ 占住吸附目标，防止回退 ✓
        items[best]?.onClick();    // ★ 全程只在这里跳一次 ✓
      }
      setPreviewIdx(null);
      setPillLeft(null);        // ⇒ 由 index 计算 + 500ms 弹性吸附 ✓
    }
  };

  return (
    <div
      ref={dockRef}
      data-slot="dock-nav"
      data-dragging={dragging ? 'true' : 'false'}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      style={
        {
          '--dock-step': STEP,
          '--dock-scale': String(scale),
        } as React.CSSProperties
      }
      className={cn(
        // ── 62 / 31 ──
        'relative w-full',
        // ★ 高度与圆角随 scale 等比 ✓
        'h-[calc(62px*var(--dock-scale,1))] rounded-[calc(31px*var(--dock-scale,1))]',
        // ── 清透玻璃三件套 ──
        'bg-dock backdrop-blur-[34px] backdrop-brightness-110 backdrop-saturate-[200%]',
        'shadow-dock overflow-hidden',
        'touch-none select-none',
        className,
      )}
    >
      {/* 选中块：3px 边距 ✓ 连续位置 ✓ */}
      <div
        aria-hidden
        style={
          {
            '--dock-index': effectiveActive,
            ...(pillLeft !== null ? {'--pill-left': `${pillLeft}px`} : {}),
          } as React.CSSProperties
        }
        className={cn(
          /* 边距 / 圆角按 scale 等比 ✓ 基准 3px / 28px */
          'pointer-events-none absolute',
          'top-[calc(3px*var(--dock-scale,1))] bottom-[calc(3px*var(--dock-scale,1))]',
          'w-[calc(var(--dock-step)-6px*var(--dock-scale,1))]',
          'rounded-[calc(28px*var(--dock-scale,1))]',
          pillLeft !== null
            ? 'left-[var(--pill-left)]'
            : 'left-[calc(var(--dock-index)*var(--dock-step)+3px)]',
          'bg-dock-pill',
          dragging
            ? 'transition-[left] duration-75 ease-out'
            : 'transition-[left] duration-500 ease-[cubic-bezier(.34,1.36,.64,1)]',
        )}
      />

      {/* 图标 + 文字 */}
      <div role="tablist" aria-label="主导航" className="relative flex h-full w-full">
        {items.map((it, i) => (
          <button
            key={it.key}
            type="button"
            role="tab"
            aria-selected={effectiveActive === i}
            aria-label={it.badge ? `${it.label}，${it.badge} 条未读` : it.label}
            data-slot="dock-nav-item"
            tabIndex={effectiveActive === i ? 0 : -1}
            onClick={() => {
              if (!dragging) select(i);   // 拖拽抬手会顺带触发 click ⇒ 挡掉 ✓
            }}
            style={{width: STEP}}
            className={cn(
              'group relative flex h-full flex-col items-center justify-center',
              'gap-[calc(2px*var(--dock-scale,1))]',
              /* ★★★ 2026-10-24 修正：两个必加项 ✗
                 ① `min-w-0` —— button 默认 `min-width: auto`
                     ⇒ flex 子项**永远不会缩到内容宽度以下** ✗
                     ⇒ 10 项总宽 > 容器 ⇒ 溢出被 overflow-hidden 裁掉 ✓
                 ② `whitespace-nowrap` —— 不加则「仪表盘」在窄列里折成两行 ✓
                 ③ `px-0.5` —— button 自带水平内边距，会吃掉本就不多的宽度 ✗ */
              'min-w-0 whitespace-nowrap px-0.5',
              'outline-none focus-visible:ring-2 focus-visible:ring-dock-active/60',
              // 三态只改颜色 ✓ 不加任何背景 ✓
              effectiveActive === i ? 'text-dock-active' : 'text-dock-idle hover:text-dock-active/70',
              'transition-[color,opacity] duration-300 ease-out',
            )}
          >
            {/* ★ 分组竖线：旧 Dock 有，新版要恢复 ✓ 用 header 前景色 12% 透明度 */}
            {it.dividerBefore && (
              <span
                aria-hidden
                className="pointer-events-none absolute left-0 top-1/4 bottom-1/4 w-px bg-foreground/[0.12]"
              />
            )}
            <span
              key={`${effectiveActive === i}-${pulse}`}
              className={cn(
                'relative grid place-items-center',
                effectiveActive === i && 'animate-[dock-pop_480ms_cubic-bezier(.34,1.36,.64,1)]',
              )}
            >
              {it.icon}
              {!!it.badge && (
                <span
                  aria-hidden
                  className={cn(
                    'absolute -top-1 left-1/2 ml-[9px] grid h-[16px] min-w-[16px] place-items-center',
                    'rounded-[8px] bg-dock-badge px-[4px]',
                    'text-[10px] font-semibold leading-none text-white',
                    'shadow-[0_0_0_2px_var(--dock-bg)]',
                  )}
                >
                  {it.badge > 99 ? '99+' : it.badge}
                </span>
              )}
            </span>
            <span
              key={`t-${effectiveActive === i}-${pulse}`}
              className={cn(
                'leading-none tracking-tight truncate max-w-full',
                'text-[calc(10px*var(--dock-scale,1))]',
                effectiveActive === i ? 'font-semibold' : 'font-medium',
                'transition-[font-weight,color] duration-300 ease-out',
                effectiveActive === i && 'animate-[dock-pop-text_480ms_cubic-bezier(.34,1.36,.64,1)]',
              )}
            >
              {it.label}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
