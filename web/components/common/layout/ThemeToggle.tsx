'use client';

import {useEffect, useState} from 'react';
import {Monitor, Moon, Sun} from 'lucide-react';
import {AnimatePresence, motion} from 'motion/react';
import {useTheme} from 'next-themes';

import {cn} from '@/lib/utils';

/**
 * 主题切换（三态合一，2026-10-23 ✓）
 *
 * 形态：**一个按钮**，点一下循环切换
 *   light → dark → system → light …
 *
 * 为什么不做成三段 pill（像 RealmToggle 那样）：
 *   ① 用户明确要求「三个按钮融为一个，点击切换」✓
 *   ② 右上角已经有 ⌘K / 语言 / 版本三个控件，再加一段三段组会挤
 *      （移动端尤其明显 ✓）
 *
 * 图标对应：Sun=浅色 ✓ Moon=深色 ✓ Monitor=跟随系统 ✓
 *
 * ★ hydration 注意：`resolvedTheme` 在服务端拿不到 ✗
 *   next-themes 的 `theme` 在挂载前是 undefined ✓
 *   ⇒ 必须等 mounted 再渲染真实图标 ✓ 否则 SSR/CSR 不一致会报 hydration 错 ✓
 *   这里用 mounted 挡住首次渲染 ✓ 先给一个同尺寸占位 ✓ 避免布局跳动 ✓
 */

type Mode = 'light' | 'dark' | 'system';

const ORDER: Mode[] = ['light', 'dark', 'system'];

const META: Record<Mode, {icon: typeof Sun; label: string; title: string}> = {
  light: {icon: Sun, label: '浅色', title: '浅色主题（点击切换到深色）'},
  dark: {icon: Moon, label: '深色', title: '深色主题（点击切换到跟随系统）'},
  system: {icon: Monitor, label: '跟随系统', title: '跟随系统（点击切换到浅色）'},
};

export function ThemeToggle({compact = false}: {compact?: boolean}) {
  const {theme, setTheme} = useTheme();
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  // 挂载前不知道真实主题 ⇒ 渲染一个不可点的占位 ✓ 尺寸与真实按钮一致 ✓
  if (!mounted) {
    return (
      <div
        className="inline-flex h-[26px] w-[26px] items-center justify-center rounded-full border border-border/60 bg-muted/60"
        aria-hidden
      />
    );
  }

  const current = (ORDER.includes(theme as Mode) ? theme : 'system') as Mode;
  const next = ORDER[(ORDER.indexOf(current) + 1) % ORDER.length];
  const {icon: Icon, label, title} = META[current];

  return (
    <button
      type="button"
      onClick={() => setTheme(next)}
      title={title}
      aria-label={`当前：${label}。${title}`}
      className={cn(
        'group relative inline-flex items-center gap-1.5 rounded-full border border-border/60',
        'bg-muted/60 px-2 py-[3px] text-[11px] font-medium text-muted-foreground',
        'transition-colors hover:text-foreground',
        // 与 RealmToggle 的视觉高度对齐（后者 pill 高约 26px ✓）
        compact && 'px-1.5',
      )}
    >
      <span className="relative grid h-3.5 w-3.5 place-items-center">
        <AnimatePresence mode="wait" initial={false}>
          <motion.span
            key={current}
            initial={{opacity: 0, rotate: -45, scale: 0.7}}
            animate={{opacity: 1, rotate: 0, scale: 1}}
            exit={{opacity: 0, rotate: 45, scale: 0.7}}
            transition={{duration: 0.18, ease: 'easeOut'}}
            className="absolute inset-0 grid place-items-center"
          >
            <Icon className="h-3.5 w-3.5" />
          </motion.span>
        </AnimatePresence>
      </span>
      {!compact && <span className="hidden sm:inline">{label}</span>}
      {compact && <span className="sr-only">{label}</span>}
    </button>
  );
}
