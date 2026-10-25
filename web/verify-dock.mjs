/**
 * Dock 交互自动化验证（2026-10-24）
 *
 * 为什么需要它：
 *   前面 5 轮"改代码 → 让用户刷新 → 用户说没效果"循环失败 ✗
 *   原因是我的验证只到"构建通过"，从未真正驱动过浏览器 ✓
 *   ⇒ 这个脚本用真实 Edge（Chromium）模拟鼠标：
 *     按下 → 等 260ms → 横向拖动 → 读取 DOM
 *   ⇒ 结果是可复现的、可自动断言的真值 ✓
 *
 * 用法：
 *   node /tmp/verify-dock.mjs
 */

import puppeteer from 'puppeteer-core';

const EDGE = '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge';
const URL = 'http://127.0.0.1:3000/dev-dock';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const results = [];
function check(name, pass, detail = '') {
  results.push({name, pass, detail});
  console.log(`${pass ? '  ✓' : '  ✗'} ${name}${detail ? '  → ' + detail : ''}`);
}

const browser = await puppeteer.launch({
  executablePath: EDGE,
  headless: true,
  args: ['--no-sandbox', '--disable-gpu'],
});

const page = await browser.newPage();
await page.setViewport({width: 900, height: 900, deviceScaleFactor: 1});

const errors = [];
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
page.on('console', (m) => {
  if (m.type() === 'error') errors.push('console: ' + m.text());
});

console.log('\n════ 加载页面 ════');
await page.goto(URL, {waitUntil: 'networkidle0', timeout: 30000});
await sleep(800);

// ★ 诊断：确认没被跳转、DOM 里到底有什么
console.log('  当前 URL:', page.url());
const html = await page.content();
console.log('  DOM 大小:', html.length, '字节');
console.log('  含 tg-dock:', html.includes('tg-dock'));
console.log('  含 Dock 预览:', html.includes('Dock 预览'));
console.log('  含 login:', html.includes('/login'));
const bodyText = await page.evaluate(() => document.body?.innerText?.slice(0, 120) ?? '(空)');
console.log('  body 文本前 120 字:', JSON.stringify(bodyText));

// ① Dock 是否存在
const dock = await page.$('[data-slot="tg-dock"]');
check('Dock 元素渲染', !!dock);
if (!dock) {
  console.log('\n  ⚠ 组件没渲染，后面的测试没有意义');
  console.log('  运行时错误:', errors.length ? errors : '（无）');
  await browser.close();
  process.exit(1);
}

// ② 4 个 tab
const items = await page.$$('[data-slot="dock-item"]');
check('4 个 tab', items.length === 4, `实际 ${items.length}`);

// ③ 初始 data-dragging
const d0 = await page.$eval('[data-slot="tg-dock"]', (el) => el.dataset.dragging);
check('初始 data-dragging=false', d0 === 'false', `实际 "${d0}"`);

// ④ 长按 + 拖动 ⇒ dragging 变 true
console.log('\n════ 长按拖选 ════');
const box = await dock.boundingBox();
const y = box.y + box.height / 2;
const x0 = box.x + box.width * 0.12;

await page.mouse.move(x0, y);
await page.mouse.down();
await sleep(260);                       // 超过 200ms 的判定阈值
await page.mouse.move(x0 + 90, y, {steps: 10});   // 横向拖 90px（约 1.7 个 tab）
await sleep(120);

const d1 = await page.$eval('[data-slot="tg-dock"]', (el) => el.dataset.dragging);
check('拖动中 data-dragging=true', d1 === 'true', `实际 "${d1}"`);

// ⑤ 拖动后选中项应变化（从 index 0 变到后面）
const activeAfterDrag = await page.$$eval('[data-slot="dock-item"]', (els) =>
  els.findIndex((el) => el.dataset.active === 'true'),
);
check('拖动后选中项后移', activeAfterDrag > 0, `activeIndex=${activeAfterDrag}`);

// ⑥ 抬手后 dragging 回 false
await page.mouse.up();
await sleep(150);
const d2 = await page.$eval('[data-slot="tg-dock"]', (el) => el.dataset.dragging);
check('抬手后 data-dragging=false', d2 === 'false', `实际 "${d2}"`);

// ⑦ 高亮在拖动时应大于 0（检查 --glow 变量）
console.log('\n════ 掠过高亮 ════');
const x1 = box.x + box.width * 0.12;
await page.mouse.move(x1, y);
await page.mouse.down();
await sleep(260);
await page.mouse.move(box.x + box.width * 0.45, y, {steps: 12});
await sleep(120);
const glows = await page.$$eval('[data-slot="dock-item"]', (els) =>
  els.map((el) => Number(getComputedStyle(el).getPropertyValue('--glow') || 0)),
);
check('拖动时有 glow 值 > 0', glows.some((g) => g > 0), `[${glows.map((g) => g.toFixed(2)).join(', ')}]`);
await page.mouse.up();
await sleep(200);

// ⑧ 滑块：改 Dock 不透明度 ⇒ --dock-bg-a 变化
console.log('\n════ 滑块联动 ════');
const beforeVar = await page.$eval('[data-slot="tg-dock"]', (el) =>
  getComputedStyle(el).getPropertyValue('--dock-bg-a').trim(),
);
// 找到范围滑块里 aria-label="Dock 不透明度" 的那个
const range = await page.$('input[aria-label="Dock 不透明度"]');
if (range) {
  await page.$eval('input[aria-label="Dock 不透明度"]', (el) => {
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      'value',
    ).set;
    setter.call(el, '0.9');
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  });
  await sleep(300);
  const afterVar = await page.$eval('[data-slot="tg-dock"]', (el) =>
    getComputedStyle(el).getPropertyValue('--dock-bg-a').trim(),
  );
  const afterBg = await page.$eval('[data-slot="tg-dock"]', (el) =>
    getComputedStyle(el).backgroundColor,
  );
  check('滑块改变 --dock-bg-a', beforeVar !== afterVar, `${beforeVar || '空'} → ${afterVar || '空'}`);
  check('计算背景色随之变化', afterBg !== 'rgba(0, 0, 0, 0)', afterBg);
} else {
  check('找到不透明度滑块', false);
}

// ⑨ 运行时错误
console.log('\n════ 运行时错误 ════');
check('无 console/page 错误', errors.length === 0, errors.join(' | ') || '无');

// 汇总
const passed = results.filter((r) => r.pass).length;
console.log(`\n════ 汇总 ════\n  ${passed}/${results.length} 通过`);

await browser.close();
process.exit(passed === results.length ? 0 : 2);
