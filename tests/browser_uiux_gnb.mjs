import assert from 'node:assert/strict';
import {colorMath} from './uiux_color_math.mjs';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  const measurements = [];
  for (const colorScheme of ['light', 'dark']) {
    await page.emulateMedia({colorScheme});
    for (const width of [320, 390, 767, 768, 769, 1024, 1440]) {
      await page.setViewportSize({width, height: 900});
      await page.goto(`${base}/admin/scenarios?long_nickname=1`);
      const menu = page.locator('#nav-menu');
      const hamburger = page.locator('.hamburger');
      if (width <= 768) {
        assert.equal(await menu.isVisible(), false);
        assert.equal(await hamburger.getAttribute('aria-controls'), 'nav-menu');
        await hamburger.focus();
        await page.keyboard.press('Enter');
        assert.equal(await hamburger.getAttribute('aria-expanded'), 'true');
      }
      assert.equal(await menu.locator('.nav-links .nav-item').count(), 3);
      assert.equal(await menu.locator('.nav-account .nav-item').count(), 1);
      assert.equal(await menu.getByRole('link', {name:'관리자', exact:true}).getAttribute('aria-current'), 'page');
      assert.equal(await menu.locator('.nav-username').innerText(), '긴한글별명'.repeat(20));
      const links = await menu.locator('.nav-links').boundingBox();
      const account = await menu.locator('.nav-account').boundingBox();
      if (width > 768) {
        assert(account.x >= links.x + links.width, 'account follows navigation on the right');
        const nav = await page.locator('body > header nav').boundingBox();
        assert(Math.abs(account.x + account.width - nav.x - nav.width) < 1, 'account aligns to right edge');
      } else {
        assert(account.y >= links.y + links.height, 'mobile account is a separate group');
      }
      const items = await menu.locator('.nav-item').all();
      const weights = [];
      let minText = Infinity, minFocus = Infinity, minWidth = Infinity, minHeight = Infinity;
      for (const item of items) {
        const box = await item.boundingBox();
        assert(box.width >= 44 && box.height >= 44, '44px target');
        minWidth = Math.min(minWidth, box.width);
        minHeight = Math.min(minHeight, box.height);
        assert(box.x >= 0 && box.x + box.width <= width, 'target fits viewport');
        for (const state of ['normal', 'hover', 'focus', 'disabled']) {
          if (state === 'hover') await item.hover();
          if (state === 'focus') {
            await item.focus();
            await page.keyboard.press('Tab');
            await page.keyboard.press('Shift+Tab');
          }
          if (state === 'disabled') {
            if (await item.evaluate(el => el.tagName !== 'BUTTON')) continue;
            await item.evaluate(el => { el.disabled = true; });
          }
          await item.evaluate(el => Promise.all(el.getAnimations().map(a => a.finished.catch(() => {}))));
          const style = await item.evaluate(new Function('el', `
            const {rgba, background, contrast} = (${colorMath})();
            const s = getComputedStyle(el);
            return {text:contrast(rgba(s.color),background(el)), focus:contrast(rgba(s.outlineColor),background(el)),
              outline:s.outlineWidth, weight:s.fontWeight, font:s.fontSize, padding:s.padding,
              bg:s.backgroundColor, border:s.borderWidth, opacity:s.opacity};
          `));
          assert(style.text >= 4.5, `${colorScheme}/${width}/${state}: text contrast ${style.text}`);
          minText = Math.min(minText, style.text);
          if (state === 'normal') {
            weights.push([style.weight, style.font, style.padding]);
            assert.equal(style.bg, 'rgba(0, 0, 0, 0)');
            assert.equal(style.border, '0px');
          }
          if (state === 'focus') {
            assert.equal(style.outline, '2px');
            assert(style.focus >= 3, 'visible focus contrast');
            minFocus = Math.min(minFocus, style.focus);
          }
          if (state === 'disabled') {
            assert.equal(style.opacity, '1');
            await item.evaluate(el => { el.disabled = false; });
          }
          await page.mouse.move(0, 899);
        }
      }
      assert(weights.every(value => JSON.stringify(value) === JSON.stringify(weights[0])), 'same menu typography and spacing');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'no page overflow');
      await page.locator('#open-about-modal').focus();
      await page.keyboard.press('Enter');
      await page.locator('#about-modal-container .contributor-card').waitFor();
      await page.keyboard.press('Escape');
      assert(await page.locator('#open-about-modal').evaluate(el => el === document.activeElement));
      if (width <= 768) {
        await hamburger.click();
        assert.equal(await hamburger.getAttribute('aria-expanded'), 'false');
        assert.equal(await menu.isVisible(), false);
      }
      measurements.push({colorScheme, width, minText, minFocus, minWidth, minHeight});
    }
  }
  await page.goto(`${base}/scenarios`);
  await page.context().addCookies([{name:'csrftoken',value:'gnb-csrf',url:base}]);
  let logout;
  await page.route('**/logout', route => {
    logout = {method:route.request().method(), csrf:route.request().headers()['x-csrf-token']};
    return route.fulfill({status:200,body:''});
  });
  await Promise.all([
    page.waitForURL(`${base}/login`),
    page.getByRole('button', {name:'로그아웃',exact:true}).click()
  ]);
  assert.deepEqual(logout, {method:'POST',csrf:'gnb-csrf'});
  console.log('GNB measurements', JSON.stringify(measurements));
  return {pageErrors:[]};
}
