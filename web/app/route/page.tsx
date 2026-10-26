'use client';

/**
 * 移动端自动选路页（2026-10-26）
 *
 * 解决的具体问题
 * --------------
 * 这台 Mac 有两条接入路径，可靠性完全不同：
 *   · 局域网 `192.168.x.x` —— 同 WiFi 下最快，出门即失效
 *   · Tailscale `100.x`    —— 任何网络下都通，但在家多走一跳
 *
 * 让用户记两个书签是错的（他自己也说不清此刻该用哪个）。所以：
 *   **书签统一存 Tailscale IP + `/route`**（因为它永远能打开这个页面），
 *   页面加载后实测各条路径，若局域网明显更快就自动切过去。
 *
 * 三条设计约束（都是踩过的坑）
 * ---------------------------
 * 1. **不能盲目跳转**。当前 host 是「页面已经加载成功」的那个地址，它必然可达。
 *    只有当别的地址**明显更快**（低于当前 70%）才跳，否则原地不动——
 *    否则一次抖动的探测结果就会把用户甩到更差的链路上。
 * 2. **必须防循环**。跳转后用 sessionStorage 记一笔，同一会话里不再为同一个
 *    目标跳第二次。没有这个，两条链路延迟接近时页面会来回弹。
 * 3. **探测只判可达性，不判业务健康**。`/healthz` 在上游无可用账号时返回 503，
 *    但那是业务状态，网络明明是通的。所以用 `mode:'no-cors'` 只确认「连上了」，
 *    状态码一律不看（no-cors 下本来也读不到）。
 */

import {useCallback, useEffect, useRef, useState} from 'react';

interface Candidate {
	host: string;
	kind: 'lan' | 'tailscale' | 'mdns';
	label: string;
	priority: number;
}

interface CandidatesResponse {
	ok: boolean;
	port: number;
	current: string | null;
	candidates: Candidate[];
	excluded: {host: string; iface: string; reason: string}[];
}

type State = 'pending' | 'ok' | 'fail';

interface Row extends Candidate {
	ms: number | null;
	state: State;
}

/**
 * 单条探测的超时。定 1200ms 是权衡后的结果：
 *   · 太短（<800ms）—— DERP 中继下 RTT 可能 300~600ms，正常链路会被误判成不可达，
 *     结果每次都「探测失败 → 进当前入口」，等于自动选路白做。
 *   · 太长（>2000ms）—— 用户点开书签后要干等，而他要的是进应用，不是等测速报告。
 * 1200ms 能覆盖中继、又不至于让等待明显可感。
 */
const PROBE_TIMEOUT_MS = 1200;
/** 判定「明显更快」的阈值：新地址延迟须低于基准的 70%。 */
const SWITCH_THRESHOLD = 0.7;
const JUMP_GUARD_KEY = 'wb-route-jumped';

/** 带超时的可达性探测。返回毫秒耗时，失败返回 null。 */
async function probe(host: string, port: number): Promise<number | null> {
	const t0 = performance.now();
	const ctrl = new AbortController();
	const timer = setTimeout(() => ctrl.abort(), PROBE_TIMEOUT_MS);
	// 协议必须与当前页面一致：https 页面去 fetch http 资源属于 mixed content，
	// 浏览器会直接拦掉并记一条报错，探测结果全部变成「不可达」——比不知道更糟。
	const proto = typeof window === 'undefined' ? 'http:' : window.location.protocol;
	const isStdPort = (proto === 'https:' && port === 443) || (proto === 'http:' && port === 80);
	const portPart = isStdPort ? '' : `:${port}`;
	try {
		// no-cors：跨源读不到响应体与状态码，但能区分「连上」与「连不上」，
		// 这正是选路需要的唯一信息。cache:no-store 避免命中上一次的缓存结果。
		await fetch(`${proto}//${host}${portPart}/healthz`, {
			mode: 'no-cors',
			cache: 'no-store',
			signal: ctrl.signal
		});
		return Math.round(performance.now() - t0);
	} catch {
		return null;
	} finally {
		clearTimeout(timer);
	}
}

