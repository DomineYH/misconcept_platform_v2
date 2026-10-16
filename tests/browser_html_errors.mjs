import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import {colorMath} from './uiux_color_math.mjs';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  const out = new URL('../test-results/impl144/', import.meta.url);
  await mkdir(out, {recursive: true});
  for (const width of [390, 1440]) {
    await page.setViewportSize({width, height: 900});
    for (const theme of ['light', 'dark']) {
      await page.emulateMedia({colorScheme: theme});
      for (const language of ['ko', 'en']) {
        await page.context().addCookies([{name: 'ui_lang', value: language, url: base}]);
        for (const status of [403, 404]) {
          const response = await page.goto(`${base}/fixtures/errors/${status}`);
          assert.equal(response.status(), status);
          assert.equal(await page.locator('html').getAttribute('lang'), language);
          const main = page.locator('main');
          const title = status === 404
            ? language === 'ko' ? '페이지를 찾을 수 없습니다' : 'Page not found'
            : language === 'ko' ? '접근 권한이 없습니다' : 'Access denied';
          assert.equal(await main.locator('h2').innerText(), title);
          const link = main.getByRole('link', {name: language === 'ko' ? '시나리오 목록으로' : 'Back to scenarios', exact: true});
          await link.waitFor({state: 'visible'});
          assert.equal(await page.locator('nav').count(), 1);
          assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
          const measure = new Function('el', `
            const {rgba, over, background, contrast} = (${colorMath})();
            const bg = background(el);
            return contrast(over(rgba(getComputedStyle(el).color), bg), bg);
          `);
          for (const element of [main.locator('h2'), main.locator('p').last(), link]) {
            assert(await element.evaluate(measure) >= 4.5, 'error text and return link meet AA contrast');
          }
          let reached = false;
          for (let tab = 0; tab < 20; tab++) {
            await page.keyboard.press('Tab');
            if (await link.evaluate(el => el === document.activeElement)) {
              reached = true;
              break;
            }
          }
          assert(reached, 'Tab reaches the return link');
          assert(await link.evaluate(el => parseFloat(getComputedStyle(el).outlineWidth) >= 2));
          await page.screenshot({path: new URL(`error-${status}-${language}-${theme}-${width}.png`, out).pathname});
          await Promise.all([page.waitForURL(`${base}/scenarios`), page.keyboard.press('Enter')]);
          assert.equal(new URL(page.url()).pathname, '/scenarios');
        }
      }
    }
  }
  return {pageErrors: []};
}
