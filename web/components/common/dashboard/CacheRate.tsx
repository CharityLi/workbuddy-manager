'use client';

import {useEffect, useState} from 'react';
import {Gauge} from 'lucide-react';

import {statsApi} from '@/lib/api';
import type {CacheRate as CacheRateData} from '@/lib/types';
import {cn} from '@/lib/utils';

/**
 * 缓存命中率统计（2026-10-23 新增 ✓）
 *
 * 位置：仪表盘「反代上游」卡片下方（那里原本有一块空白 ✓）
 *
 * 口径：
 *   命中率 = 命中 / (命中 + 未命中)   ⇒ 输入侧 ✓ 不含输出 ✓
 *   「输出 tokens」是独立指标，不进分母 ✓（它是模型的生成量，与缓存无关）
 *
 * 数据源：**网关的 request-logs JSONL** ✗ 不是自己的库
 *   原因：`usage_daily` 没有缓存列 ✗；`request_logs` 只记 manager 自己的请求 ✗
 *   网关每条记录都带 cache_hit_tokens / cache_miss_tokens ✓
 *
 * ★ 环形图用原生 SVG，不引图表库：一个圆环不值得多 30KB ✗
 *   原理：circle 的 strokeDasharray = 弧长 周长，配合 rotate(-90) 从 12 点开始 ✓
 */

const R = 34;                     // 半径
const CIRC = 2 * Math.PI * R;     // 周长

export function CacheRate({className}: {className?: string}) {
  const [data, setData] = useState<CacheRateData | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () => {
      statsApi
        .cacheRate(1)
        .then((d) => {
          if (!alive) return;
          setData(d);
          setFailed(false);
        })
        .catch(() => {
          if (alive) setFailed(true);
        });
    };
    load();
    // 页面本身 30 秒刷一次，这块以前只在挂载时取一次 ⇒ 跨零点或换天后
    // 一直显示旧数字，看着像「不跟随每日更新」。
    const timer = setInterval(load, 30000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  // 加载中 / 失败 / 无文件：都渲染「—」，不占位不闪烁 ✓
  const rate = data?.rate ?? 0;
  const ready = !!data?.found && (data.hit + data.miss) > 0;

  const items = [
    {label: '命中 tokens', value: data?.hit ?? 0, dot: 'bg-emerald-500', show: true},
    {label: '未命中 tokens', value: data?.miss ?? 0, dot: 'bg-amber-500', show: true},
    {label: '总输出 tokens', value: data?.output ?? 0, dot: 'bg-blue-500', show: true},
  ];

  return (
    <div className={cn('mt-4 border-t border-border/60 pt-4', className)}>
      <div className="mb-3 flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Gauge className="h-4 w-4" />
          命中率统计
        </div>
      </div>

      <div className="flex items-center gap-4">
        {/* 环形图 */}
        <div className="relative h-[88px] w-[88px] shrink-0">
          <svg viewBox="0 0 88 88" className="h-full w-full -rotate-90">
            <circle
              cx="44" cy="44" r={R}
              fill="none"
              stroke="var(--muted)"
              strokeWidth="10"
            />
            {ready && (
              <circle
                cx="44" cy="44" r={R}
                fill="none"
                stroke="var(--chart-1)"
                strokeWidth="10"
                strokeLinecap="round"
                strokeDasharray={`${(CIRC * rate) / 100} ${CIRC}`}
                className="transition-[stroke-dasharray] duration-500"
              />
            )}
          </svg>
          <div className="absolute inset-0 grid place-items-center">
            <span className="text-lg font-semibold tabular-nums">
              {ready ? `${rate.toFixed(1)}%` : '—'}
            </span>
          </div>
        </div>

        {/* 三项明细 */}
        <div className="min-w-0 flex-1 space-y-1.5">
          {items.map((it) => (
            <div key={it.label} className="flex items-center gap-2 text-[12px]">
              <span className={cn('h-2 w-2 shrink-0 rounded-full', it.dot)} />
              <span className="truncate text-muted-foreground">{it.label}</span>
              <span className="ml-auto shrink-0 font-medium tabular-nums">
                {ready ? fmtCompact(it.value) : '—'}
              </span>
            </div>
          ))}
        </div>
      </div>

      {failed && (
        <div className="mt-2 text-[11px] text-muted-foreground">
          统计接口不可用（后端未重启？）
        </div>
      )}
    </div>
  );
}

/** 紧凑数字：144.2M / 8.36M / 270.8K ✓ */
function fmtCompact(n: number): string {
  if (!Number.isFinite(n)) return '—';
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return String(n);
}
