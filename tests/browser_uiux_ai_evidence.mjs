import {colorMath} from './uiux_color_math.mjs';
import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {state} from './browser_ai_connections.mjs';

// Measure rendered colors after ancestor backgrounds are composed, not token literals.
function measure(colorMath) {
  const {rgba, over, background, contrast} = colorMath();
  const visible = el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
  const text = [], targets = [], borders = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el) || ['SCRIPT', 'STYLE', 'OPTION'].includes(el.tagName)) continue;
    const s = getComputedStyle(el), box = el.getBoundingClientRect();
    const ownText = [...el.childNodes].filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent.trim()).join(' ').trim();
    const bg = background(el);
    if (ownText && !el.matches('.empty-state-icon, .toast-icon')) {
      const fg = rgba(s.color);
      let opacity = 1;
      for (let node = el; node; node = node.parentElement) opacity *= Number(getComputedStyle(node).opacity);
      fg[3] = (fg[3] ?? 1) * opacity;
      const color = over(fg, bg);
      const large = parseFloat(s.fontSize) >= 24 || (parseFloat(s.fontSize) >= 18.66 && Number(s.fontWeight) >= 700);
      text.push({text: ownText.slice(0, 80), color, background: bg, ratio: contrast(color, bg), minimum: large ? 3 : 4.5});
    }
    if (el.matches('button, a.btn, a.btn-primary, [role="tab"]')) targets.push({text: (el.textContent.trim() || el.getAttribute('aria-label')).slice(0, 50), width: box.width, height: box.height});
    if (el.matches('input:not([type="checkbox"]), textarea, select, .btn-secondary')) {
      const color = rgba(s.borderTopColor).slice(0, 3);
      borders.push({name: el.id || el.name || el.textContent.slice(0, 30), inside: contrast(color, bg), outside: contrast(color, background(el.parentElement))});
    }
  }
  return {overflow: document.documentElement.scrollWidth > innerWidth, text, targets, borders};
}

export default async function check(page) {
  page.setDefaultTimeout(8000);
  const base = new URL(page.url()).origin;
  const out = new URL('./uiux/evidence/125/', import.meta.url);
  await mkdir(out, {recursive:true});
  let data, writes = 0;
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname.startsWith('/admin/ai/')) writes++;
  });
  await page.route('**/admin/ai/state', route => route.fulfill({json:data}));
  await page.route('**/admin/ai/models/1/update', async route => {
    await route.fulfill({status:409, json:{detail:'PRIVATE-SENTINEL'}});
  });
  const rows = [];
  for (const theme of ['light', 'dark']) {
    await page.emulateMedia({colorScheme:theme, reducedMotion:'reduce'});
    for (const width of [320, 390, 767, 768, 769, 1024, 1440]) {
      await page.setViewportSize({width, height:900});
      for (const screen of ['roles', 'blocked', 'master', 'editor', 'conflict']) {
        data = state();
        data.models[0].display_name = '긴 모델명 학생봇 멘토 사후 분석 설정 '.repeat(5);
        data.models[0].model_id = 'synthetic-long-model-name-'.repeat(8);
        if (screen === 'blocked') {
          data.models[0].capabilities.metadata_conflict = true;
          data.models[1].provider = 'anthropic';
          data.models[1].capabilities = {fields:[]};
        }
        if (screen === 'master') data.master_key_available = false;
        await page.goto(base + '/admin/ai');
        await page.locator('[data-provider="google"]').waitFor();
        if (screen === 'editor') {
          await page.getByRole('button', {name:'작성 기본값·호출 설정 수정'}).focus();
          await page.keyboard.press('Enter');
          assert(await page.getByLabel('학생봇 작성 기본 모델', {exact:true}).evaluate(el => el === document.activeElement));
          assert.equal(await page.getByRole('dialog').count(), 0, 'inline editor remains nonmodal');
          await page.keyboard.press('Shift+Tab');
          assert(await page.evaluate(() => !document.activeElement.closest('#ai-editor')), 'nonmodal editor allows keyboard focus outside');
          await page.getByLabel('전체 호출 한도', {exact:true}).focus();
        }
        if (screen === 'conflict') {
          await page.locator('[data-model="1"]').getByRole('button', {name:'표시명·활성·옵션 수정'}).click();
          await page.getByLabel('표시명', {exact:true}).fill('수정한 긴 모델 이름 보존');
          await page.getByRole('button', {name:'모델 설정 저장', exact:true}).click();
          await page.getByRole('alert').filter({hasText:'충돌'}).waitFor();
          assert.equal(await page.getByLabel('표시명', {exact:true}).inputValue(), '수정한 긴 모델 이름 보존');
        }
        await page.evaluate(async () => {
          await Promise.all(document.getAnimations().filter(a => a.effect.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {})));
        });
        const measured = await page.evaluate(`(${measure})(${colorMath})`);
        const focus = await page.evaluate(() => {
          const el = document.activeElement, style = getComputedStyle(el);
          return {name:el.id, outline:style.outline, shadow:style.boxShadow};
        });
        rows.push({screen, theme, width, focus, ...measured});
        if ([390, 1440].includes(width)) {
          await page.evaluate(() => scrollTo(0, 0));
          await page.screenshot({path:new URL(`${screen}-${theme}-${width}.png`, out).pathname, fullPage:true});
        }
        if (screen === 'editor') {
          await page.keyboard.press('Escape');
          assert(await page.getByRole('button', {name:'작성 기본값·호출 설정 수정'}).evaluate(el => el === document.activeElement), 'keyboard Escape returns focus');
        }
      }
    }
  }
  const compact = rows.map(row => {
    const pairs = new Map();
    for (const item of row.text) {
      const key = JSON.stringify([item.color, item.background, item.minimum]);
      if (!pairs.has(key)) pairs.set(key, {...item, count:0});
      pairs.get(key).count++;
    }
    return {...row, text:[...pairs.values()]};
  });
  await writeFile(new URL('measurements.json', out), JSON.stringify(compact, null, 2) + '\n');
  assert.equal(rows.filter(row => row.overflow).length, 0, 'no page horizontal overflow');
  assert.equal(rows.flatMap(row => row.targets).filter(t => t.width < 43.99 || t.height < 43.99).length, 0, '44px actions');
  assert.equal(rows.flatMap(row => row.borders).filter(b => b.inside < 3 || b.outside < 3).length, 0, '3:1 required boundaries');
  const failures = rows.flatMap(row => row.text.filter(t => t.ratio + .01 < t.minimum).map(t => ({screen:row.screen, theme:row.theme, width:row.width, ...t})));
  assert.equal(failures.length, 0, `text contrast: ${JSON.stringify(failures.slice(0, 4))}`);
  assert.equal(writes, 14, 'only explicit synthetic saves send writes; no paid probes');
  return {pageErrors:[]};
}
