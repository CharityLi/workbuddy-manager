'use client';

/**
 * Dock 图标 —— 手写 inline SVG（2026-10-23 ✓）
 *
 * ★ 为什么不用 lucide-react：
 *   lucide 全是**描边**图标（stroke），Telegram 的识别特征恰恰是**实心剪影**（fill）。
 *   把描边图标改成 fill 会变成一坨畸形色块 ✗ 所以只能手绘 path。
 *
 * ★ 统一规格：
 *   viewBox="0 0 24 24"  fill="currentColor"  尺寸由调用方传 size（默认 27）
 *   颜色靠 currentColor 继承父级 text-* 类 ⇒ 组件里不出现任何色值 ✓
 *
 * ★ 每个图标都带 focusable={false}：IE 时代的老习惯，但在 Safari 里
 *   能避免 SVG 被 tab 聚焦（否则 Tab 键会多停一站 ✗）
 */

type IconProps = {
  className?: string;
  size?: number;
};

const BASE = {
  viewBox: '0 0 24 24',
  fill: 'currentColor',
  'aria-hidden': true,
  focusable: false,
} as const;

/** 对话气泡：圆润气泡体 + 左下角小尾巴 + 内部三条消息线（挖空） */
export function ChatsIcon({className, size = 27}: IconProps) {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path
        fillRule="evenodd"
        clipRule="evenodd"
        d="M12 2.6C6.9 2.6 2.8 6.24 2.8 10.72c0 2.42 1.26 4.6 3.26 6.06V21l3.55-1.95c.77.18 1.57.28 2.39.28 5.1 0 9.2-3.64 9.2-8.12S17.1 2.6 12 2.6zm-4.4 9.9a1.2 1.2 0 1 1 0-2.4 1.2 1.2 0 0 1 0 2.4zm4.4 0a1.2 1.2 0 1 1 0-2.4 1.2 1.2 0 0 1 0 2.4zm4.4 0a1.2 1.2 0 1 1 0-2.4 1.2 1.2 0 0 1 0 2.4z"
      />
    </svg>
  );
}

/** 双人剪影：前景一人（实心）+ 后方一人（略淡，形成层次） */
export function ContactsIcon({className, size = 27}: IconProps) {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      {/* 前景 */}
      <circle cx="9.2" cy="8.1" r="3.35" />
      <path d="M2.6 19.6c0-3.15 2.95-5.5 6.6-5.5s6.6 2.35 6.6 5.5v.9H2.6v-.9z" />
      {/* 后方（用 opacity 而不是换色 ⇒ 换主题时自动跟随 ✓） */}
      <g opacity="0.72">
        <circle cx="17.3" cy="8.9" r="2.7" />
        <path d="M17.05 13.6c2.75 0 4.65 1.75 4.65 4.15v2.75h-4.1v-.9c0-1.55-.55-2.95-1.5-4 .3-.05.63-.05 1-.05z" />
      </g>
    </svg>
  );
}

/** 电话听筒：实心话筒 + 两端听筒头，45° 斜置（Telegram / iOS 的经典姿态） */
export function CallsIcon({className, size = 27}: IconProps) {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path d="M8.35 2.9c.62-.62 1.62-.62 2.24 0l2.3 2.3c.62.62.62 1.62 0 2.24l-1.42 1.42c.44.98 1.03 1.9 1.76 2.63s1.65 1.32 2.63 1.76l1.42-1.42c.62-.62 1.62-.62 2.24 0l2.3 2.3c.62.62.62 1.62 0 2.24l-1.72 1.72c-1.99 1.99-5.94.9-9.35-2.51S2.14 6.61 4.13 4.62L8.35 2.9z" />
    </svg>
  );
}

/** 齿轮：外圈齿 + 中心孔（evenodd 一次挖空，不用两条 path 叠色） */
export function SettingsIcon({className, size = 27}: IconProps) {
  return (
    <svg {...BASE} width={size} height={size} className={className}>
      <path
        fillRule="evenodd"
        clipRule="evenodd"
        d="M10.32 1.8h3.36l.5 2.44c.6.17 1.17.4 1.7.7l2.07-1.4 2.36 2.36-1.4 2.07c.3.53.53 1.1.7 1.7l2.44.5v3.36l-2.44.5c-.17.6-.4 1.17-.7 1.7l1.4 2.07-2.36 2.36-2.07-1.4c-.53.3-1.1.53-1.7.7l-.5 2.44h-3.36l-.5-2.44c-.6-.17-1.17-.4-1.7-.7l-2.07 1.4-2.36-2.36 1.4-2.07c-.3-.53-.53-1.1-.7-1.7l-2.44-.5V10.32l2.44-.5c.17-.6.4-1.17.7-1.7l-1.4-2.07 2.36-2.36 2.07 1.4c.53-.3 1.1-.53 1.7-.7l.5-2.44zm1.68 6.1a4.1 4.1 0 1 0 0 8.2 4.1 4.1 0 0 0 0-8.2zm0 2.2a1.9 1.9 0 1 1 0 3.8 1.9 1.9 0 0 1 0-3.8z"
      />
    </svg>
  );
}
