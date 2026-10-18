import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {colorMath} from './uiux_color_math.mjs';

// Browser-only presentation and lifecycle checks; no database or provider calls.
function buttonMeasurement(el, colorMath) {
  const {rgba, over, background, contrast} = colorMath();
  const style = getComputedStyle(el), bg = background(el), surround = background(el.parentElement);
  const box = el.getBoundingClientRect();
  return {
    color:style.color, backgroundColor:style.backgroundColor, composedBackground:bg,
    textContrast:contrast(over(rgba(style.color), bg), bg),
    borderColor:style.borderTopColor,
    borderContrast:contrast(over(rgba(style.borderTopColor), surround), surround),
    outlineColor:style.outlineColor, outlineStyle:style.outlineStyle,
    outlineWidth:parseFloat(style.outlineWidth), outlineOffset:style.outlineOffset,
    focusContrast:contrast(over(rgba(style.outlineColor), surround), surround),
    boxShadow:style.boxShadow, opacity:style.opacity,
    focusVisible:el.matches(':focus-visible'), hovered:el.matches(':hover'),
    active:el.matches(':active'), disabled:el.disabled,
    targetWidth:box.width, targetHeight:box.height,
  };
}

function headerMeasurement(el) {
  const box = node => {
    const r = node.getBoundingClientRect();
    return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};
  };
  const bounds = box(el), content = el.querySelector('.header-content'), title = el.querySelector('h1');
  const children = [...el.children].map(box);
  const overlaps = children.some((a, i) => children.slice(i + 1).some(b =>
    Math.min(a.right,b.right) - Math.max(a.x,b.x) > 1 &&
    Math.min(a.bottom,b.bottom) - Math.max(a.y,b.y) > 1));
  const text = [title,el.querySelector('.subtitle'),...el.querySelectorAll('button,a')].filter(Boolean);
  const clipped = text.some(node => {
    const range = document.createRange();
    range.selectNodeContents(node);
    const rect = box(node);
    return [...range.getClientRects()].some(r => r.x < rect.x - 1 || r.right > rect.right + 1 ||
      r.y < rect.y - 1 || r.bottom > rect.bottom + 1) ||
      rect.x < bounds.x - 1 || rect.right > bounds.right + 1 || rect.bottom > bounds.bottom + 1;
  });
  const fontSize = parseFloat(getComputedStyle(title).fontSize);
  return {
    bounds,content:box(content),title:box(title),titleText:title.textContent.trim(),
    subtitle:el.querySelector('.subtitle')?.textContent.trim(),fontSize,
    // Four title characters distinguish readable wrapping from the reported 1–2 character column.
    squeezed:box(content).width < Math.min(bounds.width,4 * fontSize),
    overlaps,clipped,documentWidth:document.documentElement.scrollWidth,viewportWidth:innerWidth,
    actions:[...el.querySelectorAll('button,a')].map(node => ({text:node.textContent.trim(),...box(node)})),
  };
}

