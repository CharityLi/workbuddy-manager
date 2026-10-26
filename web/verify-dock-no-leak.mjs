import fs from 'node:fs';
import puppeteer from 'puppeteer-core';
const TOKEN = fs.readFileSync('/tmp/.t4','utf8').trim();
const r = [];
const chk = (n, ok, d='') => { r.push({n,ok,d}); console.log(`${ok?'  ✓':'  ✗'} ${n}${d?'  → '+d:''}`); };

const b = await puppeteer.launch({executablePath:'/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge', headless:true, args:['--no-sandbox','--disable-gpu']});
const p = await b.newPage();
await p.setViewport({width:1280, height:900});
await p.setCookie({name:'wb_session', value:TOKEN, domain:'127.0.0.1', path:'/'});
await p.goto('http://127.0.0.1:3000/dashboard', {waitUntil:'networkidle2', timeout:40000});
await new Promise(x=>setTimeout(x,3000));

const s = await p.evaluate(() => {
  const leakSlots = document.querySelectorAll('[data-slot="dock-quick-add"], [data-slot="dock-profile-trigger"]').length;
  // 顶部 60px 内的孤立图标（之前的泄漏表现：left≈632，宽高 16）
  const straySvg = [...document.querySelectorAll('svg')].filter(el => {
    const b = el.getBoundingClientRect();
    return b.top < 60 && b.width > 8 && b.width < 30 && b.left > 550 && b.left < 750;
  }).length;
  const root = document.querySelector('[data-slot="dock-root"]');
  const btns = root ? [...root.querySelectorAll('button')] : [];
  return {
    leakSlots, straySvg,
    texts: btns.map(x => (x.textContent||'').replace(/\s+/g,'')),
    hasQuickAdd: btns.some(x => /快速添加/.test(x.textContent||'')),
    hasProfile: btns.some(x => /个人信息/.test(x.textContent||'')),
    dockRect: root ? (()=>{const q=root.getBoundingClientRect();return `top=${Math.round(q.top)} h=${Math.round(q.height)}`;})() : 'none'
  };
});
chk('顶部无泄漏的 dock-quick-add / dock-profile-trigger', s.leakSlots === 0, `${s.leakSlots} 个`);
chk('顶部无孤立图标', s.straySvg === 0, `${s.straySvg} 个`);
chk('Dock 仍在底部', /top=[5-9]\d\d/.test(s.dockRect), s.dockRect);
chk('Dock 保留「快速添加」', s.hasQuickAdd);
chk('Dock 保留「个人信息」', s.hasProfile);
console.log('  Dock 项:', s.texts.join(' | '));

// 点「快速添加」应打开添加账号弹窗
await p.evaluate(() => {
  const root = document.querySelector('[data-slot="dock-root"]');
  [...root.querySelectorAll('button')].find(x => /快速添加/.test(x.textContent||''))?.click();
});
await new Promise(x=>setTimeout(x,1200));
const addDlg = await p.evaluate(() => {
  const d = document.querySelector('[role="dialog"]');
  return {open: !!d, text: (d?.textContent||'').replace(/\s+/g,'').slice(0,40)};
});
chk('点「快速添加」打开弹窗', addDlg.open, addDlg.text);

// 关掉，再点「个人信息」
await p.keyboard.press('Escape');
await new Promise(x=>setTimeout(x,800));
await p.evaluate(() => {
  const root = document.querySelector('[data-slot="dock-root"]');
  [...root.querySelectorAll('button')].find(x => /个人信息/.test(x.textContent||''))?.click();
});
await new Promise(x=>setTimeout(x,1500));
const profDlg = await p.evaluate(() => {
  const ds = [...document.querySelectorAll('[role="dialog"]')];
  const t = ds.map(d=>d.textContent||'').join(' ');
  return {open: ds.length>0, hasLogout: /退出登录|Logout|注销/.test(t), text: t.replace(/\s+/g,'').slice(0,40)};
});
chk('点「个人信息」打开弹窗', profDlg.open, profDlg.text);
chk('弹窗含「退出登录」', profDlg.hasLogout);

await b.close();
const bad = r.filter(x=>!x.ok);
console.log(`\n结果：${r.length-bad.length}/${r.length} 通过`);
if (bad.length) { console.log('失败：'); bad.forEach(x=>console.log(`  - ${x.n} ${x.d}`)); process.exit(1); }
