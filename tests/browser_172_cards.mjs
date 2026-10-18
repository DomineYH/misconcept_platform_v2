import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {state} from './browser_ai_connections.mjs';
import {colorMath} from './uiux_color_math.mjs';
import {settle} from './browser_uiux_scenario_layout.mjs';

// Baseline mode only records evidence; the default run always checks the contract.
const baseline = process.env.CARDS_172_BASELINE_ONLY === '1';
const evidence = process.env.CARDS_172_EVIDENCE_DIR;

function measure({selector, list, colorMath}) {
  const {rgba, background, contrast} = colorMath();
  const visible = el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
  const root = getComputedStyle(document.documentElement);
  const token = name => root.getPropertyValue(name).trim();
  const probe = document.createElement('div');
  document.body.append(probe);
  probe.style.color = 'var(--color-border-light)';
  const light = getComputedStyle(probe).color;
  probe.style.color = 'var(--color-bg-surface)';
  const surface = getComputedStyle(probe).color;
  probe.remove();
  const cards = [...document.querySelectorAll(selector)].filter(visible).map(el => {
    const s = getComputedStyle(el), rect = el.getBoundingClientRect();
    return {name:el.id || el.className || el.tagName, border:s.borderTopColor, borderWidth:s.borderTopWidth,
      background:s.backgroundColor, radius:s.borderTopLeftRadius,
      padding:[s.paddingTop,s.paddingRight,s.paddingBottom,s.paddingLeft],
      x:rect.x,y:rect.y,width:rect.width,height:rect.height,
      overflow:el.scrollWidth > el.clientWidth + 1};
  });
  const inputs = [...document.querySelectorAll('main input:not([type="checkbox"]), main select, main textarea')]
    .filter(el => visible(el) && !el.disabled).map(el => {
      const s = getComputedStyle(el), color = rgba(s.borderTopColor);
      return {name:el.id || el.name, inside:contrast(color,background(el)), outside:contrast(color,background(el.parentElement))};
    });
  const controls = [...document.querySelectorAll('.scenario-steps button, .scenario-actions button, .comparison-tabs button')]
    .filter(visible).map(el => {
      const box = el.getBoundingClientRect();
      return {name:el.id || el.textContent.trim(),width:box.width,height:box.height};
    });
  const clippedText = [...document.querySelectorAll(`${selector.split(',').map(s => `${s} p, ${s} h2, ${s} h3, ${s} pre`).join(',')}`)]
    .filter(visible).filter(el => el.scrollWidth > el.clientWidth + 1).map(el => el.textContent.slice(0,80));
  const preview = document.querySelector('.scenario-preview');
  const previewChildren = preview ? [...preview.querySelectorAll('section > *')].filter(visible) : [];
  const previewGaps = previewChildren.slice(1).map((el,index) => ({
    before:previewChildren[index].tagName,after:el.tagName,
    gap:el.getBoundingClientRect().top-previewChildren[index].getBoundingClientRect().bottom
  }));
  const previewSections = preview ? [...preview.children].filter(visible) : [];
  const sectionGap = previewSections.length > 1 ? previewSections[1].getBoundingClientRect().top-previewSections[0].getBoundingClientRect().bottom : null;
  return {cards,inputs,controls,clippedText,previewGaps,sectionGap,
    overflow:document.documentElement.scrollWidth > innerWidth,
    previewPosition:preview ? getComputedStyle(preview).position : null,
    expected:{border:light,background:surface,radius:token('--radius-xl'),padding:list ? 24 : 16}};
}