export default async function check(page) {
  const base = new URL(page.url()).origin;
  const out = process.env.BROWSER_174_ARTIFACT_DIR || join(tmpdir(), 'mp2-browser-174');
  const phase = process.env.BROWSER_174_PHASE || 'after';
  await mkdir(out, {recursive:true});
  const buttons = [], headers = [], lifecycle = [], failures = [], pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  const expect = (condition, message) => { if (!condition) failures.push(message); };
  const settle = () => page.evaluate(async () => {
    await Promise.all(document.getAnimations().filter(a => a.effect.getTiming().iterations !== Infinity)
      .map(a => a.finished.catch(() => {})));
  });
  async function keyboardFocus(target) {
    // Use actual Tab traversal, including the production dialog's focus trap.
    await page.keyboard.press('Tab');
    for (let i = 0; i < 80; i++) {
      if (await target.evaluate(el => el === document.activeElement)) return true;
      await page.keyboard.press('Tab');
    }
    return false;
  }
  async function shot(meta, name) {
    if (![320,390,1180].includes(meta.width)) return;
    await page.screenshot({path:join(out, `${phase}-${meta.language}-${meta.theme}-${meta.width}-${name}.png`)});
  }
  async function states(meta, consumer, target) {
    for (const state of ['default','hover','focus','active','disabled']) {
      const label = `${meta.language}/${meta.theme}/${meta.width}/${consumer}/${state}`;
      await page.mouse.move(0,0);
      await target.evaluate(el => el.blur());
      try {
        if (state === 'hover' || state === 'active') await target.hover();
        if (state === 'focus') expect(await keyboardFocus(target), `${label}: reachable with Tab`);
        if (state === 'active') await page.mouse.down();
        if (state === 'disabled') await target.evaluate(el => { el.disabled = true; });
        await settle();
        const row = { ...meta, consumer, state,
          ...await target.evaluate(new Function('el', `return (${buttonMeasurement})(el, (${colorMath}));`)) };
        buttons.push(row);
        expect(row.targetWidth >= 44 && row.targetHeight >= 44,
          `${label}: 44x44 target (${row.targetWidth}x${row.targetHeight})`);
        if (state !== 'disabled') {
          expect(row.textContrast >= 4.5, `${label}: text contrast ${row.textContrast.toFixed(4)} < 4.5`);
          // The close glyph is necessary non-text; the border is decorative.
          if (consumer !== 'language') expect(row.textContrast >= 3, `${label}: close glyph contrast < 3`);
        }
        if (state === 'hover') expect(row.hovered, `${label}: real pointer hover`);
        if (state === 'active') expect(row.active, `${label}: real pointer active`);
        if (state === 'focus') {
          expect(row.focusVisible && row.outlineStyle !== 'none' && row.outlineWidth >= 2,
            `${label}: visible keyboard focus`);
          expect(row.focusContrast >= 3, `${label}: focus indicator contrast ${row.focusContrast.toFixed(4)} < 3`);
        }
        if (state === 'disabled') {
          const url = page.url();
          await target.hover();
          const box = await target.boundingBox();
          await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
          expect(page.url() === url && await target.isVisible(), `${label}: disabled click has no action`);
        }
        await shot(meta, `${consumer}-${state}`);
      } catch (error) {
        failures.push(`${label}: ${error.message}`);
        console.log(`174 capture failed: ${label}: ${error.message.split('\n')[0]}`);
      } finally {
        // Release outside the viewport so a held pointer cannot click the backdrop.
        await page.mouse.move(-10,-10);
        await page.mouse.up();
        await target.evaluate(el => { el.disabled = false; el.blur(); });
      }
    }
  }
  async function closeAndReturn(meta, consumer, opener, root, close) {
    await close.click();
    const clicked = !await root.isVisible() && await opener.evaluate(el => el === document.activeElement);
    expect(clicked, `${consumer}: close click hides dialog and returns focus`);
    await opener.click();
    await root.waitFor({state:'visible'});
    await page.keyboard.press('Escape');
    const escaped = !await root.isVisible() && await opener.evaluate(el => el === document.activeElement);
    expect(escaped, `${consumer}: Escape hides dialog and returns focus`);
    lifecycle.push({...meta, consumer, clicked, escaped});
  }
  async function checkHeaders(meta, stressed = false) {
    await page.setViewportSize({width:meta.width,height:900});
    for (const [screen,path] of [
      ['users','/fixtures/s6/users?total=41'],
      ['groups','/fixtures/uiux/admin/groups'],
      ['about','/fixtures/uiux/admin/about'],
      ['scenarios','/fixtures/s6/scenarios?total=41&uiux123=1'],
    ]) {
      await page.goto(base + path);
      const header = page.locator('main .page-header-admin');
      if (stressed) await header.evaluate((el, language) => {
        el.querySelector('h1').textContent += language === 'ko'
          ? ' — 학교별 교사 계정과 역할 및 그룹 배정 현황 관리'
          : ' — Administration of teacher accounts, roles and school group assignments';
        el.querySelector('.subtitle').textContent += language === 'ko'
          ? ' 여러 학교의 교사와 관리자가 긴 제목 및 설명을 확인하면서 기존의 등록, 수정, 다운로드 작업을 안전하게 수행합니다.'
          : ' Teachers and administrators from multiple schools review the full title and description while using the existing create, edit and download actions.';
      }, meta.language);
      const row = {...meta,screen,stressed,...await header.evaluate(headerMeasurement)};
      headers.push(row);
      const label = `${meta.language}/${meta.theme}/${meta.width}/${screen}/${stressed ? 'stressed' : 'real-labels'}`;
      expect(row.documentWidth <= meta.width, `${label}: document overflow (${row.documentWidth})`);
      expect(!row.overlaps, `${label}: header children overlap`);
      expect(!row.clipped, `${label}: header text clipped`);
      if (screen === 'users') expect(!row.squeezed,
        `${label}: title squeezed to ${row.content.width.toFixed(2)}px (< four title characters)`);
      await shot(meta, `${screen}-${stressed ? 'stressed-' : ''}header`);
      row.actionsReachable = [];
      for (const action of await header.locator('button,a').all()) {
        const tabbable = await keyboardFocus(action);
        const reachable = await action.evaluate(el => {
          const r = el.getBoundingClientRect();
          const hit = document.elementFromPoint(r.x + r.width / 2,r.y + r.height / 2);
          return el === document.activeElement && r.x >= 0 && r.right <= innerWidth + 1 &&
            r.y >= 0 && r.bottom <= innerHeight + 1 && (hit === el || el.contains(hit));
        });
        row.actionsReachable.push(tabbable && reachable);
        expect(tabbable && reachable, `${label}: header action reachable: ${(await action.innerText()).trim()}`);
      }
    }
  }

  for (const language of ['ko','en']) {
    await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
    for (const theme of ['light','dark']) {
      await page.emulateMedia({colorScheme:theme});
      for (const width of [320,390,768,1024,1180,1440]) {
        const meta = {language,theme,width};
        console.log(`174 ${phase}: ${language}/${theme}/${width}`);
        await page.setViewportSize({width,height:900});
        await checkHeaders(meta);
        await page.goto(`${base}/login`);
        expect(await page.locator('html').getAttribute('lang') === language, 'fixture display language');
        if (width <= 768) await page.locator('.hamburger').click();
        const toggle = page.locator('[data-ui-language]');
        await states(meta, 'language', toggle);
        await keyboardFocus(toggle);
        await Promise.all([page.waitForEvent('load'), page.keyboard.press('Enter')]);
        const next = language === 'ko' ? 'en' : 'ko';
        const switched = await page.locator('html').getAttribute('lang') === next;
        expect(switched, 'keyboard language switch reloads in the requested language');
        lifecycle.push({...meta, consumer:'language', switched});
        await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
        await page.goto(`${base}/login`);
        if (width <= 768) await page.locator('.hamburger').click();

        let release;
        const pending = new Promise(resolve => { release = resolve; });
        let delivered;
        const delivery = new Promise(resolve => { delivered = resolve; });
        const delayAbout = async route => {
          const response = await route.fetch();
          await pending;
          await route.fulfill({response});
          delivered();
        };
        await page.route('**/about/content', delayAbout);
        const opener = page.locator('#open-about-modal');
        const about = page.locator('#about-modal-overlay');
        const close = about.locator('.modal-close');
        await opener.click();
        await about.locator('[role=status]').waitFor();
        try {
          await states(meta, 'layout-loading-close', close);
        } finally {
          release();
          await delivery;
          await page.unroute('**/about/content', delayAbout);
        }
        await about.locator('.contributor-card').first().waitFor();
        await closeAndReturn(meta, 'layout-loading-close', opener, about, close);
        await opener.click();
        await about.locator('.contributor-card').first().waitFor();
        await states(meta, 'about-fragment-close', close);
        await closeAndReturn(meta, 'about-fragment-close', opener, about, close);

        await page.goto(`${base}/fixtures/uiux/admin/about`);
        for (const [consumer, selector, root] of [
          ['admin-create-close','#open-create-modal','#create-modal'],
          ['admin-edit-close','.edit-btn','#edit-modal'],
          ['admin-delete-close','.delete-btn','#delete-modal'],
        ]) {
          const caller = page.locator(selector).first(), dialog = page.locator(root);
          await caller.click();
          await dialog.waitFor({state:'visible'});
          await states(meta, consumer, dialog.locator('.modal-close'));
          await closeAndReturn(meta, consumer, caller, dialog, dialog.locator('.modal-close'));
        }
      }
      // Geometry alone brackets the existing mobile breakpoint and stresses real headers.
      for (const width of [767,769]) await checkHeaders({language,theme,width});
      for (const width of [320,390,767,768,769,1024,1180,1440]) {
        await checkHeaders({language,theme,width}, true);
      }
    }
  }
  await writeFile(join(out, `${phase}-measurements.json`), JSON.stringify({phase,buttons,headers,lifecycle,pageErrors,failures},null,2) + '\n');
  assert.equal(buttons.length, 720, '24 combinations x six consumers x five states recorded');
  assert.equal(headers.length, 256, 'four headers x ko/en x light/dark x eight widths x real/stressed labels');
  assert.equal(pageErrors.length, 0, `uncaught page errors: ${pageErrors.join('; ')}`);
  assert.equal(failures.length, 0, `${failures.length} failures; ${failures.slice(0,12).join('; ')}; all measurements: ${out}/${phase}-measurements.json`);
  return {pageErrors:[]};
}
