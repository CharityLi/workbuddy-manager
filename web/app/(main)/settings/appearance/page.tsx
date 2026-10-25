"use client";

import {useEffect, useRef, useState} from 'react';
import {DEFAULT_BG, readBg, writeBg, type BgConfig} from '@/components/common/BackgroundApplier';

/**
 * 设置 → 外观（2026-10-23 新增 ✓）
 *
 * 能力：默认 / 纯色 / 渐变（6 个预设）/ 图片（URL 或本地文件 ✓）
 * 存储：localStorage（本机 ✓ 不同步 ✗）
 * ★ 图片会转 base64 存本地 ⇒ 建议先压缩到 2MB 以内 ✓
 *   （超过 localStorage 5MB 配额会存不进去 ⇒ 页面有提示 ✓）
 */

const GRADIENTS: {name: string; css: string}[] = [
  {name: '夜色', css: 'linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #0f172a 100%)'},
  {name: '深海', css: 'linear-gradient(135deg, #042f2e 0%, #134e4a 50%, #042f2e 100%)'},
  {name: '晨雾', css: 'linear-gradient(135deg, #e2e8f0 0%, #f8fafc 50%, #e2e8f0 100%)'},
  {name: '暖阳', css: 'linear-gradient(135deg, #7c2d12 0%, #c2410c 50%, #7c2d12 100%)'},
  {name: '森林', css: 'linear-gradient(135deg, #14532d 0%, #166534 50%, #14532d 100%)'},
  {name: '紫霞', css: 'linear-gradient(135deg, #3b0764 0%, #6b21a8 50%, #3b0764 100%)'},
];

