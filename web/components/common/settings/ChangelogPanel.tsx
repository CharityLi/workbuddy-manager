'use client';

import {useEffect, useState} from 'react';
import {
  ChevronRight,
  FileText,
  GitBranch,
  Loader2,
  Package,
  RefreshCw,
  TriangleAlert,
} from 'lucide-react';

import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {systemApi} from '@/lib/api';
import {fmtDateTime} from '@/lib/format';
import {RichText} from '@/lib/i18n/rich-text';
import {useT} from '@/lib/i18n/provider';
import {cn} from '@/lib/utils';
import type {Changelog, ChangelogItem, UpstreamLog} from '@/lib/types';

const REPO_URL = 'https://github.com/ithtelab/workbuddy-manager/releases';

/** 分类配色，让「安全」「修复」这类一眼可辨 */
const SECTION_STYLE: Record<string, string> = {
  安全: 'border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-400',
  新增: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400',
  修复: 'border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400',
  改进: 'border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400',
  说明: 'border-border bg-muted text-muted-foreground',
  计划中: 'border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-400',
};

/**
 * 分类标题 → i18n 键。
 *
 * 分类名来自服务端解析 CHANGELOG.md 的小标题（固定的几个），属**数据**；
 * 按原值映射到译文。条目正文仍按仓库里写的中文原样展示——那是文档内容，
 * 翻译它属于改 CHANGELOG.md 本身，不在界面适配范围内。
 */
const SECTION_LABEL_KEYS: Record<string, string> = {
  安全: 'changelog.sectionSecurity',
  新增: 'changelog.sectionAdded',
  修复: 'changelog.sectionFixed',
  改进: 'changelog.sectionImproved',
  说明: 'changelog.sectionNotes',
  计划中: 'changelog.sectionPlanned',
};

function sectionLabel(title: string, t: (key: string) => string): string {
  const key = SECTION_LABEL_KEYS[title];
  return key ? t(key) : title;
}

/**
 * 轻量行内渲染：只处理 `**加粗**` 与 `` `代码` ``，不引入 markdown 依赖。
 * 更新日志格式固定，用不着完整解析器——少一个依赖，离线包也更小。
 */
function InlineText({text}: {text: string}) {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean);
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return (
            <strong key={i} className="font-medium text-foreground">
              {part.slice(2, -2)}
            </strong>
          );
        }
        if (part.startsWith('`') && part.endsWith('`')) {
          return (
            <code key={i} className="rounded bg-muted px-1 py-0.5 font-mono text-[11px]">
              {part.slice(1, -1)}
            </code>
          );
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}

function ItemList({items}: {items: ChangelogItem[]}) {
  const hasSub = items.some((it) => it.level > 0);
  return (
    <ul className={cn('space-y-1.5', hasSub && 'space-y-2')}>
      {items.map((it, i) => (
        <li
          key={i}
          className={cn(
            'flex gap-2 text-xs leading-5 text-muted-foreground',
            it.level > 0 && 'ml-3.5 border-l border-border pl-2.5',
          )}
        >
          <span
            className={cn(
              'mt-[7px] h-1 w-1 shrink-0 rounded-full',
              it.level > 0 ? 'bg-muted-foreground/40' : 'bg-muted-foreground/70',
            )}
          />
          <span className="min-w-0 flex-1 break-words">
            <InlineText text={it.text} />
          </span>
        </li>
      ))}
    </ul>
  );
}

