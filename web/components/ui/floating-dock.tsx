/**
 * Note: Use position fixed according to your needs
 * Desktop navbar is better positioned at the bottom
 * Mobile navbar is better positioned at bottom right.
 **/

import {usePathname} from 'next/navigation';
import {cn} from '@/lib/utils';
import {withBasePath} from '@/lib/base-path';
import {splitByGroup} from '@/lib/dock-groups';
import {IconLayoutNavbarCollapse} from '@tabler/icons-react';
import {MoreHorizontal} from 'lucide-react';
import {
  AnimatePresence,
  MotionValue,
  motion,
  useMotionValue,
  useSpring,
  useTransform,
} from 'motion/react';

import {Fragment, useRef, useState, memo, useCallback} from 'react';

export type FloatingDockItem = {
  title: string;
  icon: React.ReactNode;
  href?: string;
  onClick?: () => void;
  tooltip?: string;
  customComponent?: React.ReactNode;
  external?: boolean;
  /** 分组键：相邻两项不同时，两组之间画一条分隔线（见 `@/lib/dock-groups`）。 */
  groupKey?: string;
  /** 分组名（已翻译）。桌面端挂在分隔线上做 hover 提示，移动端直接显示。 */
  groupLabel?: string;
};

export const FloatingDock = ({
  items,
  desktopClassName,
  mobileClassName,
  mobileButtonClassName,
}: {
  items: FloatingDockItem[];
  desktopClassName?: string;
  mobileClassName?: string;
  mobileButtonClassName?: string;
}) => {
  return (
    <>
      <FloatingDockDesktop items={items} className={desktopClassName} />
      <FloatingDockMobile
        items={items}
        className={mobileClassName}
        buttonClassName={mobileButtonClassName}
      />
    </>
  );
};

const MOBILE_VISIBLE_DEFAULT = 4;

/**
 * 移动端底部 Dock（2026-10-23 重写 ✓）
 *
 * 原实现：一个 48×48 的悬浮按钮，点击后向上弹出一列 11 项 ✗
 *   ⇒ 用户反馈「默认隐藏、点击弹出、容易误触」✗
 *   ⇒ 而且 `dock-root` 在没拖过位置时 `visibility: hidden` ⇒ 首次进入根本看不见 ✗✗
 *
 * 现在：**常驻底部横排** ✓ 像系统 Dock 一样一直在
 *   · 横排显示前 `maxVisible` 项（默认 4 ✓ 375px 屏下 4×44 + 更多 刚好放得下）
 *   · 其余收进「更多」✓ 点开是**网格**（4 列）而不是长列表 ⇒ 少滚动 ✓
 *   · 触摸目标统一 44×44 ✓（Apple HIG 最小尺寸 ✓）避免误触
 *
 * ★ 桌面端不受影响 ✓ 仍然走 FloatingDockDesktop
 */
