'use client';

import * as React from 'react';

import {DockItem} from '@/components/dock/dock-item';
import {CallsIcon, ChatsIcon, ContactsIcon, SettingsIcon} from '@/components/dock/dock-icons';
import {cn} from '@/lib/utils';

/**
 * Telegram iOS 风格底部 Dock（2026-10-23 ✓ 第二版：图标 + 文字）
 *
 * ── 几何（第二版调整过，见每项标注）─────────────
 *   容器     62 高 / rounded-[31px] / inset-x-3.5 / bottom-[26px]     不变
 *   指示球   高 = 容器 - 2×2px = 58（top/bottom 各 2px）                ★ 改
 *            宽 = 25% - 4px                                              ★ 改
 *            圆角 = 31 - 2 = 29                                          ★ 改
 *   图标     27 → **22**（给文字腾纵向空间）                              ★ 改
 *   文字     9px / 行高 1                                                ★ 新增
 *   徽标     18 → 16 高 / rounded-8（图标变小，徽标跟着收一点）           ★ 改
 *
 * ── 指示球：距边缘 2px，且同时覆盖图标与文字 ──────
 *   `top-0.5 bottom-0.5` ⇒ 上下各内缩 2px ✓
 *   `w-[calc(25%-4px)]` + `left-[calc(var(--dock-index)*25%+2px)]`
 *     ⇒ 左右也各内缩 2px ✓
 *   指示球铺满 tab 的**整个高度** ⇒ 图标与文字都在高亮区内 ✓
 *
 * ── 为什么用 left 而不是 translate-x（这一点没变）──
 *   translateX 的百分比是**相对元素自身宽度** ✗
 *   left 的百分比才是**相对父容器** ✓
 *   ⇒ 无需 JS 测量 DOM、无需 ResizeObserver、resize 不会错位 ✓
 *
 * ── 首次挂载不播动画 ─────────────────────────────
 *   mounted 之前套 transition-none ✓ 用 rAF 在**下一帧**摘掉 ✓
 *   （setTimeout(0) 可能同帧完成 ⇒ 浏览器不触发过渡 ⇒ 动画反而丢失 ✗）
 *
 * ── 主题 ────────────────────────────────────────
 *   颜色全部走语义 token（bg-dock / text-dock-idle / bg-dock-pill …）✓
 *   明暗差异只在 globals.css 的 :root 与 .dark 两块里 ✓
 *   ⇒ 组件里没有 `dark:` 硬编码色值，也没有第二份浅色组件 ✓
 */

const TABS = [
  {key: 'chats', label: 'Chats', caption: '聊天', Icon: ChatsIcon},
  {key: 'contacts', label: 'Contacts', caption: '通讯录', Icon: ContactsIcon},
  {key: 'calls', label: 'Calls', caption: '通话', Icon: CallsIcon},
  {key: 'settings', label: 'Settings', caption: '设置', Icon: SettingsIcon},
] as const;

export type DockTabKey = (typeof TABS)[number]['key'];

/**
 * 图标尺寸：26（2026-10-23 五调：22 → 26）
 * ★ 用户要求"图表和文字再放大一点"✓
 *   26（图标）+ 2（gap）+ 11（文字）= 39 ≤ 58（62 - 2×2 内边距）⇒ 仍放得下 ✓
 */
const ICON_SIZE = 26;

