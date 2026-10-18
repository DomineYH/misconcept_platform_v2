import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {state} from './browser_ai_connections.mjs';
import {colorMath} from './uiux_color_math.mjs';

const screens = [
  ['teacher-scenarios','/scenarios'],
  ['dashboard','/fixtures/s5/menu'],
  ['scenarios','/fixtures/s6/scenarios?total=41&uiux123=1'],
  ['users','/fixtures/s6/users?total=41'],
  ['groups','/fixtures/uiux/admin/groups'],
  ['about','/fixtures/uiux/admin/about'],
  ['sessions','/fixtures/uiux/admin/sessions'],
  ['user-conversations','/fixtures/uiux/admin/user_conversations'],
  ['analysis','/fixtures/uiux/admin/analysis'],
  ['api-usage','/admin/api-usage'],
  ['ai','/admin/ai'],
  ['editor','/fixtures/s2/editor'],
  ['comparison','/fixtures/uiux/comparison'],
];
const widths = [320,390,767,768,769,1024,1180,1440];

// Inspect rendered headings and descriptions, accepting both existing header aliases.
function measure(colorMath) {
  const {rgba, over, background, contrast} = colorMath();
  const box = node => {
    const r = node.getBoundingClientRect();
    return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};
  };
  const visible = node => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden';
  const title = document.querySelector('main h1');
  const header = title.closest('.page-header,.page-header-admin');
  // The fallback records the old editor/comparison/analysis appearance for RED evidence.
  const root = header || title.closest('header') || title.parentElement;
  const descriptions = [...root.querySelectorAll('p')].filter(node => visible(node) &&
    !node.querySelector('a,button') && !node.matches('[role], [id]'));
  const intro = header ? descriptions : descriptions.slice(0,2);
  const actions = header ? [...header.querySelectorAll('a,button')].filter(visible) : [];
  const textStyle = node => {
    const style = getComputedStyle(node), bg = background(node);
    return {fontSize:parseFloat(style.fontSize),fontWeight:style.fontWeight,
      lineHeight:parseFloat(style.lineHeight),color:style.color,
      contrast:contrast(over(rgba(style.color),bg),bg),...box(node)};
  };
  const probe = document.createElement('span');
  probe.style.color = 'var(--color-text-secondary)';
  root.append(probe);
  const secondary = getComputedStyle(probe).color;
  probe.remove();
  const style = getComputedStyle(root), bounds = box(root);
  const text = [title,...intro,...actions];
  const clipped = text.some(node => {
    const range = document.createRange();
    range.selectNodeContents(node);
    const r = box(node);
    return r.x < bounds.x - 1 || r.right > bounds.right + 1 ||
      r.y < bounds.y - 1 || r.bottom > bounds.bottom + 1 ||
      [...range.getClientRects()].some(rect => rect.x < r.x - 1 || rect.right > r.right + 1 ||
        rect.y < r.y - 1 || rect.bottom > r.bottom + 1);
  });
  const overlaps = text.some((node,index) => text.slice(index+1).some(other => {
    const a = box(node), b = box(other);
    return Math.min(a.right,b.right)-Math.max(a.x,b.x) > 1 &&
      Math.min(a.bottom,b.bottom)-Math.max(a.y,b.y) > 1;
  }));
  let next = root.nextElementSibling;
  while (next && (!visible(next) || ['SCRIPT','STYLE','LINK'].includes(next.tagName))) next = next.nextElementSibling;
  const content = root.querySelector('.header-content');
  return {common:!!header,h1Count:document.querySelectorAll('main h1').length,
    title:textStyle(title),titleText:title.textContent.trim(),
    descriptions:intro.map(textStyle),secondary,bounds,
    descriptionGap:intro[0] ? box(intro[0]).y-box(title).bottom : null,
    paddingBottom:parseFloat(style.paddingBottom),marginBottom:parseFloat(style.marginBottom),
    borderBottom:parseFloat(style.borderBottomWidth),
    bodyGap:header && next ? box(next).y-bounds.bottom : null,
    contentWidth:content ? box(content).width : box(title).width,
    actions:actions.map(textStyle),clipped,overlaps,
    documentWidth:document.documentElement.scrollWidth,viewportWidth:innerWidth};
}