const FloatingDockMobile = memo(
  ({
    items,
    className,
    buttonClassName,
    maxVisible = MOBILE_VISIBLE_DEFAULT,
  }: {
    items: FloatingDockItem[];
    className?: string;
    buttonClassName?: string;
    /** 横排直接显示几项 ✓ 其余进「更多」 */
    maxVisible?: number;
  }) => {
    const [open, setOpen] = useState(false);

    const visible = items.slice(0, maxVisible);
    const rest = items.slice(maxVisible);

    // ★ 选中态（2026-10-23 ✓ 参照 Telegram）
    //   原来没有任何高亮 ✗ 用户看不出自己在哪一页
    //   TG 的做法：选中项给一块**浅色底块** ✓ 图标不变色（不改字重）
    const pathname = usePathname();
    const isActive = (href?: string) => {
      if (!href) return false;
      if (pathname === href) return true;
      // 子路由也算（/settings/appearance 应点亮 /settings ✓）
      return href !== '/' && pathname?.startsWith(href + '/');
    };

    const renderItem = (item: FloatingDockItem, key: string) => {
      const active = isActive(item.href);
      const base = cn(
        // ★ aspect-square + min-w：截图里选中块被压成了竖长条 ✗
        //   `w-11` 在 flex 空间紧张时仍可能被压缩，加 min-w 与 aspect 双保险 ✓
        'flex aspect-square h-11 w-11 min-w-11 shrink-0 items-center justify-center rounded-[16px]',
        'transition-[background-color,transform] duration-200 active:scale-95',
        active
          ? 'bg-foreground/[0.09] text-foreground dark:bg-white/[0.14]'
          : 'text-muted-foreground hover:text-foreground',
        buttonClassName,
      );
      if (item.customComponent) {
        return (
          <div key={key} className={base}>
            {item.customComponent}
          </div>
        );
      }
      if (item.href) {
        return (
          <a
            key={key}
            href={withBasePath(item.href)}
            aria-label={item.title}
            className={base}
            {...((item.external || item.href.startsWith('https://')) ?
              {target: '_blank', rel: 'noopener noreferrer'} :
              {})}
          >
            {item.icon}
          </a>
        );
      }
      return (
        <button key={key} type="button" onClick={item.onClick} aria-label={item.title} className={base}>
          {item.icon}
        </button>
      );
    };

    return (
      <div className={cn('block w-full md:hidden', className)}>
        <AnimatePresence>
          {open && (
            <motion.div
              initial={{opacity: 0, y: 12, scale: 0.98}}
              animate={{opacity: 1, y: 0, scale: 1}}
              exit={{opacity: 0, y: 12, scale: 0.98}}
              transition={{duration: 0.18, ease: 'easeOut'}}
              className={cn(
                'absolute inset-x-0 bottom-full mb-2 mx-1 max-h-[min(65dvh,440px)] overflow-y-auto',
                'rounded-3xl border border-border/60 bg-background/95 p-3',
                'shadow-[0_18px_50px_rgba(15,23,42,0.18)] backdrop-blur-xl dark:bg-background/95',
              )}
            >
              {(() => {
                const groups = splitByGroup(items);
                return groups.map((group, gi) => (
                  <div key={group.key || `g-${gi}`} className={gi > 0 ? 'mt-3' : ''}>
                    {/* 第一组不加标题 ⇒ 弹层顶部不啰嗦 ✓ */}
                    {gi > 0 && group.label ? (
                      <div className="mb-1.5 px-1 text-[10px] font-medium text-muted-foreground">
                        {group.label}
                      </div>
                    ) : null}
                    <div className="grid grid-cols-4 gap-2">
                      {group.items.map((item) =>
                        renderItem(item, `${group.key || gi}-${item.title}`),
                      )}
                    </div>
                  </div>
                ));
              })()}
            </motion.div>
          )}
        </AnimatePresence>

        {/* 常驻横排 ✓ 不再折叠成一个点 */}
        {/* ★ 参照 Telegram 的悬浮胶囊（2026-10-23 ✓）
            要点：① 大圆角（26px）不是直角通栏 ✓
                  ② 强毛玻璃：blur-2xl + saturate-150 ✓（TG 的透明度约 50~60% ✓）
                  ③ 四周留白 ⇒ 由外层 px-4 提供 ✓ 不贴屏幕边缘 ✓
                  ④ 选中项浅色底块 ✓（见 renderItem）
            ★ 边框用白色半透明而不是 --border：玻璃边缘的"高光"来自白色 ✓
              --border 在浅色下是灰的 ⇒ 会显脏 ✗ */}
        <div
          data-slot="dock-bar"
          className={cn(
            // ★ 2026-10-23 二次修正：rounded-full 反而**过度**了 ✗
            //   对比 TG 截图（高度约 50px、圆角约 18~20px）：
            //     · rounded-full ⇒ 圆角 = 高度一半 ≈ 28px ⇒ 变成**药丸** ✗ TG 不是药丸
            //     · rounded-[26px] ⇒ 偏方 ✗
            //   ⇒ 取中间：**rounded-[20px]** ✓ 与 TG 的视觉比例（0.35~0.4）一致 ✓
            'flex items-center justify-around gap-0.5 rounded-[20px]',
            'border border-white/50 dark:border-white/[0.12]',
            'bg-white/55 dark:bg-neutral-900/60',
            'backdrop-blur-2xl backdrop-saturate-150',
            'px-2 py-2',
          )}
        >
          {visible.map((item) => renderItem(item, item.title))}
          {rest.length > 0 && (
            <button
              type="button"
              data-slot="dock-mobile-more"
              aria-expanded={open}
              aria-label={open ? 'collapse navigation' : 'expand navigation'}
              onClick={() => setOpen((v) => !v)}
              className={cn(
                'flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl',
                'transition-transform active:scale-95',
                open ? 'bg-foreground/10 text-foreground' : 'text-muted-foreground',
              )}
            >
              <MoreHorizontal className="size-5" />
            </button>
          )}
        </div>
      </div>
    );
  },
);

