'use client';

/**
 * 上游（账号池分组）的新增 / 编辑弹窗。
 *
 * 为什么抽成公共组件：这个表单有两个入口——「设置 → 上游」的管理面板，以及
 * 账号页的「添加分组」（分组的日常入口在账号页：加号、移号都在那儿发生）。
 * 两份拷贝会漂移，而这里每个字段都直接影响路由行为（地址错了请求打错地方、
 * 目录错了账号管到别的组去）。
 *
 * **账号页的入口用 `simple` 模式：只显示名称一个字段**——地址与账号目录的默认值
 * 静默带出（地址 = 默认分组的、目录 = 默认目录同级 + 名称后缀），要细调到
 * 「设置 → 上游」。用户反馈：添加分组只想填个名字。
 *
 * 新增时的**建议值**：
 *   · 上游地址默认带出「默认分组」的地址（同址 = 与默认分组共用实例；
 *     api_key 留空时服务端自动沿用默认那把，见 server/upstreamsvc.forward_api_key）；
 *   · 账号目录默认带出默认分组目录的同级路径（跟随名称变化，手动改过就不再跟）。
 *
 * 编辑时**不回填明文 api_key**（接口只回脱敏值，见 routers/upstreams.py）：
 * 留空 = 不修改。
 */
import {useEffect, useState} from 'react';

import {errText, upstreamsApi} from '@/lib/api';
import {useT} from '@/lib/i18n/provider';
import {notify} from '@/lib/toast';
import type {AuthDirOption, UpstreamEndpoint} from '@/lib/types';
import {Button} from '@/components/ui/button';
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/animate-ui/radix/dialog';
import {Input} from '@/components/ui/input';
import {Label} from '@/components/ui/label';
import {Switch} from '@/components/ui/switch';
import {Textarea} from '@/components/ui/textarea';

interface FormState {
  name: string;
  base_url: string;
  api_key: string;
  note: string;
  enabled: boolean;
  auth_dir: string;
  container: string;
}

const emptyForm: FormState = {
  name: '',
  base_url: '',
  api_key: '',
  note: '',
  enabled: true,
  auth_dir: '',
  container: '',
};

/**
 * 给分组建议一个账号目录：默认分组目录的同级、加名称后缀。
 *
 * 为什么要有建议值：「添加分组」的期望用法是**只填名称**——配套的账号目录
 * 是这个功能的落点（移动账号往哪放），留空会让分组收不了账号。只做路径拼接，
 * 不建目录（移动/加号时才会真的创建）。
 */
