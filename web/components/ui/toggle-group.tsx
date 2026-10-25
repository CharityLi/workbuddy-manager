'use client';

import * as ToggleGroupPrimitive from '@radix-ui/react-toggle-group';
import * as React from 'react';

/**
 * ToggleGroup 的最小包装（2026-10-23 ✓）
 *
 * ★ 为什么不用 shadcn 官方的 toggle-group：
 *   官方版带 `variant` / `size` 两套 cva 样式（bg-transparent / data-[state=on]:bg-accent …）
 *   ⇒ 会和 Dock 自己的视觉打架 ✗ 本节要求"视觉全部自己写"✓
 *   ⇒ 这里只保留 Radix 的**无障碍语义**（role=radiogroup / 方向键 / roving tabindex）✓
 *     不给任何背景、边框、圆角 ✓ 样式由调用方的 className 决定 ✓
 *
 * ★ 为什么用 ToggleGroup 而不是 Tabs：
 *   Tabs 会自己管理 tabpanel 与 aria-controls，而 Dock 的"内容区"是**页面路由**、
 *   不在同一个 React 树里 ✗ 用 Tabs 会产生悬空的 aria-controls（读屏报错 ✗）
 *   ToggleGroup 只表达"一组单选按钮"✓ 语义正确且没有这个包袱 ✓
 */

const ToggleGroup = React.forwardRef<
  React.ComponentRef<typeof ToggleGroupPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof ToggleGroupPrimitive.Root>
>(({className, children, ...props}, ref) => (
  <ToggleGroupPrimitive.Root
    ref={ref}
    className={className}
    {...props}
  >
    {children}
  </ToggleGroupPrimitive.Root>
));
ToggleGroup.displayName = 'ToggleGroup';

const ToggleGroupItem = React.forwardRef<
  React.ComponentRef<typeof ToggleGroupPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof ToggleGroupPrimitive.Item>
>(({className, children, ...props}, ref) => (
  // ★ 不传 variant/size、不加任何默认样式 ⇒ 只有调用方给的 className ✓
  <ToggleGroupPrimitive.Item
    ref={ref}
    className={className}
    {...props}
  >
    {children}
  </ToggleGroupPrimitive.Item>
));
ToggleGroupItem.displayName = 'ToggleGroupItem';

export {ToggleGroup, ToggleGroupItem};