FloatingDockMobile.displayName = 'FloatingDockMobile';

/**
 * 分隔线的「命中区」半宽（px）。
 *
 * 为什么要留宽、而且**不能**靠分隔线自己的 hover 事件：
 *
 * 底栏是会放大的——鼠标一动，附近的图标就从 40px 长到 70px，整个底栏随之变宽
 * （实测 725 → 787px）。底栏是 `translateX(-50%)` 居中的，于是**分隔线会从光标
 * 底下挪走**（实测挪 8~30px，而线本身只有 1px 宽 + 8px 内边距）。用
 * `onMouseEnter`/`onMouseLeave` 的结果是「组名亮一下又灭」：用户在界面上看到的
 * 是一次闪烁，而代码里看不出任何异常。
 *
 * 所以判据改成在底栏的 mousemove 里**当场量**每条分隔线的矩形，看光标落在谁的
 * 命中区里。光标不动、只有布局在动时不会再触发 mousemove，标签就留在屏幕上——
 * 这正是想要的：它是「我把鼠标放上去了」的反馈，不该被图标放大挤掉。
 */
const DIVIDER_HIT_SLOP = 10;

const FloatingDockDesktop = memo(
    ({
      items,
      className,
    }: {
    items: FloatingDockItem[];
    className?: string;
  }) => {
      const mouseX = useMotionValue(Infinity);
      const rootRef = useRef<HTMLDivElement>(null);
      const [activeDivider, setActiveDivider] = useState<number | null>(null);

      const handleMouseMove = useCallback(
          (e: React.MouseEvent) => {
            mouseX.set(e.pageX);

            const nodes =
              rootRef.current?.querySelectorAll('[data-slot=dock-group-divider]');
            let next: number | null = null;
            if (nodes) {
              for (let i = 0; i < nodes.length; i += 1) {
                const rect = nodes[i].getBoundingClientRect();
                if (e.clientX >= rect.left - DIVIDER_HIT_SLOP &&
                    e.clientX <= rect.right + DIVIDER_HIT_SLOP) {
                  next = i;
                  break;
                }
              }
            }
            // 值没变时 React 会跳过重渲染，所以这里可以放心每次 mousemove 都设。
            setActiveDivider(next);
          },
          [mouseX],
      );

      const handleMouseLeave = useCallback(() => {
        mouseX.set(Infinity);
        setActiveDivider(null);
      }, [mouseX]);

      return (
        <motion.div
          ref={rootRef}
          onMouseMove={handleMouseMove}
          onMouseLeave={handleMouseLeave}
          data-slot="dock-bar"
          className={cn(
              'mx-auto hidden items-end gap-2 rounded-xl bg-gray-50 px-2 pb-2 md:flex dark:bg-neutral-900',
              className,
          )}
        >
          {(() => {
            // 按 groupKey 切组。原来的写法是「以 title === 'divider' 的哨兵条目为界
            // 切成两段」——只支持两段，而且注释里正解释着「不能写死 slice(0, 3)：
            // 写死会把多出来的那个图标静默丢掉（曾经把『密钥』挤掉过）」。
            // 改成按分组键切之后，段数由数据决定，不再有「第几段」这个概念。
            const groups = splitByGroup(items);

            return (
              <>
                {groups.map((group, index) => (
                  <Fragment key={group.key || `group-${index}`}>
                    {/* `activeDivider` 数的是**分隔线**在 DOM 里的序号，而这里只有
                        `index > 0` 的组才画线（第一组之前没有线），所以是 index - 1。 */}
                    {index > 0 &&
                      <GroupDivider label={group.label} active={activeDivider === index - 1} />}
                    <div className="flex items-end gap-2">
                      {group.items.map((item) => (
                        <IconContainer mouseX={mouseX} key={item.title} {...item} />
                      ))}
                    </div>
                  </Fragment>
                ))}
              </>
            );
          })()}
        </motion.div>
      );
    },
);

FloatingDockDesktop.displayName = 'FloatingDockDesktop';

/**
 * 组间分隔线。光标靠近时在线的上方显示组名——**这是这一批要修的那句「分隔线无标签」**：
 * 原来的竖线不带任何说明，用户只能猜它分开的是什么。
 *
 * 为什么组名走「靠近才显示」而不是常显：底栏的高度是写死的 `h-16`，图标 hover 时会从
 * 40px 放大到 70px（本来就溢出容器）。再加一行常显的组名会把底栏顶高、并和放大后的
 * 图标打架。
 *
 * ⚠️ `active` 由**父组件按光标位置算**，本组件刻意不带自己的 hover 状态——理由见
 * `DIVIDER_HIT_SLOP` 上面那段（元素会被图标放大推走）。
 */
