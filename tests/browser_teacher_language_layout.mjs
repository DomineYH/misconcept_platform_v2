import assert from 'node:assert/strict';
import {colorMath} from './uiux_color_math.mjs';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  const screens = ['/scenarios?many=1&long=1&missing_problem=1', '/scenarios?empty=1',
    '/chat?long=1&mentor_manual=1', '/fixtures/uiux122/history?reconstructed=1',
    '/fixtures/s4/analysis?state=preserved', '/fixtures/s4/analysis?state=partial',
    '/fixtures/s4/analysis?state=plan', '/fixtures/s4/analysis?state=running'];
  await page.route('**/sessions/1/analysis/runs/run-1', route => route.fulfill({status:500, body:'Isolated status failure'}));
  for (const language of ['en','ko']) {
    await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
    for (const theme of ['light','dark']) {
      await page.emulateMedia({colorScheme:theme, reducedMotion:'reduce'});
      for (const width of [320,390,768,1024,1440]) {
        await page.setViewportSize({width,height:900});
        for (const path of screens) {
          await page.goto(base + path);
          const result = page.locator('[data-analysis-result]');
          if (await result.count()) await result.locator('h2').first().waitFor();
          if (path.endsWith('state=running')) await result.getByRole('button', {name:language === 'en' ? 'Check run status again' : '실행 상태 다시 확인', exact:true}).waitFor();
          assert.equal(await page.locator('html').getAttribute('lang'), language);
          if (path.startsWith('/chat')) {
            if (width <= 768) await page.locator('#mobile-tab-scenario').click();
            await page.locator('#info-tab-guide').focus();
            await page.keyboard.press('ArrowRight');
            assert.equal(await page.locator('#info-tab-profile').getAttribute('aria-selected'), 'true');
            assert(await page.locator('#info-tab-profile').evaluate(el => el === document.activeElement));
          }
          const measured = await page.evaluate(new Function(`
            const {rgba,background,contrast} = (${colorMath})();
            const visible = el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
            const targets = [...document.querySelectorAll('button, a.btn, a.btn-primary, [role="tab"]')].filter(visible);
            const badTargets = targets.filter(el => {const r=el.getBoundingClientRect(); return r.width < 43.99 || r.height < 43.99 || r.left < -0.01 || r.right > innerWidth + .01;}).map(el => el.textContent.trim());
            const badContrast = [...document.querySelectorAll('body *')].filter(visible).filter(el => {
              if (['SCRIPT','STYLE','OPTION'].includes(el.tagName) || ![...el.childNodes].some(n=>n.nodeType===Node.TEXT_NODE && n.textContent.trim())) return false;
              const style=getComputedStyle(el), font=parseFloat(style.fontSize);
              const large=font>=24 || (font>=18.66 && Number(style.fontWeight)>=700);
              return contrast(rgba(style.color),background(el))+.01 < (large ? 3 : 4.5);
            }).map(el=>el.textContent.trim().slice(0,80));
            return {overflow:document.documentElement.scrollWidth > innerWidth, badTargets, badContrast};
          `));
          assert.equal(measured.overflow,false,`${language}/${theme}/${width}/${path} overflow`);
          assert.deepEqual(measured.badTargets,[],`${language}/${theme}/${width}/${path} targets`);
          assert.deepEqual(measured.badContrast,[],`${language}/${theme}/${width}/${path} contrast`);
        }
      }
    }
  }
  return {pageErrors:[]};
}