function check(row) {
  const label = `${row.language}/${row.theme}/${row.width}/${row.screen}`;
  assert(row.cards.length > 0,`${label}: rendered content cards`);
  for (const card of row.cards) {
    assert.equal(card.border,row.expected.border,`${label}/${card.name}: general content card uses the light boundary`);
    assert.equal(card.borderWidth,'1px',`${label}/${card.name}: one-pixel card boundary`);
    assert.equal(card.background,row.expected.background,`${label}/${card.name}: surface background`);
    assert.equal(card.radius,row.expected.radius,`${label}/${card.name}: card radius`);
    assert(card.padding.every(value => parseFloat(value) === row.expected.padding),`${label}/${card.name}: content padding`);
    assert(!card.overflow,`${label}/${card.name}: content stays within its card`);
  }
  assert(!row.overflow,`${label}: no document overflow`);
  assert.equal(row.clippedText.length,0,`${label}: text fits without horizontal clipping`);
  assert(row.previewGaps.every(item => item.gap >= 8),`${label}: preview headings and body keep spacing-sm separation without overlap`);
  if (row.sectionGap !== null) assert(row.sectionGap >= 24,`${label}: preview sections retain spacing-lg separation`);
  assert(row.inputs.every(item => item.inside >= 3 && item.outside >= 3),`${label}: input boundaries retain 3:1 contrast: ${JSON.stringify(row.inputs)}`);
  assert(row.controls.every(item => item.width >= 44 && item.height >= 44),`${label}: controls retain 44px targets`);
  if (row.previewPosition) assert.equal(row.previewPosition,row.width <= 800 ? 'static' : 'sticky',`${label}: responsive preview positioning`);
}

