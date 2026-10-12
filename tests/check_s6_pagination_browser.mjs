import assert from 'node:assert/strict';
import {chromium} from 'playwright';

const base = `http://127.0.0.1:${process.argv[2]}`;
const browser = await chromium.launch({headless:true});
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => new URL(route.request().url()).origin === base
    ? route.continue() : route.abort());
  async function login(username) {
    await page.goto(base + '/login');
    await page.locator('[name=username]').fill(username);
    await page.locator('[name=password]').fill('s6-browser-password');
    await Promise.all([page.waitForURL(url => url.pathname !== '/login'),
      page.locator('button[type=submit]').click()]);
  }
  await login('admin');
  for (const kind of ['users', 'scenarios']) {
    const url = `${base}/admin/${kind}`;
    const rows = page.locator(kind === 'users' ? '#users-table tbody tr[data-user-id]' : '.scenario-card');
    const nav = page.getByRole('navigation', {name:'목록 페이지'});
    for (const width of [1280, 390]) {
      await page.setViewportSize({width,height:900});
      await page.goto(url);
      assert.equal(await rows.count(), 20);
      assert((await nav.innerText()).includes('총 41개'));
      for (const [number, count] of [[2,20], [3,1]]) {
        await nav.getByRole('link', {name:'다음',exact:true}).focus();
        await Promise.all([page.waitForURL(`${url}?page=${number}`), page.keyboard.press('Enter')]);
        assert.equal(await rows.count(), count);
      }
      assert(await nav.getByRole('button', {name:'다음',exact:true}).isDisabled());
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.goto(`${url}?page=9`);
      assert.equal(await rows.count(), 0);
      assert(await page.getByText('이 페이지에 결과가 없습니다.', {exact:true}).isVisible());
      await nav.getByRole('link', {name:'이전',exact:true}).click();
      await page.waitForURL(`${url}?page=3`);
    }
    const csrf = (await page.context().cookies()).find(cookie => cookie.name === 'csrftoken').value;
    const rejected = await page.request.post(`${url}/${kind === 'users' ? 41 : 1}/delete`, {
      data: kind === 'users' ? {} : {expected_version:2}, headers:{'x-csrf-token':csrf}
    });
    assert.equal(rejected.status(), kind === 'users' ? 400 : 409);
    assert.equal(await rows.count(), 1);
    page.once('dialog', dialog => dialog.accept());
    await rows.locator('.delete-btn').click();
    await page.waitForURL(`${url}?page=2`);
    assert.equal(await rows.count(), 20);
    assert((await nav.innerText()).includes('총 40개'));
  }
  await login('teacher_40');
  for (const kind of ['users', 'scenarios', 'sessions']) {
    assert.equal((await page.request.get(`${base}/admin/${kind}`)).status(), 403);
  }
  assert.deepEqual(errors, []);
} finally {
  await browser.close();
}
