'use client';

import {useCallback, useEffect, useState} from 'react';

import {watchApi} from '@/lib/api';
import {fmtDateTime} from '@/lib/format';
import type {WatchEvent, WatchPage} from '@/lib/types';

const KIND_LABEL: Record<string, string> = {
  body: '页面内容变了',
  asset: '前端资源变了（疑似发版）',
  error: '抓取失败',
};

export default function WatchPageView() {
  const [pages, setPages] = useState<WatchPage[]>([]);
  const [events, setEvents] = useState<WatchEvent[]>([]);
  const [url, setUrl] = useState('');
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const [p, e] = await Promise.all([watchApi.pages(), watchApi.events(undefined, 30)]);
      setPages(p.items);
      setEvents(e.items);
      setError('');
    } catch {
      setError('加载失败');
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const add = async () => {
    if (!url.trim()) return;
    setBusy(true);
    try {
      await watchApi.add({url: url.trim(), name: name.trim(), interval_min: 30});
      setUrl('');
      setName('');
      await load();
    } catch {
      setError('添加失败：地址需要以 http:// 或 https:// 开头');
    } finally {
      setBusy(false);
    }
  };

  const run = async (id: number) => {
    setBusy(true);
    try {
      await watchApi.checkNow(id);
      await load();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <section className="rounded-[20px] bg-muted p-4">
        <h2 className="mb-1 text-sm font-medium">页面监控</h2>
        <p className="mb-3 text-xs text-muted-foreground">
          定时抓取指定页面，比对正文与前端的 js/css 指纹。首次抓取只记基线，
          之后只有变化才会留下一条事件 —— 没有变化就什么都不写。
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <input
            className="h-9 min-w-[240px] flex-1 rounded-md border bg-background px-3 text-sm"
            placeholder="https://www.codebuddy.cn/"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
          <input
            className="h-9 w-[160px] rounded-md border bg-background px-3 text-sm"
            placeholder="备注名（可选）"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <button
            type="button"
            disabled={busy}
            onClick={() => void add()}
            className="h-9 rounded-md bg-primary px-4 text-sm text-primary-foreground disabled:opacity-50">
            添加
          </button>
        </div>
        {error ? <p className="mt-2 text-xs text-destructive">{error}</p> : null}
      </section>

      <section className="rounded-[20px] bg-muted p-4">
        <h2 className="mb-3 text-sm font-medium">监控列表（{pages.length}）</h2>
        {pages.length === 0 ? (
          <p className="text-xs text-muted-foreground">还没有页面。加一个地址后点「立即抓取」先存基线。</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {pages.map((p) => (
              <li key={p.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg bg-background p-3 text-sm">
                <span className="font-medium">{p.name || p.url}</span>
                <span className="truncate text-xs text-muted-foreground">{p.url}</span>
                <span className="text-xs text-muted-foreground">每 {p.interval_min} 分钟</span>
                <span className="text-xs text-muted-foreground">
                  {p.last_check ? `上次 ${fmtDateTime(p.last_check)}` : '尚未抓取'}
                </span>
                {p.last_error ? (
                  <span className="text-xs text-destructive">{p.last_error.slice(0, 60)}</span>
                ) : p.last_check ? (
                  <span className="text-xs text-emerald-600">正常</span>
                ) : null}
                <span className="ml-auto flex gap-2">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void run(p.id)}
                    className="rounded-md border px-2 py-1 text-xs disabled:opacity-50">
                    立即抓取
                  </button>
                  <button
                    type="button"
                    onClick={() => void watchApi.toggle(p.id, !p.enabled).then(load)}
                    className="rounded-md border px-2 py-1 text-xs">
                    {p.enabled ? '暂停' : '启用'}
                  </button>
                  <button
                    type="button"
                    onClick={() => void watchApi.remove(p.id).then(load)}
                    className="rounded-md border px-2 py-1 text-xs text-destructive">
                    删除
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-[20px] bg-muted p-4">
        <h2 className="mb-3 text-sm font-medium">变化记录</h2>
        {events.length === 0 ? (
          <p className="text-xs text-muted-foreground">暂无变化。</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {events.map((e) => (
              <li key={e.id} className="rounded-lg bg-background p-3 text-sm">
                <div className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
                  <span>{fmtDateTime(e.ts)}</span>
                  <span>·</span>
                  <span>{pages.find((p) => p.id === e.page_id)?.name ?? `#${e.page_id}`}</span>
                  <span>·</span>
                  <span className={e.kind === 'error' ? 'text-destructive' : 'text-amber-600'}>
                    {KIND_LABEL[e.kind] ?? e.kind}
                  </span>
                </div>
                <p className="mt-1">{e.summary}</p>
                {e.detail ? <p className="mt-1 whitespace-pre-wrap text-xs text-muted-foreground">{e.detail}</p> : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