export default async function checkCardRoles(page) {
  const base = new URL(page.url()).origin;
  const rows = [];
  if (evidence) await mkdir(evidence,{recursive:true});
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.route('**/admin/ai/state',route => route.fulfill({json:state()}));
  await page.route('**/admin/comparison-conversations**',route => route.fulfill({json:{items:[],next_cursor:null}}));
  await page.route('**/fixtures/uiux/comparison',async route => {
    const response = await route.fetch();
    const html = (await response.text()).replace(/(<script id="comparison-conversation-data" type="application\/json">)(.*?)(<\/script>)/s,(_,open,json,close) => {
      const data = JSON.parse(json);
      data.snapshot.pairs = [{turn_id:'card-turn',teacher:'공통 질문 common question '.repeat(12),student:'공개 학생 응답 public student response '.repeat(16)}];
      return open+JSON.stringify(data)+close;
    });
    await route.fulfill({response,body:html});
  });
  const capture = async (screen,language,theme,width,selector,list=false) => {
    await page.mouse.move(0,0);
    await settle(page);
    const row = {screen,language,theme,width,...await page.evaluate(
      `(${measure})({selector:${JSON.stringify(selector)},list:${list},colorMath:(${colorMath})})`)};
    rows.push(row);
    if (evidence) await writeFile(`${evidence}/${baseline ? 'before' : 'after'}-measurements.json`,JSON.stringify(rows,null,2)+'\n');
    if (evidence && language === 'ko' && [320,390,1180].includes(width)) {
      await page.evaluate(() => scrollTo(0,0));
      await page.screenshot({path:`${evidence}/${baseline ? 'before' : 'after'}-${screen}-${theme}-${width}.png`,fullPage:true});
    }
    if (!baseline) check(row);
  };
  for (const language of ['ko','en']) {
    await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
    for (const theme of ['light','dark']) {
      await page.emulateMedia({colorScheme:theme});
      for (const width of [320,390,768,1024,1180,1440]) {
        await page.setViewportSize({width,height:900});
        await page.goto(`${base}/fixtures/s2/editor`);
        await page.waitForFunction(() => document.querySelector('#editor-status').textContent);
        await page.locator('[data-step="1"]').click();
        await page.locator('[data-field="problem.public_text"]').fill('공개 문제 public problem '.repeat(70)+'\nPUBLIC_PROBLEM_END');
        await page.locator('[data-step="2"]').click();
        await page.locator('[data-field="student.public_profile"]').fill('공개 학생 소개 public student profile '.repeat(40)+'\nPUBLIC_PROFILE_END');
        await page.locator('[data-field="student.internal_profile"]').fill('INTERNAL_PROFILE_172');
        await page.locator('[data-field="student.behavior_instruction"]').fill('INTERNAL_INSTRUCTION_172');
        const publicText = await page.locator('#public-preview').innerText();
        assert(publicText.includes('PUBLIC_PROBLEM_END') && publicText.includes('PUBLIC_PROFILE_END'),'long public text reaches the preview');
        assert(!publicText.includes('INTERNAL_'),'internal student and mentor instructions stay outside the public preview');
        await capture('editor',language,theme,width,'#scenario-form > section, .scenario-preview, #scenario-form fieldset');
        const input = page.locator('[data-field="student.name"]');
        await page.keyboard.press('Tab');
        await input.focus();
        const focus = await input.evaluate(new Function('el',`const {rgba,background,contrast}=(${colorMath})(); const s=getComputedStyle(el); return {width:parseFloat(s.outlineWidth),style:s.outlineStyle,ratio:contrast(rgba(s.outlineColor),background(el.parentElement))};`));
        rows.at(-1).focus = focus;
        if (!baseline) assert(focus.width >= 2 && focus.style === 'solid' && focus.ratio >= 3,`${language}/${theme}/${width}: input focus boundary retains 3:1 contrast`);
        await page.locator('#open-comparison').click();
        await page.locator('[data-comparison-panel]').first().waitFor();
        await capture('comparison-old',language,theme,width,'#scenario-comparison, .comparison-grid article');
        if (width < 768) {
          const tabs = page.locator('.comparison-tabs [role="tab"]');
          await tabs.first().focus();
          await page.keyboard.press('ArrowRight');
          if (!baseline) {
            assert.equal(await tabs.nth(1).getAttribute('aria-selected'),'true','mobile comparison tabs respond to keyboard');
            assert(await page.locator('[data-comparison-panel]').nth(1).isVisible(),'selected comparison panel remains visible');
            assert(!(await page.locator('[data-comparison-panel]').first().isVisible()),'other mobile panels remain hidden');
          }
        } else if (width < 1280) {
          const grid = page.locator('.comparison-grid');
          const overflow = await grid.evaluate(el => el.scrollWidth > el.clientWidth);
          if (!baseline) assert.equal(await grid.evaluate(el => getComputedStyle(el).overflowX),'auto','comparison overflow remains inside its grid');
          if (!baseline && width <= 1024) assert(overflow,'narrow comparison grids retain internal overflow');
          if (overflow) {
            await grid.focus();
            await page.keyboard.press('ArrowRight');
            if (!baseline) await page.waitForFunction(() => document.querySelector('.comparison-grid').scrollLeft > 0);
          }
        }
        await page.goto(`${base}/fixtures/uiux/comparison`);
        await page.locator('#conversation-rounds article').first().waitFor();
        await capture('comparison-new',language,theme,width,'.conversation-panels > article, #conversation-dialogue article');
        if (!baseline) assert(await page.locator('.conversation-panels > article').evaluateAll(els => els.every(el => getComputedStyle(el).overflowY === 'visible')),'comparison conversation results keep document scrolling');
        for (const [screen,path] of [['scenarios','/scenarios'],['groups','/fixtures/uiux/admin/groups']]) {
          await page.goto(base+path);
          await capture(screen,language,theme,width,'.scenario-card',true);
        }
        await page.goto(`${base}/admin/ai`);
        await page.locator('[data-provider="google"]').waitFor();
        await capture('ai',language,theme,width,'#ai-page article');
        await page.locator('#ai-content section').last().getByRole('button').click();
        await page.locator('#ai-editor').waitFor();
        await capture('ai-settings',language,theme,width,'#ai-editor, #ai-editor fieldset');
      }
      for (const width of [799,800,801]) {
        await page.setViewportSize({width,height:900});
        await page.goto(`${base}/fixtures/s2/editor`);
        await page.waitForFunction(() => document.querySelector('#editor-status').textContent);
        await capture('editor-breakpoint',language,theme,width,'#scenario-form > section, .scenario-preview');
      }
    }
  }
  if (evidence) await writeFile(`${evidence}/${baseline ? 'before' : 'after'}-measurements.json`,JSON.stringify(rows,null,2)+'\n');
  console.log(`${baseline ? 'BASELINE' : 'CHECKED'} ${rows.length} card measurements`);
  return {pageErrors:[]};
}
