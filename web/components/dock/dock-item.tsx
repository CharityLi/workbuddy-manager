'use client';

import * as React from 'react';

import {cn} from '@/lib/utils';

/**
 * Dock 单个 tab（2026-10-24 ✓ 原生 button 版）
 *
 * ★★★ 为什么从 Radix ToggleGroupItem 换成原生 button：
 *   「Dock 元素：✗ 没找到」⇒ **整个组件在运行时崩溃** ✗
 *   样式类都进产物了 ✓ 说明不是 CSS 问题 ✓ 是渲染期抛错 ✓
 *   最大嫌疑是 `@radix-ui/react-toggle-group@1.1.19` 在 React 19.3 下的组合组件兼容性 ✗
 *   ⇒ 规格里本来就留了退路：「原生 button 组 + role="tablist"」✓
 *   ⇒ 直接走这条路，**彻底绕开 Radix** ✓
 *
 * ★ 无障碍不降级：
 *   role="tablist" + role="tab" + aria-selected + aria-label ✓
 *   键盘方向键由 dock.tsx 统一处理 ✓
 *   焦点环保留 ✓
 */

type DockItemProps = {
  /** aria-label 用的完整名称：Chats / Contacts / Calls / Settings ✓ */
  label: string;
  /** 图标下方短文字：聊天 / 通讯录 / 通话 / 设置 ✓ */
  caption: string;
  badge?: number;
  /** 掠过高亮强度 0~1（长按拖选时按手指距离实时写入 ✓） */
  glow?: number;
  active: boolean;
  /** 重挂载 key = `${activeIndex}-${点击次数}` ⇒ 重复点击也重播动画 ✓ */
  pulseKey: string;
  index: number;
  onClick: (index: number) => void;
  children: React.ReactNode;
};

export const DockItem = React.memo(function DockItem({
  label,
  caption,
  badge = 0,
  glow = 0,
  active,
  pulseKey,
  index,
  onClick,
  children,
}: DockItemProps) {
  const ariaLabel = badge > 0 ? `${label}，${badge} 条未读` : label;

  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      aria-label={ariaLabel}
      data-slot="dock-item"
      data-active={active ? 'true' : 'false'}
      tabIndex={active ? 0 : -1}
      onClick={() => onClick(index)}
      style={{'--glow': String(glow)} as React.CSSProperties}
      className={cn(
        'group relative flex h-full w-1/4 flex-col items-center justify-center gap-[2px]',
        'outline-none focus-visible:ring-2 focus-visible:ring-dock-active/60 focus-visible:ring-offset-0',
        // ★ 三态颜色（2026-10-24）：
        //   未选中 → text-dock-idle（深色白 / 浅色近黑）
        //   悬停   → 提前透出选中蓝的 70% ⇒ 鼠标未点也有"将选中"的预感 ✓
        //   选中   → 满色品牌蓝 ✓
        //   ★ 全部只改 color，**不加任何背景/遮罩** ✓（需求明确要求）
        active ? 'text-dock-active' : 'text-dock-idle hover:text-dock-active/70',
        /* ★★ 2026-10-24 修正：transition **不能包含 transform** ✗
           `@keyframes dock-pop` 改的就是 transform（scale）✓
           而 transition 也管 transform ⇒ 两者抢同一个属性
           ⇒ 重播动画时浏览器先按 transition 插值到 0%，再按 animation 放大
           ⇒ 用户看到"先缩一下再变大"，也就是「大→小」的反向感 ✗
           ⇒ 只保留 color / opacity 的过渡，transform 交给 animation 独占 ✓ */
        'transition-[color,opacity] duration-300 ease-out',
        // ★ 悬停的 translate 也去掉：它同样改 transform ✗
        //   悬停反馈改由颜色表达（70% 蓝）已足够 ✓
      )}
    >
      {/* 图标：key 变化 ⇒ 重挂载 ⇒ 播一次 dock-pop 回弹 ✓ */}
      <span
        key={pulseKey}
        className={cn(
          'relative grid place-items-center',
          active && 'animate-[dock-pop_480ms_cubic-bezier(.34,1.36,.64,1)]',
        )}
      >
        {children}

        {/* 未读徽标：贴图标右上角 ✓ 外圈用 --dock-bg 描边 ⇒ 换主题自动跟随 ✓ */}
        {badge > 0 && (
          <span
            aria-hidden
            className={cn(
              'absolute -top-1 left-1/2 ml-[9px] grid h-[16px] min-w-[16px] place-items-center',
              'rounded-[8px] bg-dock-badge px-[4px]',
              'text-[10px] font-semibold leading-none text-white',
              'shadow-[0_0_0_2px_var(--dock-bg)]',
            )}
          >
            {badge > 99 ? '99+' : badge}
          </span>
        )}
      </span>

      {/* 文字标签：颜色继承父级 ✓ */}
      <span
        key={`t-${pulseKey}`}
        className={cn(
          'text-[11px] leading-none tracking-tight',
          active ? 'font-semibold' : 'font-medium',
          'transition-[font-weight,color] duration-300 ease-out',  // 不含 transform ✓
          // ★ 文字也做一次轻微放大（比图标小，避免抢视觉层次 ✓）
          active && 'animate-[dock-pop-text_480ms_cubic-bezier(.34,1.36,.64,1)]',
        )}
      >
        {caption}
      </span>
    </button>
  );
});
