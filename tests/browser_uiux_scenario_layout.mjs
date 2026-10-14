import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';

// Measure rendered colors after ancestor backgrounds are composed, not token literals.
export function measure() {
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

export async function settle(page) {
  await page.evaluate(async () => {
    await Promise.all(document.getAnimations().filter(animation => animation.effect.getTiming().iterations !== Infinity).map(animation => animation.finished.catch(() => {})));
  });
}

export default async function checkScenarioLayout(page) {
  const base = new URL(page.url()).origin;
  await page.emulateMedia({reducedMotion:'reduce'});
  const evidence = new URL('./uiux/evidence/123/', import.meta.url);
  await mkdir(evidence, {recursive:true});
  const measurements = [];
  for (const theme of ['light', 'dark']) {
    await page.emulateMedia({colorScheme:theme});
    for (const width of [320,390,767,768,769,800,801,1024,1279,1280,1440]) {
      await page.setViewportSize({width,height:900});
      await page.goto(`${base}/fixtures/s2/editor?uiux123=1`);
      await page.getByLabel('제목', {exact:true}).fill('긴한글제목'.repeat(35));
      await page.getByRole('button', {name:'2. 문제 상황',exact:true}).focus();
      await page.keyboard.press('Enter');
      await page.getByLabel('공개 문제 상황').fill('공개 문제의 긴 문단입니다. '.repeat(100) + '\n마지막 공개 문단');
      const layout = await page.evaluate(() => {
        const preview = document.querySelector('.scenario-preview');
        const form = document.querySelector('#scenario-form');
        const rect = preview.getBoundingClientRect();
        const controls = [...document.querySelectorAll('.scenario-steps button, .scenario-actions button')].map(el => ({name:el.textContent.trim(),width:el.getBoundingClientRect().width,height:el.getBoundingClientRect().height}));
        return {overflow:document.documentElement.scrollWidth > innerWidth,previewWidth:rect.width,gap:rect.left-form.getBoundingClientRect().right,position:getComputedStyle(preview).position,top:getComputedStyle(preview).top,controls};
      });
      assert(!layout.overflow, `${theme}/${width}: editor page overflow`);
      assert(layout.controls.every(control => control.width >= 44 && control.height >= 44), 'editor actions have 44px targets');
      assert.equal(layout.position, width <= 800 ? 'static' : 'sticky');
      if (width > 800) { assert.equal(layout.previewWidth,320); assert.equal(layout.gap,24); assert.equal(layout.top,'76px'); }
      await page.locator('#validation-summary').scrollIntoViewIfNeeded();
      assert(await page.locator('#validation-summary').evaluate(el => { const rect = el.getBoundingClientRect(); return rect.top >= 0 && rect.bottom <= innerHeight; }), 'long preview ends in document scroll');
      await settle(page);
      const editorColors = await page.evaluate(measure);
      assert(editorColors.text.every(item => item.ratio >= item.minimum),'editor text contrast');
      assert(editorColors.borders.every(item => item.inside >= 3 && item.outside >= 3),'editor input boundary contrast');
      measurements.push({screen:'editor',theme,width,...layout,...editorColors});
      await page.evaluate(() => scrollTo(0,0));
      if ([390,1440].includes(width)) await page.screenshot({path:new URL(`editor-${theme}-${width}.png`,evidence).pathname,fullPage:true});
      for (const step of ['1. 기본 정보','3. 학생봇','4. 멘토','5. 사후 분석']) {
        await page.getByRole('button',{name:step,exact:true}).click();
        await settle(page);
        const measured = await page.evaluate(measure);
        assert(!measured.overflow,`${step} fits`);
        assert(measured.targets.every(item => item.width >= 44 && item.height >= 44),`${step} targets`);
        assert(measured.text.every(item => item.ratio >= item.minimum),`${step} text contrast: ${JSON.stringify(measured.text.filter(item => item.ratio < item.minimum))}`);
        assert(measured.borders.every(item => item.inside >= 3 && item.outside >= 3),`${step} boundaries`);
        measurements.push({screen:step,theme,width,...measured});
      }
      await page.goto(`${base}/fixtures/s6/scenarios?uiux123=1`);
      assert(await page.getByText('검토 필요',{exact:true}).isVisible());
      assert(await page.getByText('변환 필요',{exact:true}).isVisible());
      assert(await page.locator('.scenario-card').getByText('비활성',{exact:true}).first().isVisible());
      await page.getByRole('searchbox',{name:'시나리오 검색',exact:true}).fill('현재 페이지에 없는 제목');
      await page.getByText('현재 페이지에서 검색·필터에 맞는 시나리오가 없습니다.',{exact:true}).waitFor({timeout:2000});
      await page.getByRole('searchbox',{name:'시나리오 검색',exact:true}).fill('');
      const list = await page.evaluate(() => ({searchBoxHeight:document.querySelector('.search-box').getBoundingClientRect().height,searchHeight:document.querySelector('#search-input').getBoundingClientRect().height,headerWidth:document.querySelector('.page-header-admin').getBoundingClientRect().width,titleWidth:document.querySelector('.header-content').getBoundingClientRect().width,overflow:document.documentElement.scrollWidth > innerWidth,controls:[...document.querySelectorAll('.scenarios-admin .btn, .scenarios-admin select, .scenarios-admin input')].map(el => ({name:el.textContent.trim(),width:el.getBoundingClientRect().width,height:el.getBoundingClientRect().height}))}));
      assert.equal(list.searchBoxHeight,list.searchHeight,'search group adds no empty vertical space');
      assert.equal(list.titleWidth,list.headerWidth,'list title has its own row before actions');
      assert(!list.overflow,`${theme}/${width}: list page overflow`);
      assert(list.controls.every(control => control.width >= 44 && control.height >= 44),'list actions have 44px targets');
      await settle(page);
      const listColors = await page.evaluate(measure);
      assert(listColors.text.every(item => item.ratio >= item.minimum),'list text contrast');
      assert(listColors.borders.every(item => item.inside >= 3 && item.outside >= 3),'list input boundary contrast');
      measurements.push({screen:'list',theme,width,...list,...listColors});
      await page.evaluate(() => scrollTo(0,0));
      if ([390,1440].includes(width)) await page.screenshot({path:new URL(`list-${theme}-${width}.png`,evidence).pathname,fullPage:false});
    }
  }
  await writeFile(new URL('measurements.json',evidence),JSON.stringify(measurements,null,2)+'\n');
  return {pageErrors:[]};
}
