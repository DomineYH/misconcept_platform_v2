import assert from 'node:assert/strict';

// Public screen seam: native login validation and assigned-scenario navigation.
export default async function check(page) {
  const base = new URL(page.url()).origin;
  for (const colorScheme of ['light', 'dark']) {
    await page.emulateMedia({colorScheme});
    for (const width of [320, 390, 768, 769, 1024, 1440]) {
      await page.setViewportSize({width, height: 900});
      await page.goto(`${base}/login?error=1`);
      const card = await page.locator('.login-card').boundingBox();
      assert(card.width <= 480 && card.width >= Math.min(480, width - 32), 'login uses available width up to 480px');
      await page.locator('.skip-link').focus();
      await Promise.all([
        page.waitForURL(`${base}/login?error=1#main-content`),
        page.keyboard.press('Enter')
      ]);
      assert.equal(new URL(page.url()).hash, '#main-content', 'keyboard skip link reaches main content');
      await page.getByRole('button', {name: '로그인', exact: true}).click();
      assert.equal(await page.locator('#username').evaluate(el => el === document.activeElement), true, 'required ID receives focus');
      assert.match(await page.getByRole('alert').innerText(), /올바르지 않습니다/);
      assert.match(await page.getByLabel('사용자 ID', {exact: false}).getAttribute('aria-describedby'), /username-help/);
      assert.equal(await page.getByRole('link', {name: '관리자', exact: true}).count(), 0);
      await page.goto(`${base}/scenarios?long=1`);
      if (width <= 768) {
        await page.getByRole('button', {name: '메뉴', exact: true}).focus();
        await page.keyboard.press('Enter');
        assert.equal(await page.locator('.hamburger').getAttribute('aria-expanded'), 'true');
      }
      assert.equal(await page.getByRole('link', {name: '시나리오', exact: true}).getAttribute('aria-current'), 'page');
      assert.equal(await page.getByRole('link', {name: '대화 시작'}).getAttribute('href'), '/scenarios/1');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${colorScheme}/${width}: no page overflow`);
      const start = await page.getByRole('link', {name: '대화 시작'}).boundingBox();
      assert(start.width >= 44 && start.height >= 44, 'start action target');
      const startLink = page.getByRole('link', {name: '대화 시작'});
      await startLink.focus();
      await page.keyboard.press('Shift+Tab');
      await page.keyboard.press('Tab');
      assert(await startLink.evaluate(el => el === document.activeElement), 'start link remains in keyboard order');
      const focus = await startLink.evaluate(el => {
        const s = getComputedStyle(el); return {width: s.outlineWidth, offset: s.outlineOffset, gap: s.boxShadow};
      });
      assert.equal(focus.width, '2px');
      assert.equal(focus.offset, '2px');
      assert.match(focus.gap, /2px/);
      const colors = await startLink.evaluate(el => {
        const s = getComputedStyle(el); return [s.color, s.backgroundColor];
      });
      assert.deepEqual(colors, colorScheme === 'light' ? ['rgb(255, 255, 255)', 'rgb(59, 63, 216)'] : ['rgb(16, 17, 36)', 'rgb(139, 143, 247)']);
      await page.goto(`${base}/scenarios?many=1`);
      const cards = await page.locator('.scenario-card').all();
      const boxes = await Promise.all(cards.map(card => card.boundingBox()));
      assert.equal(boxes.length, 3);
      assert.equal(boxes[0].y === boxes[1].y, width > 768, 'one column through 768px, auto-fit thereafter');
      await page.goto(`${base}/scenarios?empty=1`);
      assert.equal(await page.getByRole('link', {name: '대화 시작'}).count(), 0);
      assert.match(await page.locator('.empty-state').innerText(), /배정|사용할 수 있는/);
      await page.goto(`${base}/scenarios?missing_problem=1`);
      assert.equal(await page.getByRole('button', {name: '대화 시작'}).isDisabled(), true);
      assert.match(await page.locator('.scenario-card').innerText(), /관리자/);
      const disabled = page.getByRole('button', {name: '대화 시작'});
      await disabled.hover({force: true});
      assert.equal(await disabled.evaluate(el => getComputedStyle(el).opacity), '1');
      assert(!/PRIVATE|legacy-secret/.test(await page.content()), 'only public information');
    }
  }
  return {pageErrors: []};
}