function VersionBlock({
  index,
  version,
  label,
  date,
  unreleased,
  isCurrent,
  currentVersion,
  expanded,
  onToggle,
  sections,
  showSeeAll = true,
}: {
  index: number;
  version: string;
  /** 左上角那行文字；缺省是 `v<version>`（上游日志传原文，避免出现「v上游 …」） */
  label?: string;
  date: string;
  unreleased: boolean;
  isCurrent: boolean;
  currentVersion: string;
  expanded: boolean;
  onToggle: () => void;
  sections: Changelog['versions'][number]['sections'];
  /** 本地日志底部有「全部版本见 Releases」；上游流水没有 Release，要关掉 */
  showSeeAll?: boolean;
}) {
  const t = useT();
  const count = sections.reduce((n, s) => n + s.items.length, 0);
  // 当前版本置顶时也默认展开，方便一眼看到最新改了什么
  return (
    <div
      className={cn(
        'overflow-hidden rounded-[16px] border transition-colors',
        expanded ? 'border-border bg-background' : 'border-transparent bg-muted/50',
      )}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left transition-colors hover:bg-muted/60"
      >
        <ChevronRight
          className={cn(
            'h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform',
            expanded && 'rotate-90',
          )}
        />
        <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-mono text-xs font-medium">
            {label ?? (unreleased ? t('changelog.unreleased') : `v${version}`)}
          </span>
          {isCurrent && !unreleased && (
            <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
              {t('changelog.currentVersion')}
            </Badge>
          )}
          {unreleased && (
            <Badge
              variant="outline"
              className="h-5 border-amber-500/40 bg-amber-500/10 px-1.5 text-[10px] text-amber-600 dark:text-amber-400"
            >
              {t('changelog.inProgress')}
            </Badge>
          )}
          {date && <span className="text-[11px] text-muted-foreground">{date}</span>}
        </span>
        {!expanded && (
          <span className="shrink-0 text-[11px] text-muted-foreground">
            {t('changelog.items', {count, n: count})}
          </span>
        )}
      </button>

      {expanded && (
        <div className="space-y-3.5 border-t border-border px-3.5 py-3.5">
          {sections.map((sec, si) => (
            <div key={si} className="space-y-2">
              <div className="flex items-center gap-2">
                <span
                  className={cn(
                    'rounded-md border px-1.5 py-0.5 text-[10px] font-medium',
                    SECTION_STYLE[sec.title] ?? 'border-border bg-muted text-muted-foreground',
                  )}
                >
                  {sectionLabel(sec.title, t)}
                </span>
                <span className="text-[11px] text-muted-foreground">
                  {t('changelog.items', {count: sec.items.length, n: sec.items.length})}
                </span>
              </div>
              <ItemList items={sec.items} />
            </div>
          ))}
          {isCurrent && currentVersion && !unreleased && (
            <div className="pt-0.5 text-[11px] text-muted-foreground">
              {t('changelog.thisIsCurrent')}
            </div>
          )}
          {index === 0 && !unreleased && showSeeAll && (
            <div className="pt-0.5 text-[11px] text-muted-foreground">
              {t('changelog.seeAll')}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function LocalChangelog() {
  const t = useT();
  const [data, setData] = useState<Changelog | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    systemApi
      .changelog()
      .then((d) => {
        if (!alive) return;
        setData(d);
        if (!d.available) setError(d.error || t('changelog.unavailable'));
        else if (d.versions.length) setOpen(d.versions[0].version);
      })
      .catch(() => alive && setError(t('changelog.loadFailed')))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [t]);

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 rounded-[20px] bg-muted py-16 text-xs text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        {t('changelog.loading')}
      </div>
    );
  }

  if (error || !data?.available) {
    return (
      <div className="flex items-start gap-2.5 rounded-[20px] border border-amber-500/30 bg-amber-500/10 p-4">
        <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
        <div className="space-y-1 text-xs">
          <div className="font-medium">{t('changelog.unavailable')}</div>
          <div className="text-muted-foreground">{error || data?.error}</div>
          <div className="text-muted-foreground">
            <RichText text={t('changelog.unavailableHint')} />
          </div>
        </div>
      </div>
    );
  }

  const currentVersion = (data.current || '').replace(/^v/i, '');

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <FileText className="h-4 w-4" />
          {t('changelog.title')}
          {currentVersion && (
            <span className="font-mono text-xs text-muted-foreground">
              {t('changelog.currentVersionLabel', {v: currentVersion})}
            </span>
          )}
        </div>
        <Button asChild variant="ghost" size="sm" className="h-7 gap-1.5 text-xs">
          <a href={REPO_URL} target="_blank" rel="noreferrer">
            <Package className="h-3.5 w-3.5" />
            Releases
          </a>
        </Button>
      </div>

      <div className="scroll-slim max-h-[calc(100dvh-260px)] min-h-[320px] space-y-2 overflow-y-auto pr-1">
        {data.versions.map((v, i) => (
          <VersionBlock
            key={v.version}
            index={i}
            version={v.version}
            date={v.date}
            unreleased={v.unreleased}
            isCurrent={!v.unreleased && v.version.replace(/^v/i, '') === currentVersion}
            currentVersion={currentVersion}
            expanded={open === v.version}
            onToggle={() => setOpen(open === v.version ? null : v.version)}
            sections={v.sections}
          />
        ))}
      </div>

      {data.truncated && (
        <p className="text-[11px] text-muted-foreground">
          {t('changelog.truncated', {n: data.versions.length, total: data.total ?? 0})}
        </p>
      )}
    </div>
  );
}

/**
 * 上游仓库更新日志（只记录，不跟随）。
 *
 * 与上面的本地面板是两份独立数据：本地这份读 `CHANGELOG.md`（本仓库发布日志），
 * 这份读 `CHANGELOG-UPSTREAM.md`（每日巡检上游追加的流水）。分开显示的目的就是
 * 让「我们自己改了什么」与「上游又动了什么」不会搅在一起，也保证巡检永远不会
 * 写进本地版本的日志里。
 */
function UpstreamChangelog() {
  const t = useT();
  const [data, setData] = useState<UpstreamLog | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);
  const [open, setOpen] = useState<string | null>(null);

  const apply = (d: UpstreamLog) => {
    setData(d);
    if (d.versions.length) setOpen((cur) => cur ?? d.versions[0].version);
  };

  useEffect(() => {
    let alive = true;
    systemApi
      .upstreamLog()
      .then((d) => alive && apply(d))
      .catch(() => alive && setError(t('changelog.upstreamLoadFailed')))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [t]);

  const runCheck = async () => {
    setChecking(true);
    setError('');
    try {
      const res = await systemApi.checkUpstream();
      const d = await systemApi.upstreamLog();
      apply(d);
      if (!res.ok) setError(t('changelog.upstreamCheckFailed', {err: res.error || ''}));
    } catch {
      setError(t('changelog.upstreamLoadFailed'));
    } finally {
      setChecking(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 rounded-[20px] bg-muted py-16 text-xs text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        {t('changelog.loading')}
      </div>
    );
  }

  const meta = data;
  const repoUrl = meta?.repo ? `https://github.com/${meta.repo}` : '';
  const behind = meta?.remote_newer === true;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <GitBranch className="h-4 w-4" />
          {t('changelog.upstreamTitle')}
          {meta?.app_version && (
            <span className="font-mono text-xs text-muted-foreground">
              {t('changelog.upstreamAppVersion', {v: meta.app_version})}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          {repoUrl && (
            <Button asChild variant="ghost" size="sm" className="h-7 gap-1.5 text-xs">
              <a href={repoUrl} target="_blank" rel="noreferrer">
                <Package className="h-3.5 w-3.5" />
                {meta?.repo}
              </a>
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            className="h-7 gap-1.5 text-xs"
            onClick={runCheck}
            disabled={checking}
          >
            {checking ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            {checking ? t('changelog.upstreamChecking') : t('changelog.upstreamCheckNow')}
          </Button>
        </div>
      </div>

      {/* 只记录不跟随 —— 说在前面，避免被当成「自动升级开关」 */}
      <p className="rounded-[14px] border border-border bg-muted/60 px-3 py-2 text-[11px] leading-5 text-muted-foreground">
        {t('changelog.upstreamNever')}
      </p>

      {meta && (
        <div className="grid gap-2 rounded-[16px] border border-border p-3 text-[11px] sm:grid-cols-2">
          <div className="min-w-0 space-y-1">
            <div className="text-muted-foreground">{t('changelog.upstreamRemoteHead')}</div>
            <div className="truncate font-mono">{meta.remote_short || '—'}</div>
            <div className="truncate text-muted-foreground" title={meta.remote_subject}>
              {meta.remote_date} {meta.remote_subject}
            </div>
          </div>
          <div className="min-w-0 space-y-1">
            <div className="text-muted-foreground">{t('changelog.upstreamLocalHead')}</div>
            <div className="truncate font-mono">{meta.local_short || '—'}</div>
            <div className="truncate text-muted-foreground" title={meta.local_subject}>
              {meta.local_date} {meta.local_subject}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
            {meta.remote_newer === null ? null : behind ? (
              <Badge
                variant="outline"
                className="h-5 border-amber-500/40 bg-amber-500/10 px-1.5 text-[10px] text-amber-600 dark:text-amber-400"
              >
                {t('changelog.upstreamBehind')}
              </Badge>
            ) : (
              <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
                {t('changelog.upstreamSynced')}
              </Badge>
            )}
            <span className="text-muted-foreground">
              {meta.checked_at
                ? t('changelog.upstreamCheckedAt', {t: fmtDateTime(meta.checked_at)})
                : t('changelog.upstreamNeverChecked')}
            </span>
            <span className="text-muted-foreground">
              · {t('changelog.upstreamSchedule', {hour: String(meta.watch_hour)})}
            </span>
          </div>
        </div>
      )}

      {(error || meta?.error) && (
        <div className="flex items-start gap-2.5 rounded-[16px] border border-amber-500/30 bg-amber-500/10 p-3">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
          <div className="min-w-0 break-words text-[11px] text-muted-foreground">
            {error || meta?.error}
          </div>
        </div>
      )}

      {!meta?.available || !meta.versions.length ? (
        <div className="rounded-[20px] bg-muted px-4 py-10 text-center text-xs text-muted-foreground">
          {t('changelog.upstreamEmpty')}
        </div>
      ) : (
        <div className="scroll-slim max-h-[calc(100dvh-320px)] min-h-[240px] space-y-2 overflow-y-auto pr-1">
          {meta.versions.map((v, i) => (
            <VersionBlock
              key={v.version}
              index={i}
              version={v.version}
              label={v.version}
              date=""
              unreleased={v.unreleased}
              isCurrent={false}
              currentVersion=""
              expanded={open === v.version}
              onToggle={() => setOpen(open === v.version ? null : v.version)}
              sections={v.sections}
              showSeeAll={false}
            />
          ))}
        </div>
      )}

      {meta?.truncated && (
        <p className="text-[11px] text-muted-foreground">
          {t('changelog.truncated', {n: meta.versions.length, total: meta.total})}
        </p>
      )}
    </div>
  );
}

/**
 * 更新日志面板：本地 / 上游两块分开显示。
 *
 * 为什么做成一个 Tab 里的两段而不是两个设置页：设置页的 Tab 数量有测试盯着
 * （8 个，改数要同步路由、路线图与 CHANGELOG），而这两份日志本来就是「同一个
 * 问题的两面」——本地改了什么、上游又动了什么，放在一起看才有意义。
 */
export function ChangelogPanel() {
  const t = useT();
  const [view, setView] = useState<'local' | 'upstream'>('local');
  const tabs: Array<{key: 'local' | 'upstream'; label: string}> = [
    {key: 'local', label: t('changelog.tabLocal')},
    {key: 'upstream', label: t('changelog.tabUpstream')},
  ];
  return (
    <div className="space-y-3">
      <div className="inline-flex rounded-full bg-muted p-0.5" role="tablist">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={view === tab.key}
            onClick={() => setView(tab.key)}
            className={cn(
              'rounded-full px-3 py-1 text-xs transition-colors',
              view === tab.key
                ? 'bg-background font-medium text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {view === 'local' ? <LocalChangelog /> : <UpstreamChangelog />}
    </div>
  );
}
