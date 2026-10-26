'use client';

import {useEffect, useState} from 'react';
import {
  Check,
  ChevronRight,
  Copy,
  FileText,
  GitBranch,
  Loader2,
  Package,
  RefreshCw,
  TriangleAlert,
} from 'lucide-react';

import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/animate-ui/radix/dialog';
import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {systemApi} from '@/lib/api';
import {fmtDateTime} from '@/lib/format';
import {RichText} from '@/lib/i18n/rich-text';
import {useT} from '@/lib/i18n/provider';
import {cn} from '@/lib/utils';
import type {Changelog, ChangelogItem, UpstreamLog, UpgradeBrief} from '@/lib/types';

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

function LocalChangelog({data, error}: {data: Changelog | null; error: string}) {
  const t = useT();
  const [open, setOpen] = useState<string | null>(null);

  // 「未发布」那一版默认展开：本地这栏通常只有这一版
  const unreleasedVersion = (data?.versions || []).find((v) => v.unreleased)?.version
    ?? (data?.versions || [])[0]?.version ?? null;
  useEffect(() => {
    if (unreleasedVersion) setOpen((cur) => cur ?? unreleasedVersion);
  }, [unreleasedVersion]);

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
  // 这里只放「还没发布」的改动：已经发过的版本在上游那栏（GitHub Releases）里，
  // 两处都列一遍会出现两份会互相漂移的副本。
  // 只列本地自己的那一版（未发布的 + 当前正在跑的版本）：更早的都是上游历史，
  // 要看它们去「上游」栏，本地栏列一遍只会重复。
  const localVersions = data.versions.filter(
    (v) => v.unreleased || v.version.replace(/^v/i, '') === currentVersion,
  );

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
        {localVersions.map((v, i) => (
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

      {!localVersions.length && (
        <p className="rounded-[20px] bg-muted py-10 text-center text-xs text-muted-foreground">
          {t('changelog.localEmpty')}
        </p>
      )}

      {data.truncated && (
        <p className="text-[11px] text-muted-foreground">
          {t('changelog.truncated', {n: data.versions.length, total: data.total ?? 0})}
        </p>
      )}
    </div>
  );
}

/**
 * 上游更新（GitHub Releases）。
 *
 * 与上面的本地面板分工明确：**本地这栏只放没发布的改动**（`CHANGELOG.md` 的
 * `[未发布]`），**已经发出去的版本在上游这栏**（`ithtelab/workbuddy-manager` 的
 * Releases，逐版列出正文）。同一份内容不在两处各存一份，避免漂移。
 *
 * 数据来自每日巡检（`services/upstreamwatch`）缓存的 Release 列表，页面打开时
 * 只读缓存；「立即检查」才联网。
 */
function UpstreamChangelog({initial, initialError}: {initial: UpstreamLog | null; initialError: string}) {
  const t = useT();
  const [data, setData] = useState<UpstreamLog | null>(initial);
  const [error, setError] = useState(initialError);
  const [checking, setChecking] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  // 升级对比简报：点按钮才生成（要跑 git log/status，不预热）
  const [brief, setBrief] = useState<UpgradeBrief | null>(null);
  const [briefOpen, setBriefOpen] = useState(false);
  const [briefing, setBriefing] = useState(false);
  const [copied, setCopied] = useState(false);

  const runBrief = async () => {
    setBriefing(true);
    try {
      const d = await systemApi.upgradeBrief();
      setBrief(d);
      setBriefOpen(true);
    } catch {
      setError(t('changelog.upstreamLoadFailed'));
    } finally {
      setBriefing(false);
    }
  };

  const copyBrief = async () => {
    if (!brief) return;
    try {
      await navigator.clipboard.writeText(brief.markdown);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      // 剪贴板被拒（非安全上下文）：正文本来就能手动选中复制，不额外提示
    }
  };

  const apply = (d: UpstreamLog) => {
    setData(d);
    // 默认展开**最新版本**：进这栏就是要看「上游现在发到哪、都改了什么」
    setOpen((cur) => cur ?? d.versions[0]?.version ?? null);
  };

  useEffect(() => {
    if (initial) apply(initial);
    // 只在首次拿到父组件数据时展开一次，之后由用户自己点
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initial]);

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

  const meta = data;
  const repoUrl = meta?.repo ? `https://github.com/${meta.repo}` : '';
  const releasesUrl = meta?.repo ? `https://github.com/${meta.repo}/releases` : '';
  const localTag = (meta?.local_version || '').replace(/^v/i, '');

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-medium">
          <GitBranch className="h-4 w-4" />
          {t('changelog.upstreamTitle')}
        </div>
        <div className="flex items-center gap-1.5">
          {repoUrl && (
            <Button asChild variant="ghost" size="sm" className="h-7 gap-1.5 text-xs">
              <a href={releasesUrl} target="_blank" rel="noreferrer">
                <Package className="h-3.5 w-3.5" />
                {meta?.repo}
              </a>
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            className="h-7 gap-1.5 text-xs"
            onClick={runBrief}
            disabled={briefing}
            title={t('changelog.briefHint')}
          >
            {briefing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <FileText className="h-3.5 w-3.5" />
            )}
            {t('changelog.briefButton')}
          </Button>
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

      <Dialog open={briefOpen} onOpenChange={setBriefOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>{t('changelog.briefTitle')}</DialogTitle>
            <DialogDescription>{t('changelog.briefDesc')}</DialogDescription>
          </DialogHeader>
          <DialogBody>
            <pre className="max-h-[58vh] overflow-auto whitespace-pre-wrap break-words rounded-[12px] border border-border bg-muted/50 p-3 font-mono text-[11px] leading-5">
              {brief?.markdown || ''}
            </pre>
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" size="sm" className="gap-1.5" onClick={copyBrief}>
              {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
              {copied ? t('changelog.briefCopied') : t('changelog.briefCopy')}
            </Button>
            <Button size="sm" onClick={() => setBriefOpen(false)}>
              {t('changelog.briefClose')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 只记录不跟随 —— 说在前面，避免被当成「自动升级开关」 */}
      <p className="rounded-[14px] border border-border bg-muted/60 px-3 py-2 text-[11px] leading-5 text-muted-foreground">
        {t('changelog.upstreamNever')}
      </p>

      {meta && (
        <div className="grid gap-2 rounded-[16px] border border-border p-3 text-[11px] sm:grid-cols-2">
          <div className="min-w-0 space-y-1">
            <div className="text-muted-foreground">{t('changelog.upstreamRemoteHead')}</div>
            <div className="truncate font-mono">{meta.latest || '—'}</div>
          </div>
          <div className="min-w-0 space-y-1">
            <div className="text-muted-foreground">{t('changelog.upstreamLocalHead')}</div>
            <div className="truncate font-mono">{meta.local_version || '—'}</div>
          </div>
          <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
            {meta.behind === null ? null : meta.behind ? (
              <Badge
                variant="outline"
                className="h-5 border-amber-500/40 bg-amber-500/10 px-1.5 text-[10px] text-amber-600 dark:text-amber-400"
              >
                {t('changelog.upstreamBehind', {latest: meta.latest, local: meta.local_version})}
              </Badge>
            ) : (
              <Badge variant="secondary" className="h-5 px-1.5 text-[10px]">
                {t('changelog.upstreamSynced', {latest: meta.latest, local: meta.local_version})}
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

      {error && (
        <p className="flex items-start gap-1.5 rounded-[14px] border border-destructive/40 bg-destructive/5 px-3 py-2 text-[11px] text-destructive">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span className="min-w-0 break-words">{error}</span>
        </p>
      )}

      {meta && !meta.available && !error && (
        <p className="rounded-[20px] bg-muted py-10 text-center text-xs text-muted-foreground">
          {t('changelog.upstreamEmpty')}
        </p>
      )}

      {meta && meta.available && (
        <>
          <p className="text-[11px] text-muted-foreground">
            {t('changelog.upstreamListHint', {min: meta.min_tag, n: meta.total})}
          </p>
          <div className="space-y-2">
            {meta.versions.map((v, i) => (
              <VersionBlock
                key={v.version}
                index={i}
                version={v.version}
                date={v.date}
                unreleased={false}
                isCurrent={v.version === localTag}
                currentVersion={localTag}
                expanded={open === v.version}
                onToggle={() => setOpen(open === v.version ? null : v.version)}
                sections={v.sections}
                showSeeAll={false}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

export function ChangelogPanel() {
  const t = useT();
  // 默认落在「上游更新」：来这里的人通常先想知道「上游发到哪一版了、都改了什么」，
  // 本地这栏（只有未发布的改动）多数时候是空的。
  const [view, setView] = useState<'local' | 'upstream'>('upstream');
  const [local, setLocal] = useState<Changelog | null>(null);
  const [localError, setLocalError] = useState('');
  const [upstream, setUpstream] = useState<UpstreamLog | null>(null);
  const [upstreamError, setUpstreamError] = useState('');
  const [loading, setLoading] = useState(true);

  // 两个数据在这里一起取：标签上要显示版本号，未挂载的那一栏也得知道版本
  useEffect(() => {
    let alive = true;
    Promise.allSettled([systemApi.changelog(), systemApi.upstreamLog()])
      .then(([l, u]) => {
        if (!alive) return;
        if (l.status === 'fulfilled') {
          setLocal(l.value);
          if (!l.value.available) setLocalError(l.value.error || t('changelog.unavailable'));
        } else {
          setLocalError(t('changelog.loadFailed'));
        }
        if (u.status === 'fulfilled') setUpstream(u.value);
        else setUpstreamError(t('changelog.upstreamLoadFailed'));
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [t]);

  const localTag = (local?.current || '').replace(/^v/i, '');
  const upstreamTag = (upstream?.latest || '').replace(/^v/i, '');
  const tabs: Array<{key: 'local' | 'upstream'; label: string; version: string}> = [
    {key: 'upstream', label: t('changelog.tabUpstream'), version: upstreamTag},
    {key: 'local', label: t('changelog.tabLocal'), version: localTag},
  ];

  return (
    <div className="space-y-3">
      <div className="inline-flex items-center rounded-full bg-muted p-0.5" role="tablist">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            role="tab"
            aria-selected={view === tab.key}
            onClick={() => setView(tab.key)}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs transition-colors',
              view === tab.key
                ? 'bg-background font-medium text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {tab.label}
            {tab.version && (
              <span className="font-mono text-[10px] text-muted-foreground">v{tab.version}</span>
            )}
          </button>
        ))}
      </div>
      {loading ? (
        <div className="flex items-center justify-center gap-2 rounded-[20px] bg-muted py-16 text-xs text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          {t('changelog.loading')}
        </div>
      ) : view === 'local' ? (
        <LocalChangelog data={local} error={localError} />
      ) : (
        <UpstreamChangelog initial={upstream} initialError={upstreamError} />
      )}
    </div>
  );
}