export default async function check(page) {
  const base = new URL(page.url()).origin;
  const phase = process.env.BROWSER_171_PHASE || 'after';
  const out = process.env.BROWSER_171_ARTIFACT_DIR || join(tmpdir(),'mp2-browser-171');
  await mkdir(out,{recursive:true});
  await page.route('**/admin/ai/state',route => route.fulfill({json:state()}));
  const rows = [], failures = [], pageErrors = [];
  page.on('pageerror',error => pageErrors.push(error.message));
  const expect = (condition,message) => { if (!condition) failures.push(message); };
  const near = (value,expected) => Math.abs(value-expected) <= .1;
  async function capture(meta,variant) {
    const row = {...meta,variant,...await page.evaluate(`(${measure})((${colorMath}))`)};
    rows.push(row);
    const label = `${meta.language}/${meta.theme}/${meta.width}/${meta.screen}/${variant}`;
    const test = (condition,message) => expect(condition,`${label}: ${message}`);
    test(row.common,'shared page header');
    test(row.h1Count === 1,'one page h1');
    test(near(row.title.fontSize,32),'title 32px');
    test(row.title.fontWeight === '600','title weight 600');
    test(near(row.title.lineHeight,41.6),'title line-height 1.3');
    test(row.title.contrast >= 3,'large heading contrast >=3');
    test(row.descriptions.length === (meta.screen === 'dashboard' ? 0 : meta.screen === 'comparison' ? 2 : 1),
      'existing descriptions preserved');
    for (const description of row.descriptions) {
      test(near(description.fontSize,16),'description 16px');
      test(near(description.lineHeight,24),'description line-height 1.5');
      test(description.color === row.secondary,'secondary description color');
      test(description.contrast >= 4.5,'description contrast >=4.5');
    }
    if (row.descriptionGap !== null && meta.screen !== 'comparison') {
      test(near(row.descriptionGap,8),`title-to-description gap 8px (${row.descriptionGap})`);
    }
    test(near(row.paddingBottom,16),'header bottom padding 16px');
    test(near(row.marginBottom,24),'header-to-body margin 24px');
    test(near(row.borderBottom,1),'one-pixel header boundary');
    if (row.bodyGap !== null) test(near(row.bodyGap,24),'header-to-body gap 24px');
    test(row.documentWidth <= meta.width,'no page overflow');
    test(!row.clipped,'no clipped header text');
    test(!row.overlaps,'no overlapping title, description or actions');
    for (const action of row.actions) {
      test(action.width >= 44 && action.height >= 44,'header action >=44px');
      test(action.contrast >= 4.5,'header action text contrast >=4.5');
    }
    if (meta.screen === 'users' && meta.width <= 390) {
      test(row.contentWidth >= meta.width-32,`user header content keeps ${meta.width-32}px width`);
    }
    if ([320,390,1180].includes(meta.width) && variant !== 'stressed-no-actions') {
      await page.evaluate(() => scrollTo(0,0));
      await page.screenshot({path:join(out,`${phase}-${meta.screen}-${meta.language}-${meta.theme}-${meta.width}-${variant}.png`)});
    }
    if ([320,390,1180].includes(meta.width) && variant === 'native') {
      row.actionsReachable = [];
      for (const action of await page.locator('main .page-header a,main .page-header button,main .page-header-admin a,main .page-header-admin button').all()) {
        if (!await action.isVisible()) continue;
        await action.focus();
        await page.keyboard.press('Shift+Tab');
        await page.keyboard.press('Tab');
        const reachable = await action.evaluate(node => {
          const r = node.getBoundingClientRect();
          const hit = document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
          return node === document.activeElement && node.tabIndex >= 0 &&
            r.x >= 0 && r.right <= innerWidth+1 && (hit === node || node.contains(hit));
        });
        row.actionsReachable.push(reachable);
        test(reachable,'header action reachable with Tab');
      }
    }
  }
  for (const language of ['ko','en']) {
    await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
    for (const theme of ['light','dark']) {
      await page.emulateMedia({colorScheme:theme});
      for (const width of widths) {
        await page.setViewportSize({width,height:900});
        console.log(`171 ${phase}: ${language}/${theme}/${width}`);
        for (const [screen,path] of screens) {
          const meta = {language,theme,width,screen};
          try {
            await page.goto(base+path);
            expect(await page.locator('html').getAttribute('lang') === language,`${screen}: display language`);
            await capture(meta,'native');
            await page.locator('main h1').evaluate((title,language) => {
              title.append(document.createTextNode(language === 'ko'
                ? ' — 학교별 교사 계정과 역할 및 그룹 배정 현황 관리'
                : ' — Administration of teacher accounts, roles and school group assignments'));
              const root = title.closest('.page-header,.page-header-admin,header') || title.parentElement;
              const description = [...root.querySelectorAll('p')].find(node =>
                !node.querySelector('a,button') && !node.matches('[role], [id]'));
              description?.append(document.createTextNode(language === 'ko'
                ? ' 여러 학교의 교사와 관리자가 긴 제목과 설명을 확인하며 기존의 등록, 수정, 다운로드 작업을 수행합니다.'
                : ' Teachers and administrators from multiple schools review the full title and description while using the existing create, edit and download actions.'));
            },language);
            await capture(meta,'stressed');
            const actions = page.locator('main .page-header a,main .page-header button,main .page-header-admin a,main .page-header-admin button');
            if (await actions.count()) {
              await actions.evaluateAll(nodes => nodes.forEach(node => { node.style.display = 'none'; }));
              await capture(meta,'stressed-no-actions');
            }
          } catch (error) {
            failures.push(`${language}/${theme}/${width}/${screen}: ${error.message}`);
          }
        }
        // Persist incrementally so interrupted evidence runs still have useful measurements.
        await writeFile(join(out,`${phase}-measurements.json`),JSON.stringify({phase,rows,pageErrors,failures},null,2)+'\n');
      }
    }
  }
  const native = rows.filter(row => row.variant === 'native').length;
  assert.equal(native,screens.length*2*2*widths.length,'all consumer, language, theme and width combinations rendered');
  assert.equal(pageErrors.length,0,`uncaught page errors: ${pageErrors.join('; ')}`);
  assert.equal(failures.length,0,`${failures.length} failures: ${failures.slice(0,12).join('; ')}; measurements: ${out}/${phase}-measurements.json`);
  return {pageErrors:[]};
}
