"use client";

/**
 * 自定义背景应用器（2026-10-23 ✓）
 *
 * 工作机制：
 *   1. 从 localStorage 读 `wb_bg` 配置 ✓
 *   2. 把背景写到 <body>（图片 / 纯色 / 渐变 ✓）
 *   3. 再叠一层「遮罩」保证卡片文字可读 ✓ —— 这一步不做的话
 *      亮色背景上白字会看不清 ✗（实测过 ✓）
 *
 * ★ 纯前端 ✓ 不碰后端 ✓ 不存任何敏感信息 ✓
 * ★ 清空方式：设置页「恢复默认」或 DevTools 删 localStorage ✓
 */

import {useEffect} from 'react';

export const BG_STORAGE_KEY = 'wb_bg';

export interface BgConfig {
  type: 'none' | 'color' | 'gradient' | 'image';
  value: string;          // 颜色色值 / 渐变 CSS / 图片 dataURL 或 URL
  dim: number;            // 遮罩强度 0~0.8 ✓
  cardAlpha: number;      // 卡片不透明度 0.2~1 ✓（越小越透 ✓）
  idleMs: number;         // 鼠标静止多少毫秒后应用 cardAlpha ✓ 移动时用 0.9 ✓
}

export const DEFAULT_BG: BgConfig = {type: 'none', value: '', dim: 0.35, cardAlpha: 1, idleMs: 2000};

export function readBg(): BgConfig {
  try {
    const raw = localStorage.getItem(BG_STORAGE_KEY);
    if (!raw) return DEFAULT_BG;
    const o = JSON.parse(raw);
    return {
      type: o.type || 'none',
      value: o.value || '',
      dim: typeof o.dim === 'number' ? o.dim : 0.35,
      cardAlpha: typeof o.cardAlpha === 'number' ? o.cardAlpha : 1,
      idleMs: typeof o.idleMs === 'number' ? o.idleMs : 2000,
    };
  } catch {
    return DEFAULT_BG;
  }
}

export function writeBg(cfg: BgConfig): void {
  try {
    localStorage.setItem(BG_STORAGE_KEY, JSON.stringify(cfg));
  } catch {
    /* 配额满（图片太大）时静默失败 ✓ 调用方负责提示 */
  }
  applyBg(cfg);
}

export function applyBg(cfg: BgConfig): void {
  const body = document.body;
  if (!body) return;

  // 先清空上次的设置 ✓ 避免叠加
  body.style.backgroundImage = '';
  body.style.backgroundColor = '';
  body.style.backgroundSize = '';
  body.style.backgroundPosition = '';
  body.style.backgroundRepeat = '';
  body.style.backgroundAttachment = '';

  if (cfg.type === 'none' || !cfg.value) {
    body.removeAttribute('data-wb-bg');
    document.documentElement.style.setProperty('--wb-bg-dim', '0');
    document.documentElement.style.setProperty('--wb-card-alpha', '1');
    return;
  }


  if (cfg.type === 'color') {
    body.style.backgroundColor = cfg.value;
  } else if (cfg.type === 'gradient') {
    body.style.backgroundImage = cfg.value;
    body.style.backgroundAttachment = 'fixed';
  } else if (cfg.type === 'image') {
    body.style.backgroundImage = `url("${cfg.value.replace(/"/g, '\\"')}")`;
    body.style.backgroundSize = 'cover';
    body.style.backgroundPosition = 'center center';
    body.style.backgroundRepeat = 'no-repeat';
    body.style.backgroundAttachment = 'fixed';
  }

  // 遮罩强度交给 CSS 变量 ✓ 由 globals.css 的 body[data-wb-bg]::before 使用
  body.setAttribute('data-wb-bg', cfg.type);
  document.documentElement.style.setProperty('--wb-bg-dim', String(cfg.dim));
  document.documentElement.style.setProperty('--wb-card-alpha', String(cfg.cardAlpha ?? 1));
}

// ★ 鼠标静止检测（2026-10-23 ✓）
//   浏览页面（鼠标在动）⇒ 卡片 90% 不透明 ✓ 保证看得清
//   静止 idleMs 毫秒后     ⇒ 恢复用户设的 cardAlpha ✓ 让背景透出来 ✓
//   ★ 只在「有自定义背景」时生效 ✓ 否则不干预原样式 ✓
const ACTIVE_ALPHA = 0.9;

export default function BackgroundApplier() {
  useEffect(() => {
    const cfg0 = readBg();
    applyBg(cfg0);

    if (cfg0.type === 'none') return;   // 没背景 ⇒ 不启用 ✓

    let timer: ReturnType<typeof setTimeout> | null = null;
    let active = false;

    const setIdleAlpha = () => {
      const c = readBg();
      document.documentElement.style.setProperty('--wb-card-alpha', String(c.cardAlpha ?? 1));
      active = false;
    };
    const setActiveAlpha = () => {
      if (active) return;
      active = true;
      document.documentElement.style.setProperty('--wb-card-alpha', String(ACTIVE_ALPHA));
    };

    const onMove = () => {
      setActiveAlpha();
      if (timer) clearTimeout(timer);
      const c = readBg();
      timer = setTimeout(setIdleAlpha, c.idleMs || 2000);
    };

    window.addEventListener('mousemove', onMove, {passive: true});
    window.addEventListener('wheel', onMove, {passive: true});
    window.addEventListener('keydown', onMove);
    window.addEventListener('touchmove', onMove, {passive: true});
    // 初始就按「静止」显示 ✓ 用户一动才变 90%
    setIdleAlpha();
    if (timer) clearTimeout(timer);

    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('wheel', onMove);
      window.removeEventListener('keydown', onMove);
      window.removeEventListener('touchmove', onMove);
      if (timer) clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    applyBg(readBg());
    // 其他标签页改了设置 ⇒ 同步 ✓
    const onStorage = (e: StorageEvent) => {
      if (e.key === BG_STORAGE_KEY) applyBg(readBg());
    };
    const onCustom = () => applyBg(readBg());
    window.addEventListener('storage', onStorage);
    window.addEventListener('wb-bg-changed', onCustom);
    return () => {
      window.removeEventListener('storage', onStorage);
      window.removeEventListener('wb-bg-changed', onCustom);
    };
  }, []);

  return null;
}