export function Dock({
  className,
  unreadChats = 2,
  bgRgb,
  bgAlpha,
  pillRgb,
  pillAlpha,
}: {
  className?: string;
  /** Chats 的未读数 ✓ 点过 Chats 之后清零 ✓ */
  unreadChats?: number;
  /* ★ 2026-10-24：4 个调色参数（可选）✓
     为什么改成 props 而不是"外层 div 设变量"：
     外层注入依赖 CSS 变量沿 DOM 树继承 ✗ 实测在 demo 里没生效 ✓
     ⇒ 改成写在 Dock **自己的根元素** style 上 ⇒ 自我覆盖 ✓ 100% 生效 ✓
     传 "r g b" 三元组（如 "21 31 42"）而不是 hex ✓ 与 CSS 侧的 rgb() 语法一致 ✓ */
  bgRgb?: string;
  bgAlpha?: number;
  pillRgb?: string;
  pillAlpha?: number;
}) {
  const [activeIndex, setActiveIndex] = React.useState(0);
  const [unread, setUnread] = React.useState(unreadChats);

  /* ═══ 长按拖选（2026-10-24 ✓）
     交互：长按 260ms 进入跟手模式 ⇒ 选中框实时跟到手指位置 ⇒
           掠过哪个 tab，哪个 tab 就亮起（按距离衰减 ✓）⇒ 抬手吸附 ✓
     ★ 为什么 260ms：低于 200ms 会和"点按"混淆 ✗ 高于 320ms 会让人觉得没响应 ✓
     ★ 移动超过 8px 就取消长按 ⇒ 否则用户想横向滚动页面也会误入 ✓ */
  const dockRef = React.useRef<HTMLDivElement>(null);
  /** 按下时记下起点与时刻（不用 setTimeout ✗ 见下方说明） */
  const pressRef = React.useRef<{x: number; y: number; t: number} | null>(null);
  const [dragging, setDragging] = React.useState(false);
  /* ★★★ 2026-10-24：选中块的**连续位置**（像素）✓
     为什么必须是像素而不是 index：
       之前用 `--dock-index * 25%` ⇒ 位置只能落在整数格 ✗
       拖到两格之间时块会"跳"到最近那格 ⇒ 视觉上永远停不到中间 ✓
     ⇒ 用连续像素值 ⇒ 块可以停在任意位置 ✓（TG 就是这种跟手感）
     null 表示"未拖拽"⇒ 此时由 activeIndex 决定位置 ✓ */
  const [pillLeft, setPillLeft] = React.useState<number | null>(null);
  /* ★★ 2026-10-24：点击计数 —— 让**重复点击同一项**也能重播放大动画 ✓
     原理：动画靠 `key` 变化触发重挂载 ✗
           key 只用 activeIndex ⇒ 点同一项时 key 不变 ⇒ 动画不会重播 ✓
     ⇒ key = `${activeIndex}-${pulse}` ⇒ 每次点击 pulse+1 ⇒ 必然重挂载 ✓ */
  const [pulse, setPulse] = React.useState(0);

  /** 选中块几何（3px 边距 ✓ 块宽 = 25% - 6px） */
  const EDGE = 3;

  const pillGeometry = () => {
    const el = dockRef.current;
    if (!el) return null;
    const w = el.getBoundingClientRect().width;
    return {w, blockW: w / 4 - EDGE * 2};
  };

  /** 手指 X → 选中索引（首尾夹住 ✓ 跨多个也直接算 ✓ 不靠"上一格±1"✗） */
  const indexFromClientX = (clientX: number): {index: number; x: number} | null => {
    const el = dockRef.current;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const x = clientX - r.left;
    const idx = Math.floor((x / r.width) * 4);
    return {index: Math.max(0, Math.min(3, idx)), x};
  };

  /* ★★★ 2026-10-24 重写：不再用 setTimeout 判定长按 ✗
     旧方案的问题：
       `pointerdown` 起一个 260ms 定时器，`pointermove` 里位移 > 8px 就取消 ✗
       但触控板 / 鼠标在"按住"时会有 1~3px 的抖动 ⇒ 定时器经常被误取消 ✗
       而且用户很难在不动的前提下精确等 260ms ✓
     ⇒ 改成"移动时判定"：
       按住超过 120ms + 位移超过 4px ⇒ 直接进入跟手 ✓（2026-10-24 调快）
       不依赖定时器 ⇒ 没有"取消"这个失败路径 ✓
     ★ 大位移 + 时间短（<200ms）= 用户想滚动页面 ⇒ 放弃接管 ✓ */
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
      // 长按 + 拖动 ⇒ 进入跟手
      if (held >= 120 && dist >= 4) {
        setDragging(true);
        try {
          (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
        } catch {
          /* 忽略 */
        }
      } else if (dist > 40 && held < 120) {
        // 快速大位移 ⇒ 用户想滚动 / 滑动页面 ⇒ 不接管 ✓
        pressRef.current = null;
        return;
      } else {
        return;
      }
    }

    const hit = indexFromClientX(e.clientX);
    if (!hit) return;
    setActiveIndex(hit.index);

    /* ★ 连续位置：块中心对准手指 ✓ 夹在 3px 内边距里 ✓
       ⇒ 手指在两格之间时，块就停在两格之间 ✓ 不吸附 ✓ */
    const g = pillGeometry();
    if (g) {
      const raw = hit.x - g.blockW / 2;
      const max = g.w - EDGE - g.blockW;
      setPillLeft(Math.max(EDGE, Math.min(max, raw)));
    }

    e.preventDefault();
  };

  const endDrag = () => {
    pressRef.current = null;
    if (!dragging) return;
    setDragging(false);
    /* ★★ 2026-10-24：**不重置 pillLeft** ✗
       用户要求「选中块能在两个相邻选项之间停留，处于中间位置时也能稳定保留」
       ⇒ 松手时块停在原处 ✓ 不吸附到整数格 ✓
       ⇒ activeIndex 仍取最近的那一项（决定颜色与图标）✓
       两条独立：位置是连续的，选中是离散的 ✓ */
    /* ★★★ 2026-10-24 修正：松手按**重叠面积**吸附 ✓
       规则：块与某个 tab 的「重叠宽度」谁大就归谁 ✓
       为什么不是"块中心落在哪格"：
         块宽（25%-6px）比 tab 宽（25%）**窄 6px** ✗
         中心法与重叠法的判定线会差 3px ⇒ 在边界附近手感不一致 ✓
       实现：算 4 个重叠宽度，取最大 ✓ 然后把 pillLeft 归零
             ⇒ 由 `calc(--dock-index * 25% + 3px)` 接管 ✓
             ⇒ 自动走 500ms 弹性曲线 ⇒ 平滑吸附 ✓ */
    if (pillLeft !== null) {
      const g = pillGeometry();
      if (g) {
        const tabW = g.w / 4;
        const bl = pillLeft;                 // 块左边界
        const br = pillLeft + g.blockW;      // 块右边界
        let best = 0;
        let bestOverlap = -1;
        for (let i = 0; i < 4; i++) {
          const tabL = i * tabW;
          const tabR = tabL + tabW;
          const overlap = Math.min(br, tabR) - Math.max(bl, tabL);
          if (overlap > bestOverlap) {
            bestOverlap = overlap;
            best = i;
          }
        }
        setActiveIndex(best);
      }
      setPillLeft(null);                     // ★ 归零 ⇒ 吸附到该 tab 的标准位置 ✓
    }
    if (activeIndex === 0 && unread > 0) setUnread(0);
  };

  /** 选中某个 tab（原生 button 版 ✓ 2026-10-24） */
  const selectIndex = (next: number) => {
    const clamped = Math.max(0, Math.min(TABS.length - 1, next));
    setActiveIndex(clamped);
    /* ★ 点按选中 ⇒ 清掉拖拽留下的连续位置 ✓
       否则块会停在拖动时的旧像素位置，不跟 index 走 ✗ */
    setPillLeft(null);
    setPulse((p) => p + 1);          // ★ 每次点击都 +1 ⇒ 重播放大动画 ✓
    if (clamped === 0 && unread > 0) setUnread(0);
  };

  /** 方向键切换（role="tablist" 的无障碍要求 ✓） */
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowRight') {
      e.preventDefault();
      selectIndex(activeIndex + 1);
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      selectIndex(activeIndex - 1);
    } else if (e.key === 'Home') {
      e.preventDefault();
      selectIndex(0);
    } else if (e.key === 'End') {
      e.preventDefault();
      selectIndex(TABS.length - 1);
    }
  };

  return (
    <div
      ref={dockRef}
      data-slot="tg-dock"
      // ★ 2026-10-24 补回：这三个属性上一版被漏掉了 ✗
      //   症状就是用户看到 "dragging: undefined" ✓ 事件根本没绑 ✓
      data-dragging={dragging ? 'true' : 'false'}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      style={
        {
          ...(bgRgb !== undefined ? {'--dock-bg-rgb': bgRgb} : {}),
          ...(bgAlpha !== undefined ? {'--dock-bg-a': String(bgAlpha)} : {}),
          ...(pillRgb !== undefined ? {'--dock-pill-rgb': pillRgb} : {}),
          ...(pillAlpha !== undefined ? {'--dock-pill-a': String(pillAlpha)} : {}),
        } as React.CSSProperties
      }
      className={cn(
        // ── 62 / 31 / 14 / 26 ──
        'absolute inset-x-3.5 bottom-[26px] h-[62px] rounded-[31px]',
        // ★ touch-none：阻止浏览器把指针手势当成页面滚动/缩放 ✗
        //   缺了它，移动端一拖就被页面卷走 ⇒ 永远进不了跟手 ✓
        // ★ select-none：防止长按时选中图标文字 ✓
        'touch-none select-none',
        // ── 清透玻璃三件套（2026-10-23 六调 ✓）
        //   ① blur 34px   —— 把背后糊开 ✓
        //   ② brightness 110% —— ★ 让透出来的背景**变亮** ⇒ 这才是"清透"而非"发灰"
        //      （之前只用了 blur + saturate，透出来的背景是暗的 ⇒ 显得脏 ✗）
        //   ③ saturate 200% —— 把背景颜色拉浓 ⇒ 玻璃才有"色" ✗ 否则是灰玻璃
        'bg-dock backdrop-blur-[34px] backdrop-brightness-110 backdrop-saturate-[200%]',
        // ── 阴影来自 @theme 的 --shadow-dock（四层，含内高光与 0.5px 内描边）──
        'shadow-dock',
        // ── 裁掉指示球溢出圆角的部分 ✓
        'overflow-hidden',
        className,
      )}
    >
      {/* ── 选中块（2026-10-24 照 TG 视频帧还原）──
          TG 实测：中性灰圆角块，**不是蓝色**；无描边 ✓
          几何：距 Dock 边缘 8px，圆角 23px（= 31 - 8）✓
          颜色走 --dock-pill-bg（浅色黑6% / 深色白14%，自动跟随主题）✓ */}
      <div
        aria-hidden
        data-slot="dock-indicator"
        style={
          {
            '--dock-index': activeIndex,
            ...(pillLeft !== null ? {'--pill-left': `${pillLeft}px`} : {}),
          } as React.CSSProperties
        }
        className={cn(
          // ★ 3px 边距：上下各 3px ⇒ 高度 62 - 6 = 56 ✓
          'pointer-events-none absolute top-[3px] bottom-[3px]',
          // ★ 块宽 = 25% - 2×3px ✓ 圆角 = 31 - 3 = 28 ✓
          'w-[calc(25%-6px)] rounded-[28px]',
          // ★ 位置：拖动时用连续像素 ✓ 静止时按索引 ✓
          pillLeft !== null
            ? 'left-[var(--pill-left)]'
            : 'left-[calc(var(--dock-index)*25%+3px)]',
          'bg-dock-pill',
          dragging
            ? 'transition-[left] duration-75 ease-out'
            : 'transition-[left] duration-500 ease-[cubic-bezier(.34,1.36,.64,1)]',
        )}
      />

      {/* ── 图标 + 文字层（原生 tablist ✓ 2026-10-24 替换 Radix ToggleGroup）── */}
      <div
        role="tablist"
        aria-label="主导航"
        onKeyDown={onKeyDown}
        className="relative flex h-full w-full"
      >
        {TABS.map(({key, label, caption, Icon}, index) => (
          <DockItem
            key={key}
            label={label}
            caption={caption}
            index={index}
            active={activeIndex === index}
            pulseKey={`${activeIndex}-${pulse}`}
            badge={key === 'chats' ? unread : 0}
            onClick={() => {
              // 拖拽中抬手会顺带触发 click ⇒ 那会把连续位置清掉 ✗ 必须挡掉
              if (!dragging) selectIndex(index);
            }}
          >
            <Icon size={ICON_SIZE} />
          </DockItem>
        ))}
      </div>
    </div>
  );
}
