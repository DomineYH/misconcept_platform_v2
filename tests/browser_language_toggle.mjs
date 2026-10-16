import assert from 'node:assert/strict';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  const toggle = page.locator('[data-ui-language]');
  async function state() {
    return {
      language:await page.locator('html').getAttribute('lang'),
      target:await toggle.getAttribute('data-ui-language'),
      label:await toggle.getAttribute('aria-label'),
      text:await toggle.innerText(),
    };
  }
  for (const language of ['ko','en']) {
    const expected = {
      language,
      target:language === 'ko' ? 'en' : 'ko',
      label:language === 'ko' ? '표시 언어: KOR' : 'Display language: EN',
      text:language === 'ko' ? 'KOR' : 'EN',
    };
    await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
    for (const width of [320,768,769,1440]) {
      await page.setViewportSize({width,height:900});
      for (const path of ['/login','/scenarios','/admin/scenarios?long_nickname=1']) {
        await page.goto(base + path);
        assert.equal(await toggle.count(), 1);
        if (width <= 768) {
          assert.equal(await toggle.isVisible(), false, 'toggle follows the hamburger menu');
          await page.locator('.hamburger').focus();
          await page.keyboard.press('Enter');
        }
        assert.deepEqual(await state(), expected);
        const box = await toggle.boundingBox();
        const menuRight = await page.locator('#nav-menu').evaluate(el =>
          el.getBoundingClientRect().right - parseFloat(getComputedStyle(el).paddingRight));
        assert(Math.abs(box.x + box.width - menuRight) < 1, `${language}/${width}/${path}: toggle at right edge`);
        assert(box.width >= 44 && box.height >= 44, '44px language target');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
        if (path === '/login') {
          assert.equal(await page.locator('.nav-account').count(), 0);
          assert.equal(await page.locator('#nav-menu > .language-selector').count(), 1);
        } else {
          assert.equal(await page.locator('.nav-account > li:last-child.language-selector').count(), 1);
          if (width > 768) {
            const logout = await page.locator('.nav-logout').boundingBox();
            assert(box.x >= logout.x + logout.width, 'toggle is right of logout');
          }
        }
      }
    }

    await page.setViewportSize({width:1280,height:900});
    await page.goto(`${base}/login`);
    await page.evaluate(() => {
      window.toggleGuard = {dirty:true};
      window.removeToggleGuard = uiLanguage.registerGuard(() => toggleGuard);
    });
    for (const guard of [{dirty:true},{busy:true}]) {
      await page.evaluate(value => { window.toggleGuard = value; }, guard);
      await toggle.click();
      await page.locator('#language-notice').waitFor({state:'visible'});
      assert.deepEqual(await state(), expected, 'blocked switch preserves the current toggle');
      assert.equal((await page.context().cookies(base)).find(cookie => cookie.name === 'ui_lang').value, language);
    }
    await page.evaluate(() => removeToggleGuard());

    for (const throws of [false,true]) {
      await page.evaluate(shouldThrow => Object.defineProperty(document, 'cookie', {
        configurable:true,
        get:() => '',
        set:() => { if (shouldThrow) throw new Error('Cookie storage refused'); },
      }), throws);
      await toggle.click();
      assert.deepEqual(await state(), expected, 'failed cookie storage preserves the current toggle');
      assert.match(await page.locator('#language-notice').innerText(), language === 'ko' ? /쿠키/ : /cookies/);
    }
    await page.reload();
    for (const key of ['Enter','Space']) {
      const next = (await state()).target;
      await toggle.focus();
      await Promise.all([page.waitForEvent('load'),page.keyboard.press(key)]);
      assert.equal(await page.locator('html').getAttribute('lang'), next);
      assert.equal(await toggle.innerText(), next === 'ko' ? 'KOR' : 'EN');
    }
    assert.deepEqual(await state(), expected, 'both keyboard switches update state after reload');
  }
  return {pageErrors:[]};
}
