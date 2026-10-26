// 验证「设置 → 更新日志」的两个标签：本地 / 上游，并截图。
import fs from 'node:fs';
import puppeteer from 'puppeteer-core';

const TOKEN = fs.readFileSync('/tmp/.t6', 'utf8').trim();
const BASE = 'http://127.0.0.1:3000';

const browser = await puppeteer.launch({
  executablePath: '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
  headless: 'new',
  args: ['--no-sandbox'],
});
const page = await browser.newPage();
await page.setViewport({width: 1440, height: 1000, deviceScaleFactor: 1});
await browser.setCookie({name: 'wb_session', value: TOKEN, domain: '127.0.0.1', path: '/'});

const out = {};
await page.goto(`${BASE}/settings/changelog`, {waitUntil: 'networkidle2'});
await new Promise((r) => setTimeout(r, 1200));

// 本地标签（默认）
out.localTabActive = await page.$$eval('button', (bs) =>
  bs.map((b) => b.textContent?.trim()).filter((t) => t === '本地更新日志' || t === '上游更新'));
await page.screenshot({path: '/tmp/upstream-local.png'});

// 切到上游标签
const clicked = await page.$$eval('button', (bs) => {
  const b = bs.find((x) => x.textContent?.trim() === '上游更新');
  if (b) { b.click(); return true; }
  return false;
});
out.clicked = clicked;
await new Promise((r) => setTimeout(r, 1200));

out.upstream = await page.evaluate(() => {
  const text = document.body.innerText;
  return {
    hasTitle: text.includes('上游仓库发布版本'),
    hasBehind: text.includes('上游已发布') || text.includes('本机已是最新发布版本'),
    hasNever: text.includes('只记录，不跟随'),
    hasLatest: text.includes('v2.10.2'),
    hasLocal: text.includes('v2.9.0'),
    hasHeads: text.includes('上游最新版本') && text.includes('本机当前版本'),
    hasListHint: text.includes('v1.0.87') && /32 个发布版本/.test(text),
    versionBlocks: (text.match(/v\d+\.\d+\.\d+/g) || []).length,
    hScroll: document.documentElement.scrollWidth > document.documentElement.clientWidth,
  };
});
await page.screenshot({path: '/tmp/upstream-tab.png', fullPage: true});
await page.screenshot({path: '/tmp/upstream-view.png'});

// 移动端
await page.setViewport({width: 390, height: 844, deviceScaleFactor: 2});
await new Promise((r) => setTimeout(r, 600));
await page.screenshot({path: '/tmp/upstream-mobile.png', fullPage: false});
out.mobileHScroll = await page.evaluate(
  () => document.documentElement.scrollWidth > document.documentElement.clientWidth);

console.log(JSON.stringify(out, null, 2));
await browser.close();