function suggestAuthDir(defaultDir: string, name: string): string {
  const base = (defaultDir || '').replace(/[\\/]+$/, '');
  if (!base) return '';
  const slug = (name || '').trim().replace(/[\\/:*?"<>|\s]+/g, '').slice(0, 32);
  return `${base}-${slug || 'new'}`;
}

export function UpstreamFormDialog({
  open,
  onOpenChange,
  editing,
  onSaved,
  defaultUpstream,
  simple = false,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  /** null = 新建；非空 = 编辑该上游 */
  editing: UpstreamEndpoint | null;
  /** 保存成功后的回调（新建时拿到新行——账号页用它直接切到新分组） */
  onSaved?: (item: UpstreamEndpoint) => void;
  /**
   * 「默认分组」那一行：新建时用它的地址 / 账号目录给表单带出建议值。
   * 只取两个字符串参与依赖，避免父组件每次刷新列表都把正在填的表单重置掉。
   */
  defaultUpstream?: UpstreamEndpoint | null;
  /**
   * true = 极简模式（账号页「添加分组」）：**只显示名称**，其余字段不出现，
   * 用默认值提交（地址 = 默认分组、账号目录 = 建议路径）。
   */
  simple?: boolean;
}) {
  const t = useT();
  const [form, setForm] = useState<FormState>(emptyForm);
  const [busy, setBusy] = useState(false);
  /** 账号目录是否被手动改过：没改过时跟随名称刷新建议路径 */
  const [dirTouched, setDirTouched] = useState(false);
  /**
   * 账号目录候选。为什么不让人手敲路径：账号目录错了**不报错**（分组照样建好），
   * 只是那个分组永远没有账号，看起来像「上游不干活」。
   */
  const [dirOptions, setDirOptions] = useState<AuthDirOption[]>([]);

  const defaultBaseUrl = defaultUpstream?.base_url || '';
  const defaultDir = defaultUpstream?.auth_dir || '';

  // 打开时按 editing / 默认分组重置。依赖只取字符串（见上面的注释）。
  useEffect(() => {
    if (!open) return;
    if (editing) {
      setForm({
        name: editing.name,
        base_url: editing.base_url,
        api_key: '', // 不回填明文；留空 = 不修改
        note: editing.note || '',
        enabled: editing.enabled,
        // 还没配账号目录的分组：带出建议路径，用户直接保存即可开始用
        auth_dir: editing.auth_dir || suggestAuthDir(defaultDir, editing.name),
        container: editing.container || '',
      });
      setDirTouched(!!editing.auth_dir);
    } else {
      setForm({
        ...emptyForm,
        base_url: defaultBaseUrl,
        auth_dir: suggestAuthDir(defaultDir, ''),
      });
      setDirTouched(false);
    }
  }, [open, editing, defaultBaseUrl, defaultDir]);

  // 拉候选目录（打开时一次即可：目录是磁盘现状，不需要实时）
  useEffect(() => {
    if (!open) return;
    let alive = true;
    upstreamsApi
      .authDirOptions()
      .then((res) => {
        if (alive) setDirOptions(res.items || []);
      })
      // 候选只是便利功能：拿不到就退化成手填，不打扰用户
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [open]);

  /** 候选目录的一排小按钮：点一下就填进输入框（仍然可以手改）。 */
  function renderDirOptions() {
    if (dirOptions.length < 2) return null;
    return (
      <div className="flex flex-wrap gap-1.5">
        {dirOptions.map((opt) => {
          const active = form.auth_dir === opt.path;
          const taken = !!opt.owner && !active;
          return (
            <button
              key={opt.path}
              type="button"
              title={opt.path}
              onClick={() => {
                setForm((f) => ({...f, auth_dir: opt.path}));
                setDirTouched(true);
              }}
              className={`rounded-full border px-2 py-0.5 text-[11px] transition-colors ${
                active
                  ? 'border-primary/60 bg-primary/10 text-primary'
                  : 'border-border text-muted-foreground hover:bg-accent'
              }`}
            >
              {opt.name}
              <span className="ml-1.5 opacity-70">
                · {opt.is_default
                  ? t('upstreams.dirOptionDefault')
                  : taken
                    ? t('upstreams.dirOptionTaken', {name: opt.owner})
                    : t('upstreams.dirOptionAccounts', {n: opt.account_count})}
              </span>
            </button>
          );
        })}
      </div>
    );
  }

  async function submit() {
    if (busy) return;
    // simple 模式下列表可能还没加载完（默认分组行未就绪）：地址兜底再校验，
    // 免得「只填了名称」却在保存时才报「地址不能为空」。
    const baseUrl = form.base_url.trim() || (simple ? defaultBaseUrl : '');
    if (!form.name.trim() || !baseUrl) {
      notify.err(t('upstreams.nameUrlRequired'));
      return;
    }
    const payload = {...form, base_url: baseUrl};
    setBusy(true);
    try {
      const saved = editing
        ? await upstreamsApi.update(editing.id as number, payload)
        : await upstreamsApi.create(payload);
      notify.ok(editing ? t('upstreams.updated') : t('upstreams.created'));
      onOpenChange(false);
      onSaved?.(saved);
    } catch (e) {
      // 校验失败（目录不是绝对路径、容器名非法等）后端回 400 且给出原因——原样弹
      notify.err(errText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[440px]" showCloseButton>
        <DialogHeader>
          <DialogTitle>{editing ? t('upstreams.editTitle') : t('upstreams.addTitle')}</DialogTitle>
          {!simple && <DialogDescription>{t('upstreams.formGroupHint')}</DialogDescription>}
        </DialogHeader>
        {/* 布局类（space-y-4 / px-6）必须写在 Body **里面**这层：DialogBody 的
            className 落在 ScrollArea 的 Root 上，写在那儿字段之间会没有间距
            （见 DialogBody 的注释）。 */}
        <DialogBody className="max-h-[min(640px,calc(100dvh-12rem))]">
          <div className="space-y-4 px-6 pb-2">
            <div className="space-y-1.5">
              <Label className="text-[11px] text-muted-foreground">{t('upstreams.fieldName')}</Label>
              <Input
                value={form.name}
                onChange={(e) => {
                  const name = e.target.value;
                  setForm((f) => ({
                    ...f,
                    name,
                    // 名称变了、目录没被手动改过：建议路径跟着刷新
                    auth_dir: dirTouched ? f.auth_dir : suggestAuthDir(defaultDir, name),
                  }));
                }}
                placeholder={t('upstreams.fieldNamePlaceholder')}
                maxLength={64}
              />
            </div>
            {simple && (
              <div className="space-y-1.5">
                <Label className="text-[11px] text-muted-foreground">
                  {t('upstreams.fieldDir')}
                </Label>
                {renderDirOptions()}
                <p className="truncate text-[11px] leading-4 text-muted-foreground" title={form.auth_dir}>
                  {form.auth_dir || t('upstreams.fieldDirPlaceholder')}
                </p>
              </div>
            )}
            {!simple && (
              <>
                <div className="space-y-1.5">
                  <Label className="text-[11px] text-muted-foreground">{t('upstreams.fieldUrl')}</Label>
                  <Input
                    value={form.base_url}
                    onChange={(e) => setForm({...form, base_url: e.target.value})}
                    placeholder="http://127.0.0.1:7863"
                    maxLength={500}
                  />
                  <p className="text-[11px] leading-4 text-muted-foreground">
                    {t('upstreams.fieldUrlHint')}
                  </p>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-[11px] text-muted-foreground">{t('upstreams.fieldKey')}</Label>
                  <Input
                    value={form.api_key}
                    onChange={(e) => setForm({...form, api_key: e.target.value})}
                    // 编辑时不回填明文（接口只回脱敏值），当前值显示在占位里，「留空 = 不修改」
                    placeholder={editing && editing.has_key
                      ? t('upstreams.fieldKeyKeep', {masked: editing.api_key_masked})
                      : t('upstreams.fieldKeyPlaceholder')}
                    maxLength={500}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-[11px] text-muted-foreground">{t('upstreams.fieldDir')}</Label>
                  <Input
                    value={form.auth_dir}
                    onChange={(e) => {
                      setForm({...form, auth_dir: e.target.value});
                      setDirTouched(true);
                    }}
                    placeholder={t('upstreams.fieldDirPlaceholder')}
                    maxLength={500}
                  />
                  {renderDirOptions()}
                  <p className="text-[11px] leading-4 text-muted-foreground">
                    {t('upstreams.fieldDirHint')}
                  </p>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-[11px] text-muted-foreground">{t('upstreams.fieldContainer')}</Label>
                  <Input
                    value={form.container}
                    onChange={(e) => setForm({...form, container: e.target.value})}
                    placeholder={t('upstreams.fieldContainerPlaceholder')}
                    maxLength={64}
                  />
                  <p className="text-[11px] leading-4 text-muted-foreground">
                    {t('upstreams.fieldContainerHint')}
                  </p>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-[11px] text-muted-foreground">{t('upstreams.fieldNote')}</Label>
                  <Textarea
                    value={form.note}
                    onChange={(e) => setForm({...form, note: e.target.value})}
                    maxLength={200}
                    rows={2}
                  />
                </div>
                <div className="flex items-center justify-between">
                  <Label className="text-[11px] text-muted-foreground">{t('upstreams.fieldEnabled')}</Label>
                  <Switch checked={form.enabled} onCheckedChange={(v) => setForm({...form, enabled: v})} aria-label={t('upstreams.fieldEnabled')} />
                </div>
              </>
            )}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" className="rounded-full" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button className="rounded-full" disabled={busy} onClick={submit}>
            {t('common.save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
