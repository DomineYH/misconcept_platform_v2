import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';

// Measure rendered colors after ancestor backgrounds are composed, not token literals.
function measure() {
  const rgba = value => value.match(/[\d.]+/g)?.map(Number) || [0, 0, 0, 0];
  const over = (fg, bg) => fg.slice(0, 3).map((v, i) => v * (fg[3] ?? 1) + bg[i] * (1 - (fg[3] ?? 1)));
  const background = el => {
    if (!el) return [255, 255, 255];
    return over(rgba(getComputedStyle(el).backgroundColor), background(el.parentElement));
  };
  const luminance = rgb => rgb.map(v => {v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;})
    .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
  const contrast = (a, b) => (Math.max(luminance(a), luminance(b)) + .05) / (Math.min(luminance(a), luminance(b)) + .05);
  const visible = el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
  const text = [], targets = [], borders = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el) || ['SCRIPT', 'STYLE', 'OPTION'].includes(el.tagName)) continue;
    const s = getComputedStyle(el), box = el.getBoundingClientRect();
    const ownText = el.matches('#bulk-credentials-body td:nth-child(3)') ? '[REDACTED]' : [...el.childNodes].filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent.trim()).join(' ').trim();
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
  const base = new URL(page.url()).origin, out = new URL('./uiux/evidence/126/', import.meta.url);
  await mkdir(out, {recursive:true});
  await page.emulateMedia({reducedMotion:'reduce'});
  const rows = [], failures = [];
  const screens = [
    ['dashboard','/fixtures/s5/menu'], ['users','/fixtures/s6/users'],
    ['groups','/fixtures/uiux/admin/groups'], ['sessions','/fixtures/uiux/admin/sessions'],
    ['user-conversations','/fixtures/uiux/admin/user_conversations'], ['analysis','/fixtures/uiux/admin/analysis'],
    ['usage','/admin/api-usage'], ['about-admin','/fixtures/uiux/admin/about'],
    ['about-public','/login'], ['about-error','/login'],
    ['detail','/fixtures/uiux/admin/analysis'], ['detail-error','/fixtures/uiux/admin/analysis'], ['group-create','/fixtures/uiux/admin/groups'],
    ['about-edit','/fixtures/uiux/admin/about'], ['user-edit','/fixtures/s6/users'],
    ['group-error','/fixtures/uiux/admin/groups'], ['sessions-error','/fixtures/uiux/admin/sessions'],
    ['bulk-preview','/admin/users'], ['bulk-result','/admin/users'],
    ['groups-empty','/fixtures/uiux/admin/groups?empty=1'], ['analysis-empty','/fixtures/uiux/admin/analysis?empty=1'],
  ];
  await page.route('**/admin/groups', route => route.request().method() === 'POST'
    ? route.fulfill({status:422,json:{detail:'합성 지역 입력 오류'}}) : route.continue());
  await page.route('**/admin/sessions/1/analysis_modal', route => route.fulfill({status:503,body:'unavailable'}));
  await page.route('**/admin/users/bulk/preview', route => route.fulfill({json:{
    rows:[{row_num:1,username:'synthetic',nickname:'긴 합성 닉네임 '.repeat(12),role:'teacher',group_id:null,errors:[]}],
    groups:[],summary:{total:1,valid:1,error:0}
  }}));
  await page.route('**/admin/users/bulk/register', route => route.fulfill({json:{
    success_count:1,fail_count:1,credentials:[{username:'synthetic',nickname:'합성 사용자',initial_password:'SYNTHETIC_INITIAL_ONLY'}],
    failures:[{username:'duplicate',nickname:'기존 합성 사용자',reason:'이미 존재하는 사용자 ID'}]
  }}));
  for (const theme of ['light','dark']) {
    await page.emulateMedia({colorScheme:theme});
    for (const width of [320,390,768,1024,1440]) {
      await page.setViewportSize({width,height:900});
      for (const [screen,path] of screens) {
        await page.goto(base + path);
        if (screen === 'bulk-preview' || screen === 'bulk-result') {
          await page.locator('#open-bulk-modal').click();
          await page.locator('#bulk-file-input').setInputFiles({name:'synthetic.csv',mimeType:'text/csv',buffer:Buffer.from('username,nickname\nsynthetic,합성')});
          await page.locator('#bulk-preview-table tr').nth(1).waitFor();
          if (screen === 'bulk-result') {
            await page.locator('#bulk-register-btn').click();
            await page.locator('#bulk-credentials-body tr').waitFor();
          }
        }
        if (screen === 'detail' || screen === 'detail-error') {
          await page.route('**/admin/analysis/1/detail', async route => {
            if (screen === 'detail-error') return route.fulfill({status:503,body:'unavailable'});
            const response = await page.request.get(base+'/fixtures/uiux/admin/detail');
            await route.fulfill({contentType:'text/html',body:await response.text()});
          });
          await page.locator('[hx-target="#detail-modal"]').click();
          await page.locator(screen === 'detail' ? '#detail-modal h3' : '#detail-modal .modal-error').waitFor();
        }
        if (screen === 'about-public' || screen === 'about-error') {
          if (screen === 'about-error') await page.route('**/about/content', route => route.fulfill({status:503,body:'unavailable'}));
          if (width <= 768) await page.locator('.hamburger').click();
          await page.locator('#open-about-modal').click();
          if (screen === 'about-public') await page.locator('#about-modal-container .contributor-card').waitFor();
          else await page.locator('#about-load-error').waitFor();
        }
        if (screen === 'group-create' || screen === 'group-error') await page.locator('#open-create-modal').click();
        if (screen === 'group-error') {
          await page.locator('#new-name').fill('합성 오류');
          await page.locator('#create-form button[type=submit]').click();
          await page.locator('.modal-error').waitFor();
        }
        if (screen === 'about-edit' || screen === 'user-edit') await page.locator('.edit-btn').first().click();
        if (screen === 'sessions-error') {
          await page.locator('.view-analysis-btn').click();
          await page.locator('.modal-error').waitFor();
        }
        await page.evaluate(async () => {
          await Promise.all(document.getAnimations().filter(a => a.effect.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {})));
        });
        const result = await page.evaluate(measure);
        const modal = await page.evaluate(() => {
          const root = document.querySelector('.modal-overlay.active, .modal-overlay.flex, .slide-panel.active');
          if (!root) return null;
          const box = (root.querySelector('.modal-container') || root).getBoundingClientRect();
          const foot = root.querySelector('.modal-footer, .panel-footer')?.getBoundingClientRect();
          return {x:box.x,y:box.y,width:box.width,height:box.height,viewportHeight:innerHeight,
            footerVisible:!foot || (foot.top >= 0 && foot.bottom <= innerHeight), focusInside:root.contains(document.activeElement)};
        });
        const pairs = new Map();
        for (const item of result.text) {
          if (item.ratio + .01 < item.minimum) failures.push({screen,theme,width,...item});
          const key = JSON.stringify([item.color,item.background,item.minimum]);
          if (!pairs.has(key)) pairs.set(key,item);
        }
        rows.push({screen,theme,width,...result,text:[...pairs.values()],modal});
        if ([390,1440].includes(width)) await page.screenshot({path:new URL(`${screen}-${theme}-${width}.png`,out).pathname,mask:[page.locator('#bulk-credentials-body td:nth-child(3)')]});
        if (screen === 'about-error') await page.unroute('**/about/content');
        if (screen === 'detail' || screen === 'detail-error') await page.unroute('**/admin/analysis/1/detail');
      }
    }
  }
  await writeFile(new URL('measurements.json',out), JSON.stringify(rows,null,2)+'\n');
  await writeFile(new URL('contrast-failures.json',out), JSON.stringify(failures,null,2)+'\n');
  assert.equal(rows.filter(row => row.overflow).length,0,'no document overflow');
  assert.equal(rows.flatMap(row => row.targets).filter(t => t.width < 43.99 || t.height < 43.99).length,0,'44px actions');
  assert.equal(rows.flatMap(row => row.borders).filter(b => b.inside < 3 || b.outside < 3).length,0,'required input/button boundaries');
  assert.equal(failures.length,0,`composed text contrast: ${JSON.stringify(failures.slice(0,4))}`);
  assert(rows.every(row => !row.modal || (row.modal.x >= 15.99 && row.modal.y >= 0 && row.modal.height <= row.modal.viewportHeight * .9 + .1 && row.modal.footerVisible && row.modal.focusInside)), 'dialog bounds, actions and focus');
  return {pageErrors:[]};
}