export default function RoutePage() {
	const [rows, setRows] = useState<Row[]>([]);
	const [port, setPort] = useState(3000);
	const [current, setCurrent] = useState<string | null>(null);
	const [excluded, setExcluded] = useState<CandidatesResponse['excluded']>([]);
	const [phase, setPhase] = useState<'loading' | 'probing' | 'ready' | 'error'>('loading');
	const [decision, setDecision] = useState<string>('');
	const [countdown, setCountdown] = useState<number | null>(null);
	const [errMsg, setErrMsg] = useState('');
	const jumpTarget = useRef<string | null>(null);
	const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

	const go = useCallback((host: string, p: number) => {
		// replace 而非 assign：不让探测页留在历史里，否则用户按返回会再次触发跳转。
		//
		// 协议跟随当前页面而不是写死 http：将来前面挂 HTTPS 反代时，写死 http 会拼出
		// `http://<域名>:3000` —— 协议不对且端口暴露，浏览器会直接拦或走到空气里。
		// 标准端口（http:80 / https:443）不显式拼端口，避免 `:443` 这类冗余写法。
		const proto = window.location.protocol;
		const isStdPort = (proto === 'https:' && p === 443) || (proto === 'http:' && p === 80);
		const portPart = isStdPort ? '' : `:${p}`;
		window.location.replace(`${proto}//${host}${portPart}/`);
	}, []);

	useEffect(() => {
		let cancelled = false;

		(async () => {
			let data: CandidatesResponse;
			try {
				const res = await fetch('/route/candidates', {cache: 'no-store'});
				data = await res.json();
			} catch {
				if (!cancelled) {
					setPhase('error');
					setErrMsg('无法读取候选地址表。请确认后端服务在运行。');
				}
				return;
			}
			if (cancelled) return;

			const p = data.port || 3000;
			setPort(p);
			setCurrent(data.current);
			setExcluded(data.excluded || []);

			// ★ 决策一律用这个局部变量，不能用上面的 `current` state。
			// React 的 setState 在本轮 effect 内**不会同步生效**，读到的仍是上一轮
			// 闭包里的旧值（初始为 null）⇒ baseline 恒为 null ⇒ 判定永远落到
			// 「!current」兜底分支 ⇒ **自动跳转从未真正触发过**，而且文案里的地址
			// 是拿 best.host 冒充的当前入口（实测发现：从 100.x 打开，文案却报 192.168.x）。
			// 这类 bug 构建、类型检查、curl 全都发现不了，只有真浏览器跑一遍才现形。
			const currentHost = data.current;

			const initial: Row[] = (data.candidates || []).map((c) => ({
				...c,
				ms: null,
				state: 'pending' as State
			}));
			setRows(initial);
			setPhase('probing');

			// 并发探测：串行会让总耗时等于各地址超时之和，手机上观感是「卡住」
			const results = await Promise.all(
				initial.map(async (r) => ({host: r.host, ms: await probe(r.host, p)}))
			);
			if (cancelled) return;

			const byHost = new Map(results.map((r) => [r.host, r.ms]));
			const probed: Row[] = initial.map((r) => {
				const ms = byHost.get(r.host) ?? null;
				return {...r, ms, state: ms === null ? ('fail' as State) : ('ok' as State)};
			});
			setRows(probed);
			setPhase('ready');

			// ── 决策：**无论结果如何都要落地，绝不把用户留在探测页** ──────────
			//
			// 这里踩过一次设计错误：最初只有「新地址明显更快」才跳，其余情况停在
			// 页面显示诊断文字。结果是用户点开书签，看到一句「当前入口已是最优」
			// 就没了 —— 他想要的是进应用，不是看测速报告。书签入口的语义是「进入」，
			// 探测只是过程中的手段。所以现在所有分支都以跳转收尾，区别只在
			// 「跳到哪个」和「给几秒反应时间」。
			//
			// `?stay=1` 是留给排查用的：想看诊断信息时加这个参数，页面就不跳。
			const stayMode = new URLSearchParams(window.location.search).get('stay') === '1';
			const reachable = probed.filter((r) => r.state === 'ok');

			// 全不可达时，当前 host 依然是可信的：页面本身就是从它加载出来的。
			if (reachable.length === 0) {
				if (!currentHost) {
					setDecision('所有候选地址都不可达，且无法识别当前入口。请检查网络后刷新。');
					return;
				}
				setDecision(`候选地址均探测失败，直接进入当前入口 ${currentHost}。`);
				if (!stayMode) {
					jumpTarget.current = currentHost;
					setCountdown(1);
				}
				return;
			}

			// 基准 = 当前 host 的实测延迟；当前 host 不在候选表里时无法比较。
			const currentRow = currentHost ? probed.find((r) => r.host === currentHost) : null;
			const baseline = currentRow?.state === 'ok' ? (currentRow.ms as number) : null;

			// 并列时 reduce 取第一个，所以「最优」不天然等于「当前」——
			// 必须显式判断是否值得为这点差异多转一次，否则会无谓地换入口。
			const best = reachable.reduce((a, b) => ((a.ms as number) <= (b.ms as number) ? a : b));

			const worthSwitching =
				currentHost !== null &&
				best.host !== currentHost &&
				baseline !== null &&
				(best.ms as number) < baseline * SWITCH_THRESHOLD;

			// 默认目标就是最优地址；不值得切换时退回当前入口（省一次跳转）
			let target = worthSwitching ? best.host : (currentHost ?? best.host);
			let why = worthSwitching
				? `${best.kind === 'lan' ? '局域网' : best.label}快 ` +
					`${Math.round((1 - (best.ms as number) / (baseline as number)) * 100)}%` +
					`（${best.ms} ms vs ${baseline} ms）`
				: baseline !== null
					? `当前入口 ${baseline} ms，其他候选无显著优势`
					: `当前入口 ${currentHost ?? best.host}`;

			// 防循环：同一会话内已经跳过同一个目标，就改回当前入口。
			// 注意这里**不是**「停止跳转」——停止跳转等于把用户留在页面上，
			// 那正是上面说的设计错误。改成跳到当前入口，照样进应用。
			const alreadyJumped = sessionStorage.getItem(JUMP_GUARD_KEY);
			if (alreadyJumped === target) {
				target = currentHost ?? best.host;
				why = `${target}（本会话已跳过一轮，避免来回弹跳）`;
			}

			if (stayMode) {
				setDecision(`诊断模式（?stay=1），不自动跳转。将进入：${target} —— ${why}`);
				return;
			}

			setDecision(`正在进入 ${target} —— ${why}`);
			jumpTarget.current = target;
			// 换入口给 2 秒让用户看清「跳到哪」，原地进入只给 1 秒，尽快落地
			setCountdown(target === currentHost ? 1 : 2);
		})();

		return () => {
			cancelled = true;
		};
		// current 在 effect 内被 setState 更新，不作为依赖避免重复探测
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, []);

	// 倒计时跳转
	useEffect(() => {
		if (countdown === null) return;
		if (countdown <= 0) {
			const target = jumpTarget.current;
			if (target) {
				sessionStorage.setItem(JUMP_GUARD_KEY, target);
				go(target, port);
			}
			return;
		}
		timerRef.current = setInterval(() => {
			setCountdown((c) => (c === null ? null : c - 1));
		}, 1000);
		return () => {
			if (timerRef.current) clearInterval(timerRef.current);
		};
	}, [countdown, go, port]);

	const badge = (r: Row) => {
		if (r.state === 'pending') return {text: '探测中', color: 'var(--dim)'};
		if (r.state === 'fail') return {text: '不可达', color: 'var(--bad)'};
		return {text: `${r.ms} ms`, color: 'var(--good)'};
	};

	return (
		<main className="wrap">
			<h1>正在进入 WorkBuddy Manager</h1>
			<p className="lead">
				实测这台机器各条接入路径的延迟，自动走最快的一条，随后自动进入。
			</p>

			{phase === 'loading' || phase === 'probing' ? (
				<p className="lead dim">探测中…</p>
			) : null}

			{phase === 'error' ? <p className="lead bad">{errMsg}</p> : null}

			<ul className="list">
				{rows.map((r) => {
					const b = badge(r);
					const isCurrent = r.host === current;
					return (
						<li key={r.host} className={isCurrent ? 'row current' : 'row'}>
							<div className="info">
								<div className="addr">{r.host}</div>
								<div className="meta">
									{r.label}
									{isCurrent ? ' · 当前入口' : ''}
								</div>
							</div>
							<span className="ms" style={{color: b.color}}>
								{b.text}
							</span>
							<button
								type="button"
								className="pick"
								disabled={r.state === 'fail'}
								onClick={() => go(r.host, port)}>
								进入
							</button>
						</li>
					);
				})}
			</ul>

			{decision ? <p className="decide">{decision}</p> : null}

			{countdown !== null ? (
				<div className="jump">
					<span>{countdown} 秒后自动切换</span>
					<button type="button" onClick={() => setCountdown(null)}>
						留在此页
					</button>
				</div>
			) : null}

			{excluded.length > 0 ? (
				<details className="diag">
					<summary>已排除的地址（{excluded.length}）</summary>
					<ul>
						{excluded.map((e) => (
							<li key={e.host}>
								<code>{e.host}</code>（{e.iface}）— {e.reason}
							</li>
						))}
					</ul>
				</details>
			) : null}

			<p className="tip">
				建议把这个页面的 <b>Tailscale 地址</b> 存成手机书签：它在任何网络下都能打开，
				打开后会自动切到更快的局域网。端口 {port}。
			</p>

			<style jsx>{`
				.wrap {
					max-width: 640px;
					margin: 0 auto;
					padding: 28px 20px 48px;
					font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
					color: var(--fg);
					min-height: 100vh;
					box-sizing: border-box;
				}
				/* 铺满视口的实底。根 layout 的 BackgroundApplier 会把壁纸写到 body 上，
				   探测页必须自己盖一层，否则线稿会从文字下面透出来。
				   用 ::before 而不是给 .wrap 本身加背景——.wrap 只有 640px 宽，
				   两侧会漏出 body 的壁纸。 */
				.wrap::before {
					content: '';
					position: fixed;
					inset: 0;
					background: var(--bg);
					z-index: -1;
				}
				h1 {
					font-size: 22px;
					font-weight: 650;
					margin: 0 0 8px;
					letter-spacing: -0.01em;
				}
				.lead {
					font-size: 14px;
					line-height: 1.6;
					margin: 0 0 20px;
					color: var(--fg2);
				}
				.dim {
					color: var(--dim);
				}
				.bad {
					color: var(--bad);
				}
				.list {
					list-style: none;
					padding: 0;
					margin: 0 0 18px;
					display: flex;
					flex-direction: column;
					gap: 10px;
				}
				.row {
					display: flex;
					align-items: center;
					gap: 12px;
					padding: 14px 16px;
					border-radius: 12px;
					border: 1px solid var(--line);
					background: var(--card);
				}
				.row.current {
					border-color: var(--accent);
				}
				.info {
					flex: 1;
					min-width: 0;
				}
				.addr {
					font-size: 15px;
					font-weight: 600;
					font-variant-numeric: tabular-nums;
					word-break: break-all;
				}
				.meta {
					font-size: 12px;
					color: var(--dim);
					margin-top: 2px;
				}
				.ms {
					font-size: 13px;
					font-variant-numeric: tabular-nums;
					min-width: 62px;
					text-align: right;
				}
				.pick {
					font-size: 13px;
					padding: 7px 14px;
					border-radius: 8px;
					border: 1px solid var(--line);
					background: var(--btn);
					color: var(--fg);
					cursor: pointer;
				}
				.pick:disabled {
					opacity: 0.4;
					cursor: default;
				}
				.decide {
					font-size: 13px;
					line-height: 1.6;
					padding: 12px 14px;
					border-radius: 10px;
					background: var(--card);
					border: 1px solid var(--line);
					margin: 0 0 16px;
					color: var(--fg2);
				}
				.jump {
					display: flex;
					align-items: center;
					justify-content: space-between;
					gap: 12px;
					font-size: 13px;
					padding: 12px 14px;
					border-radius: 10px;
					background: var(--accent-soft);
					margin: 0 0 16px;
				}
				.jump button {
					font-size: 13px;
					padding: 6px 12px;
					border-radius: 8px;
					border: 1px solid var(--line);
					background: transparent;
					color: var(--fg);
					cursor: pointer;
				}
				.diag {
					font-size: 12px;
					color: var(--dim);
					margin: 0 0 16px;
				}
				.diag ul {
					margin: 8px 0 0;
					padding-left: 18px;
					line-height: 1.7;
				}
				.diag code {
					font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
				}
				.tip {
					font-size: 12px;
					line-height: 1.7;
					color: var(--dim);
					border-top: 1px solid var(--line);
					padding-top: 16px;
					margin: 0;
				}
			`}</style>

			<style jsx global>{`
				/* 变量挂在 .wrap 上而不是 :root：globals.css 里有同名通用变量
				   （--accent / --line / --bg 等），挂到 :root 会跨页面污染主应用。
				   CSS 变量按继承生效，.wrap 内部照常可用。 */
				.wrap {
					--bg: #faf9fc;
					--fg: #1a1720;
					--fg2: #4a4653;
					--dim: #6b6673;
					--line: rgba(26, 23, 32, 0.14);
					--card: rgba(255, 255, 255, 0.9);
					--btn: rgba(26, 23, 32, 0.05);
					--accent: #7c5fd3;
					--accent-soft: rgba(124, 95, 211, 0.1);
					--good: #1a7f47;
					--bad: #c0392b;
				}
				@media (prefers-color-scheme: dark) {
					.wrap {
						--bg: #131116;
						--fg: #f1eff6;
						--fg2: #c9c6ce;
						--dim: #a5a2a9;
						--line: rgba(255, 255, 255, 0.13);
						--card: rgba(45, 43, 49, 0.92);
						--btn: rgba(255, 255, 255, 0.08);
						--accent: #b48df5;
						--accent-soft: rgba(180, 141, 245, 0.14);
						--good: #6ed889;
						--bad: #ef675c;
					}
				}
			`}</style>
		</main>
	);
}