export default function AppearancePage() {
  const [cfg, setCfg] = useState<BgConfig>(DEFAULT_BG);
  const [msg, setMsg] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setCfg(readBg());
  }, []);

  function save(next: BgConfig) {
    setCfg(next);
    writeBg(next);
    window.dispatchEvent(new Event('wb-bg-changed'));
    setMsg('已保存 ✓');
    setTimeout(() => setMsg(''), 1800);
  }

  function onPickFile(file: File | null) {
    if (!file) return;
    if (!file.type.startsWith('image/')) {
      setMsg('请选择图片文件 ✗');
      return;
    }
    if (file.size > 3 * 1024 * 1024) {
      setMsg('图片超过 3MB，请先压缩 ✗');
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const b64 = String(reader.result || '');
      const next: BgConfig = {...cfg, type: 'image', value: b64};
      try {
        writeBg(next);
        setCfg(next);
        window.dispatchEvent(new Event('wb-bg-changed'));
        setMsg('图片已应用 ✓');
      } catch {
        setMsg('存不进去（图片太大）✗ 换小一点的');
      }
    };
    reader.readAsDataURL(file);
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-medium">外观 · 自定义背景</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          背景只保存在<b>本机浏览器</b>（localStorage），不占用服务器空间，也不会同步到其他设备。
        </p>
      </div>

      {msg && <div className="rounded-xl bg-muted px-4 py-2 text-sm">{msg}</div>}

      {/* 默认 */}
      <section className="rounded-2xl bg-card p-4">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <div className="text-sm font-medium">默认背景</div>
            <div className="text-xs text-muted-foreground">跟随亮/暗主题</div>
          </div>
          <button
            className="rounded-lg border px-3 py-1.5 text-sm"
            onClick={() => save(DEFAULT_BG)}
          >使用默认</button>
        </div>
      </section>

      {/* 纯色 */}
      <section className="rounded-2xl bg-card p-4">
        <div className="mb-3 text-sm font-medium">纯色</div>
        <div className="flex items-center gap-3">
          <input
            type="color"
            value={/^#[0-9a-f]{6}$/i.test(cfg.value) ? cfg.value : '#1e293b'}
            onChange={(e) => save({...cfg, type: 'color', value: e.target.value})}
            className="h-9 w-16 cursor-pointer rounded border bg-transparent"
          />
          <span className="font-mono text-xs text-muted-foreground">{cfg.type === 'color' ? cfg.value : '未设置'}</span>
        </div>
      </section>

      {/* 渐变 */}
      <section className="rounded-2xl bg-card p-4">
        <div className="mb-3 text-sm font-medium">渐变预设</div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {GRADIENTS.map((g) => (
            <button
              key={g.name}
              onClick={() => save({...cfg, type: 'gradient', value: g.css})}
              className={`group overflow-hidden rounded-xl border transition ${
                cfg.type === 'gradient' && cfg.value === g.css ? 'ring-2 ring-primary' : ''
              }`}
            >
              <div className="h-16 w-full" style={{backgroundImage: g.css}} />
              <div className="px-2 py-1.5 text-xs">{g.name}</div>
            </button>
          ))}
        </div>
      </section>

      {/* 图片 */}
      <section className="rounded-2xl bg-card p-4">
        <div className="mb-3 text-sm font-medium">图片</div>
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="text"
              placeholder="粘贴图片 URL（https://…）"
              defaultValue={cfg.type === 'image' && !cfg.value.startsWith('data:') ? cfg.value : ''}
              onBlur={(e) => {
                const v = e.target.value.trim();
                if (v) save({...cfg, type: 'image', value: v});
              }}
              className="min-w-[240px] flex-1 rounded-lg border bg-background px-3 py-1.5 text-sm"
            />
            <button
              className="rounded-lg border px-3 py-1.5 text-sm"
              onClick={() => fileRef.current?.click()}
            >选择本地图片</button>
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => onPickFile(e.target.files?.[0] || null)}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            <b>支持 GIF 动图</b> ✓（当作图片处理，会自动播放）。
          </p>
          <div className="rounded-xl bg-muted/60 p-3 text-xs leading-relaxed">
            <div className="mb-1 font-medium">大文件（&gt; 3MB，比如 30MB 的 GIF）怎么用？</div>
            <div className="text-muted-foreground">
              localStorage 只能存 5MB ✗ 所以大图/大 GIF 必须走<b>本地文件路径</b>：
              <pre className="mt-1 whitespace-pre-wrap font-mono text-[11px]">{`1. 把文件放到：~/apps/workbuddy-manager/web/public/
   例如：  ~/apps/workbuddy-manager/web/public/bg.gif
2. 上面输入框里填：  /bg.gif
3. 失焦即生效（不需要重启，public 是静态目录）`}</pre>
            </div>
          </div>
        </div>
      </section>

      {/* 卡片透明度 */}
      <section className="rounded-2xl bg-card p-4">
        <div className="mb-2 flex items-center justify-between">
          <div className="text-sm font-medium">卡片透明度</div>
          <span className="font-mono text-xs text-muted-foreground">
            {Math.round((cfg.cardAlpha ?? 1) * 100)}%
          </span>
        </div>
        <input
          type="range"
          min={20}
          max={100}
          value={Math.round((cfg.cardAlpha ?? 1) * 100)}
          onChange={(e) => save({...cfg, cardAlpha: Number(e.target.value) / 100})}
          className="w-full"
        />
        <p className="mt-1 text-xs text-muted-foreground">
          觉得卡片挡住背景就把这个调小 ✓（<b>25%~40%</b> 效果最明显，再低字会糊）。
          卡片自带毛玻璃模糊 ✓ 背景透过来但不影响阅读。
        </p>
      </section>

      {/* 静止延迟 */}
      <section className="rounded-2xl bg-card p-4">
        <div className="mb-2 flex items-center justify-between">
          <div className="text-sm font-medium">鼠标静止后恢复透明延迟</div>
          <span className="font-mono text-xs text-muted-foreground">
            {((cfg.idleMs ?? 2000) / 1000).toFixed(1)}s
          </span>
        </div>
        <input
          type="range"
          min={500}
          max={10000}
          step={500}
          value={cfg.idleMs ?? 2000}
          onChange={(e) => save({...cfg, idleMs: Number(e.target.value)})}
          className="w-full"
        />
        <p className="mt-1 text-xs text-muted-foreground">
          <b>鼠标移动时卡片为 90% 不透明</b>（看得清）；<b>静止这么久后</b>恢复你设的透明度（背景透出来）。
        </p>
      </section>

      {/* 遮罩 */}
      <section className="rounded-2xl bg-card p-4">
        <div className="mb-2 flex items-center justify-between">
          <div className="text-sm font-medium">遮罩强度</div>
          <span className="font-mono text-xs text-muted-foreground">{Math.round(cfg.dim * 100)}%</span>
        </div>
        <input
          type="range"
          min={0}
          max={80}
          value={Math.round(cfg.dim * 100)}
          onChange={(e) => save({...cfg, dim: Number(e.target.value) / 100})}
          className="w-full"
        />
        <p className="mt-1 text-xs text-muted-foreground">
          背景偏亮时调高，保证卡片上的文字看得清。
        </p>
      </section>
    </div>
  );
}