const GroupDivider = memo(({label, active}: {label?: string; active: boolean}) => {
  return (
    <div
      data-slot="dock-group-divider"
      className="relative flex items-center justify-center px-1 mx-1 self-stretch mt-3"
    >
      <div className="w-px h-full bg-border"></div>
      <AnimatePresence>
        {active && label ? (
          <motion.div
            initial={{opacity: 0, y: 10, x: '-50%'}}
            animate={{opacity: 1, y: 0, x: '-50%'}}
            exit={{opacity: 0, y: 2, x: '-50%'}}
            className="absolute -top-8 left-1/2 w-fit whitespace-nowrap rounded-md border border-gray-200 bg-gray-100 px-2 py-0.5 text-xs text-neutral-700 dark:border-neutral-900 dark:bg-neutral-800 dark:text-white"
          >
            {label}
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
});

GroupDivider.displayName = 'GroupDivider';

const IconContainer = memo(
    ({
      mouseX,
      title,
      icon,
      href,
      onClick,
      tooltip,
      customComponent,
      external,
    }: FloatingDockItem & {mouseX: MotionValue}) => {
      const ref = useRef<HTMLDivElement>(null);

      const distance = useTransform(mouseX, (val) => {
        const bounds = ref.current?.getBoundingClientRect() ?? {x: 0, width: 0};

        return val - bounds.x - bounds.width / 2;
      });

      const widthTransform = useTransform(distance, [-150, 0, 150], [40, 70, 40]);
      const heightTransform = useTransform(
          distance,
          [-150, 0, 150],
          [40, 70, 40],
      );

      const widthTransformIcon = useTransform(
          distance,
          [-150, 0, 150],
          [20, 35, 20],
      );
      const heightTransformIcon = useTransform(
          distance,
          [-150, 0, 150],
          [20, 35, 20],
      );

      const width = useSpring(widthTransform, {
        mass: 0.1,
        stiffness: 150,
        damping: 12,
      });
      const height = useSpring(heightTransform, {
        mass: 0.1,
        stiffness: 150,
        damping: 12,
      });

      const widthIcon = useSpring(widthTransformIcon, {
        mass: 0.1,
        stiffness: 150,
        damping: 12,
      });
      const heightIcon = useSpring(heightTransformIcon, {
        mass: 0.1,
        stiffness: 150,
        damping: 12,
      });

      const [hovered, setHovered] = useState(false);

      const handleMouseEnter = useCallback(() => {
        setHovered(true);
      }, []);

      const handleMouseLeave = useCallback(() => {
        setHovered(false);
      }, []);

      const Element = customComponent ? 'div' : href ? 'a' : 'button';
      const elementProps = customComponent ? {} : href ?
      {
        // 原生 <a> 不经过 next/link，basePath 不会自动生效
        href: withBasePath(href),
        'aria-label': title,
        ...((external || href.startsWith('https://')) ?
            {target: '_blank', rel: 'noopener noreferrer'} :
            {}),
      } :
      {onClick, 'aria-label': title};

      return (
        <Element {...elementProps}>
          <motion.div
            ref={ref}
            style={{width, height}}
            onMouseEnter={handleMouseEnter}
            onMouseLeave={handleMouseLeave}
            className="relative flex aspect-square items-center justify-center rounded-full bg-gray-200 cursor-pointer dark:bg-neutral-800"
          >
            <AnimatePresence>
              {hovered && (
                <motion.div
                  initial={{opacity: 0, y: 10, x: '-50%'}}
                  animate={{opacity: 1, y: 0, x: '-50%'}}
                  exit={{opacity: 0, y: 2, x: '-50%'}}
                  className="absolute -top-8 left-1/2 w-fit rounded-md border border-gray-200 bg-gray-100 px-2 py-0.5 text-xs whitespace-pre text-neutral-700 dark:border-neutral-900 dark:bg-neutral-800 dark:text-white"
                >
                  {tooltip || title}
                </motion.div>
              )}
            </AnimatePresence>
            <motion.div
              style={{width: widthIcon, height: heightIcon}}
              className="flex items-center justify-center"
            >
              {customComponent || icon}
            </motion.div>
          </motion.div>
        </Element>
      );
    },
);

IconContainer.displayName = 'IconContainer';
