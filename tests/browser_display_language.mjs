import assert from 'node:assert/strict';
import {colorMath} from './uiux_color_math.mjs';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  const choose = language => page.locator(`[data-ui-language="${language}"]`);
  async function openMenu() {
    if (await page.locator('.hamburger').isVisible() &&
        await page.locator('.hamburger').getAttribute('aria-expanded') === 'false') {
      await page.locator('.hamburger').click();
    }
  }
  async function switchTo(language) {
    await openMenu();
    await Promise.all([page.waitForEvent('load'), choose(language).click()]);
    assert.equal(await page.locator('html').getAttribute('lang'), language);
  }
  // Exercise the public injected-string contract with a synthetic interpolated entry.
  await page.route(`${base}/login`, async route => {
    const response = await page.request.get(`${base}/login`);
    const html = (await response.text()).replace(
      /(<script id="ui-strings" type="application\/json">)([\s\S]*?)(<\/script>)/,
      (_, open, json, close) => {
        const strings = JSON.parse(json);
        strings['common.interpolation_probe'] = 'Hello {name}';
        return open + JSON.stringify(strings) + close;
      }
    );
    await route.fulfill({response, body:html});
  });
  await page.goto(`${base}/login`);
  assert.deepEqual(await page.evaluate(() => [
    uiLanguage.t('unknown.key'), uiLanguage.t('toString'), uiLanguage.t('__proto__')
  ]), ['표시할 수 없는 안내입니다.', '표시할 수 없는 안내입니다.', '표시할 수 없는 안내입니다.']);
  assert.equal(await page.evaluate(() => uiLanguage.t('common.interpolation_probe', {name:'$&<img>'})), 'Hello $&<img>');
  assert.equal(await page.evaluate(() => uiLanguage.t('common.interpolation_probe', {name:'{other}', other:'not the name'})), 'Hello {other}');
  await page.unroute(`${base}/login`);
  await switchTo('en');
  assert.equal(await choose('en').getAttribute('aria-pressed'), 'true');
  assert.equal(await page.getByRole('group', {name:'Display language'}).count(), 1);
  assert.equal(await page.locator('.subtitle').innerText(), 'Teacher training platform');
  const cookie = (await page.context().cookies()).find(c => c.name === 'ui_lang');
  assert.equal(cookie.path, '/');
  assert.equal(cookie.sameSite, 'Lax');
  assert.equal(cookie.httpOnly, false);
  assert.equal(cookie.secure, false);
  assert(!cookie.domain.startsWith('.'), 'host-only cookie');
  assert(Math.abs(cookie.expires - Date.now() / 1000 - 31536000) < 10, 'one year');
  await page.reload();
  assert.equal(await page.locator('html').getAttribute('lang'), 'en');
  await page.goto(`${base}/login?error=1`);
  assert.match(await page.locator('#login-error').innerText(), /user ID or password is incorrect/);

  // Input protection checks the current values, including values left after failure.
  await page.setViewportSize({width:320,height:900});
  await openMenu();
  await page.locator('#username').fill('failed_user');
  await choose('ko').click();
  assert.equal(await page.locator('#language-notice').innerText(), 'Save or clear your input before switching languages.');
  assert.equal(await page.locator('#username').inputValue(), 'failed_user');
  const noticeBox = await page.locator('#language-notice').boundingBox();
  const menuBox = await page.locator('#nav-menu').boundingBox();
  assert(noticeBox.y + noticeBox.height <= menuBox.y || noticeBox.y >= menuBox.y + menuBox.height, 'blocked notice remains visible outside mobile menu');
  await page.locator('#username').fill('');
  await page.locator('#password').fill('password123');
  await choose('ko').click();
  assert.equal(await page.locator('html').getAttribute('lang'), 'en');
  await page.locator('#password').fill('');
  await switchTo('ko');

  await page.setViewportSize({width:1280,height:900});

  // Guard readers combine independent tasks and can be unregistered.
  await page.evaluate(() => {
    window.guardStates = [{busy:true}, {dirty:true}];
    window.unregister = guardStates.map(state => uiLanguage.registerGuard(() => state));
  });
  await choose('en').click();
  assert.equal(await page.locator('#language-notice').innerText(), '작업이 끝난 뒤 전환하세요');
  await page.evaluate(() => { guardStates[0].busy = false; });
  await choose('en').click();
  assert.equal(await page.locator('#language-notice').innerText(), '저장하거나 입력을 비운 뒤 전환하세요');
  await page.evaluate(() => unregister.forEach(remove => remove()));
  await page.locator('#open-about-modal').click();
  await page.locator('.contributor-card').first().waitFor();
  // An open read-only modal is not a guard; keyboard Escape restores the selector.
  await Promise.all([page.waitForEvent('load'), choose('en').evaluate(el => el.click())]);
  assert.equal(await page.locator('html').getAttribute('lang'), 'en', 'open modal alone permits switching');

  // Another tab changes the cookie while the open document keeps English.
  const sibling = await page.context().newPage();
  try {
    await sibling.goto(`${base}/login`);
    await Promise.all([sibling.waitForEvent('load'), sibling.locator('[data-ui-language="ko"]').click()]);
    assert.equal(await page.locator('html').getAttribute('lang'), 'en');
    let aboutHeader;
    page.on('request', request => {
      if (new URL(request.url()).pathname === '/about/content') aboutHeader = request.headers()['x-ui-language'];
    });
    await page.locator('#open-about-modal').click();
    await page.getByText('Email', {exact:true}).first().waitFor();
    assert.equal(aboutHeader, 'en');
    assert.equal(await page.locator('.contributor-name').first().innerText(), ('긴 소개 이름 \"따옴표\" '.repeat(8)).trim());
    const outcome = await page.evaluate(async () => {
      const target = document.createElement('div');
      document.body.append(target);
      await htmx.ajax('GET', '/about/content?empty=1', {target, swap:'innerHTML'});
      const result = target.textContent;
      target.remove();
      const external = uiLanguage.headers('https://example.org/test', {'X-UI-Language':'ko','x-csrf-token':'preserved'});
      const event = new CustomEvent('htmx:configRequest', {detail:{
        path:'https://example.org/test', headers:{'x-ui-language':'ko'}
      }});
      document.dispatchEvent(event);
      return {text:result, external:external.has('X-UI-Language'), csrf:external.get('x-csrf-token'), htmxExternal:Object.keys(event.detail.headers)};
    });
    assert.match(outcome.text, /No information has been added/);
    assert.equal(outcome.external, false);
    assert.deepEqual(outcome.htmxExternal, []);
    assert.equal(outcome.csrf, 'preserved');
    await page.keyboard.press('Escape');
  } finally { await sibling.close(); }

  // Cookie storage refusal must announce in the current language without reloading.
  await page.evaluate(() => Object.defineProperty(document, 'cookie', {configurable:true, get:() => '', set:() => {}}));
  let navigations = 0;
  const navigation = () => { navigations++; };
  page.on('framenavigated', navigation);
  await choose('ko').click();
  assert.equal(await page.locator('#language-notice').innerText(), 'Could not save your language. Allow cookies and try again.');
  assert.equal(navigations, 0);
  page.off('framenavigated', navigation);

  // An HTTPS document writes a Secure, host-only cookie without a live TLS service.
  const securePage = await page.context().newPage();
  try {
    await securePage.route('https://ui.example/**', async route => {
      const request = route.request();
      const url = new URL(request.url());
      const response = await page.request.get(`${base}${url.pathname}${url.search}`, {
        headers:{...request.headers(), cookie:request.headers().cookie || ''}
      });
      await route.fulfill({response});
    });
    await securePage.goto('https://ui.example/login');
    assert.equal(await securePage.locator('html').getAttribute('lang'), 'ko');
    await Promise.all([securePage.waitForEvent('load'), securePage.locator('[data-ui-language="en"]').click()]);
    assert.equal(await securePage.locator('html').getAttribute('lang'), 'en');
    const secureCookie = (await page.context().cookies('https://ui.example')).find(c => c.name === 'ui_lang');
    assert.equal(secureCookie.secure, true);
    assert.equal(secureCookie.httpOnly, false);
    assert.equal(secureCookie.domain, 'ui.example');
  } finally { await securePage.close(); }

  // Both themes, logged-out/in, five viewport widths, long English and keyboard.
  for (const colorScheme of ['light','dark']) {
    await page.emulateMedia({colorScheme});
    await page.context().addCookies([{name:'ui_lang',value:'en',url:base}]);
    for (const width of [320,390,768,769,1024,1440]) {
      await page.setViewportSize({width,height:900});
      for (const path of ['/login','/scenarios','/admin/scenarios?long_nickname=1']) {
        await page.goto(`${base}${path}`);
        await openMenu();
        assert.equal(await page.locator('html').getAttribute('lang'), 'en');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${colorScheme}/${width}/${path} overflow`);
        const header = await page.locator('body > header').boundingBox();
        const title = await page.locator('body > header h1').boundingBox();
        assert(title.y >= header.y && title.y + title.height <= header.y + header.height, 'long title fits header');
        const boxes = await page.locator('.language-selector button, .nav-item').evaluateAll(els => els.map(el => {
          const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height};
        }));
        for (const box of boxes) {
          assert(box.width >= 44 && box.height >= 44, '44px target');
          assert(box.x >= 0 && box.right <= width, 'target within viewport');
        }
        for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
          const a = boxes[i], b = boxes[j];
          assert(!(a.x < b.right && b.x < a.right && a.y < b.bottom && b.y < a.bottom), 'no overlapping targets');
        }
        await choose('en').focus();
        await page.keyboard.press('Shift+Tab');
        await page.keyboard.press('Tab');
        assert(await choose('en').evaluate(el => el === document.activeElement));
        const colors = await choose('en').evaluate(new Function('el', `
          const {rgba,background,contrast} = (${colorMath})();
          const s = getComputedStyle(el);
          return {text:contrast(rgba(s.color),background(el)),focus:contrast(rgba(s.outlineColor),background(el)),outline:s.outlineWidth};
        `));
        assert(colors.text >= 4.5 && colors.focus >= 3 && colors.outline === '2px', 'contrast and focus');
        if (path.includes('long_nickname')) assert.equal(await page.locator('.nav-username').innerText(), '긴한글별명'.repeat(20));
      }
    }
  }
  return {pageErrors:[]};
}
