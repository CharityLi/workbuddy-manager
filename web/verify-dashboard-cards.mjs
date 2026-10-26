import fs from 'node:fs';
import puppeteer from 'puppeteer-core';
const TOKEN = fs.readFileSync('/tmp/.t5','utf8').trim();
const b = await puppeteer.launch({executablePath:'/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge', headless:true, args:['--no-sandbox','--disable-gpu']});
const p = await b.newPage();
await p.setViewport({width:1280, height:1000});
await p.setCookie({name:'wb_session', value:TOKEN, domain:'127.0.0.1', path:'/'});
await p.goto('http://127.0.0.1:3000/dashboard', {waitUntil:'networkidle2', timeout:45000});
await new Promise(r=>setTimeout(r,4000));

const info = await p.evaluate(() => {
  // 找「反代上游」面板里的按钮卡片
  const all = [...document.querySelectorAll('button[aria-pressed]')];
  return {
    cards: all.map(b => ({
      text: (b.textContent||'').replace(/\s+/g,' ').trim(),
      pressed: b.getAttribute('aria-pressed'),
    })),
    // 「反代上游」标题下的面板
    panelText: [...document.querySelectorAll('div')]
      .filter(d => /反代上游/.test(d.textContent||'') && (d.textContent||'').length < 400)
      .map(d => (d.textContent||'').replace(/\s+/g,' ').trim())[0] || '',
  };
});
console.log('=== 分组卡片 ===');
if (!info.cards.length) console.log('  ✗ 没找到卡片');
for (const c of info.cards) console.log(`  [${c.pressed === 'true' ? '选中' : '    '}] ${c.text}`);
console.log('\n=== 面板文本（截断）===');
console.log('  ' + info.panelText.slice(0, 220));

// 点第二张卡片，验证详情切换
if (info.cards.length > 1) {
  await p.evaluate(() => {
    const btns = [...document.querySelectorAll('button[aria-pressed]')];
    btns[1].click();
  });
  await new Promise(r=>setTimeout(r,1200));
  const after = await p.evaluate(() => ({
    pressed: [...document.querySelectorAll('button[aria-pressed]')].map(b => b.getAttribute('aria-pressed')),
    detail: (document.body.textContent||'').replace(/\s+/g,' ').match(/健康账号\s*\d+/)?.[0] || '(未找到健康账号数字)',
  }));
  console.log('\n=== 点击第二张卡片后 ===');
  console.log('  aria-pressed:', JSON.stringify(after.pressed));
  console.log('  详情:', after.detail);
}
await b.close();
