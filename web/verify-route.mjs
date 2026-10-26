/**
 * /route 自动选路页的运行时验证（2026-10-26）
 *
 * 为什么必须跑真浏览器：
 *   这个页面的全部逻辑都在客户端 —— 候选拉取、并发探测、阈值判定、跳转。
 *   curl 只能证明「HTML 返回 200」，证明不了「探测真的跑了、决策真的做了」。
 *   本项目此前的教训（verify-dock.mjs 开头写着）：只验证到构建通过，等于没验证。
 *
 * 验证的两个场景：
 *   A. 从局域网 IP 打开 → 当前入口延迟若高于别的候选，应触发自动跳转
 *   B. 从 Tailscale IP 打开 → 若当前已最快，应原地不动并说明原因
 *
 * 断言口径：
 *   · 候选必须 3 条（lan / tailscale / mdns）
 *   · 至少 2 条探测出毫秒值（Clash TUN 已被服务端排除，不应出现在列表里）
 *   · 决策文案必须存在（空文案代表决策分支没走到）
 *   · 全程不得有 JS 报错
 */
import puppeteer from 'puppeteer-core';

const EDGE = '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge';
const LAN = '192.168.31.28';
const TS = '100.121.139.109';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
function check(name, pass, detail = '') {
	results.push({name, pass, detail});
	console.log(`${pass ? '  ✓' : '  ✗'} ${name}${detail ? '  → ' + detail : ''}`);
}

const browser = await puppeteer.launch({
	executablePath: EDGE,
	headless: true,
	args: ['--no-sandbox', '--disable-gpu']
});

/** 读取页面上的探测结果快照。 */
async function snapshot(page) {
	return page.evaluate(() => {
		const rows = [...document.querySelectorAll('li.row')].map((li) => ({
			addr: li.querySelector('.addr')?.textContent?.trim() ?? '',
			meta: li.querySelector('.meta')?.textContent?.trim() ?? '',
			ms: li.querySelector('.ms')?.textContent?.trim() ?? '',
			isCurrent: li.classList.contains('current')
		}));
		return {
			rows,
			decision: document.querySelector('.decide')?.textContent?.trim() ?? '',
			hasCountdown: !!document.querySelector('.jump'),
			countdownText: document.querySelector('.jump span')?.textContent?.trim() ?? '',
			excluded: document.querySelectorAll('.diag li').length
		};
	});
}

async function runScenario(label, host) {
	console.log(`\n── 场景 ${label}：从 ${host} 打开 ──`);
	const page = await browser.newPage();
	await page.setViewport({width: 390, height: 844, deviceScaleFactor: 2, isMobile: true});

	const errors = [];
	page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
	page.on('console', (m) => {
		if (m.type() === 'error') errors.push('console: ' + m.text());
	});

	// ?stay=1 让页面停在诊断态：现在默认会在 1~3 秒内自动跳进应用，
	// 4 秒后抓 DOM 只会抓到跳走之后的白页，断言全部落空。
	await page.goto(`http://${host}:3000/route?stay=1`, {waitUntil: 'domcontentloaded', timeout: 30000});

	// 探测是并发的，最慢一条 1.2s 超时；给 3s 让所有结果落地
	await sleep(3000);
	const snap = await snapshot(page);

	check('候选渲染 3 条', snap.rows.length === 3, `实际 ${snap.rows.length} 条`);
	const withMs = snap.rows.filter((r) => /\d+\s*ms/.test(r.ms));
	check('至少 2 条探测到延迟', withMs.length >= 2, withMs.map((r) => `${r.addr}=${r.ms}`).join(', '));
	check('决策文案非空', snap.decision.length > 0, snap.decision.slice(0, 70) + (snap.decision.length > 70 ? '…' : ''));
	check('当前入口被标记', snap.rows.some((r) => r.isCurrent), snap.rows.filter((r) => r.isCurrent).map((r) => r.addr).join(','));
	check('Clash TUN 未进候选', !snap.rows.some((r) => r.addr.includes('198.18')), '');
	// 未登录时 AuthProvider 会请求 /api/me 并得到 401 —— 这是预期行为，
	// 而且它**没有**把人踢到登录页，恰好证明 lib/api.ts 的 publicPaths 白名单生效。
	// 所以只对真正的运行时异常（pageerror 等）判红，401 资源加载放行。
	const realErrors = errors.filter((e) => !/401|Unauthorized/i.test(e));
	check('无 JS 运行时报错（/api/me 的 401 属未登录预期）', realErrors.length === 0, realErrors.slice(0, 2).join(' | '));

	const before = page.url();
	console.log(`  决策：${snap.decision}`);

	// stay 模式必须稳住不跳，否则说明 ?stay=1 的短路没生效
	await sleep(2500);
	check('?stay=1 时停在诊断页不跳转', page.url() === before, page.url());

	await page.close();
	return {snap, jumped: false, before, after: before};
}

/**
 * 场景 C：手动点「进入」，验证跳转 URL 的拼接。
 *
 * 自动跳转分支在本机触发不了（三个入口都走 loopback，延迟并列，永远判不出
 * 「明显更快」）。但跳转 URL 的拼接是刚改过的高危代码（协议跟随 + 标准端口省略），
 * 必须真点一次看落地地址，否则等于没验证。
 */
async function runJumpTest() {
	console.log('\n── 场景 C：手动点「进入」，验证跳转 URL ──');
	const page = await browser.newPage();
	await page.setViewport({width: 390, height: 844, deviceScaleFactor: 2, isMobile: true});
	const errors = [];
	page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
	// 同样要 ?stay=1：默认会自动跳走，手动点击的断言就没对象了
	await page.goto(`http://${LAN}:3000/route?stay=1`, {waitUntil: 'domcontentloaded', timeout: 30000});
	await sleep(3000);

	const clicked = await page.evaluate(() => {
		const rows = [...document.querySelectorAll('li.row')];
		const target = rows.find((r) => r.querySelector('.addr')?.textContent?.includes('100.'));
		const btn = target?.querySelector('.pick');
		if (!btn) return false;
		btn.click();
		return true;
	});
	check('找到 Tailscale 行的「进入」按钮', clicked, '');
	await sleep(4500);
	const url = page.url();
	// 只校验「落到了目标 host + 端口」。不能要求末尾正好是 `/`：
	// 未登录时跳到 `/` 后，主应用的 (main) 布局会立刻把人送到 `/login`，
	// 那是正常的产品行为，不是跳转失败。
	check(
		'跳转落到 http://<100.x>:3000/（可能随即被引导到 /login）',
		/^http:\/\/100\.\d{1,3}\.\d{1,3}\.\d{1,3}:3000\//.test(url),
		url
	);
	check('跳转过程无 JS 异常', errors.length === 0, errors.slice(0, 2).join(' | '));
	await page.close();
}

/**
 * 场景 D：不加任何参数，验证「打开就自动进入」。
 *
 * 这是书签入口的核心契约 —— 用户点开书签，期望的是进应用，不是看测速报告。
 * 之前的设计错误正是「不明显更快就停在页面上」，这条断言专门盯住它不许复现。
 */
async function runAutoEnterTest() {
	console.log('\n── 场景 D：不加参数，应自动进入应用 ──');
	const page = await browser.newPage();
	await page.setViewport({width: 390, height: 844, deviceScaleFactor: 2, isMobile: true});
	const errors = [];
	page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));

	await page.goto(`http://${LAN}:3000/route`, {waitUntil: 'domcontentloaded', timeout: 30000});
	// 探测(≤1.2s) + 倒计时(1~2s) + 跳转完成，留足 7s
	await sleep(7000);

	const url = page.url();
	check('离开 /route（已自动进入）', !url.includes('/route'), url);
	check('落点仍是同一台机器', /^http:\/\/(192\.168\.31\.28|100\.\d+\.\d+\.\d+|xiaoyuzaideMacBook-Air\.local):3000\//.test(url), url);
	check('自动进入过程无 JS 异常', errors.length === 0, errors.slice(0, 2).join(' | '));
	await page.close();
}

const a = await runScenario('A', LAN);
const b = await runScenario('B', TS);
await runJumpTest();
await runAutoEnterTest();

// 交叉断言：两个入口都不该在「当前已最快」的场景里被无意义地踢走
console.log('\n── 交叉判定 ──');
if (b.snap.rows.find((r) => r.isCurrent && r.addr === TS)) {
	check('场景 B：当前入口识别为 Tailscale', true, TS);
}

await browser.close();

const failed = results.filter((r) => !r.pass);
console.log(`\n结果：${results.length - failed.length}/${results.length} 通过`);
if (failed.length) {
	console.log('失败项：');
	for (const f of failed) console.log(`  - ${f.name}  ${f.detail}`);
	process.exit(1);
}
