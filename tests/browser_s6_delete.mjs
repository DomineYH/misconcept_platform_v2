import assert from 'node:assert/strict';

export default async function checkDeletePageNavigation(page) {
  const base = new URL(page.url()).origin;
  await page.context().addCookies([{name:'csrftoken', value:'s6-csrf', url:base}]);
  page.on('dialog', dialog => dialog.accept());
  for (const kind of ['users', 'scenarios']) {
    let total = 41, status = 403, attempts = 0, deleted = [];
    const listUrl = `${base}/admin/${kind}`;
    await page.route(new RegExp(`/admin/${kind}(\\?.*)?$`), async route => {
      const query = new URL(route.request().url()).searchParams;
      await route.fulfill({response:await route.fetch({
        url:`${base}/fixtures/s6/${kind}?page=${query.get('page') || 1}&total=${total}${deleted.map(id => `&deleted=${id}`).join('')}`
      })});
    });
    await page.route(`**/admin/${kind}/*/delete`, async route => {
      const request = route.request();
      attempts++;
      assert(request.method() === 'POST');
      assert(request.headers()['x-csrf-token'] === 's6-csrf', `${kind}: delete CSRF`);
      if (kind === 'scenarios') assert(request.postDataJSON().expected_version === 7, 'delete preserves revision');
      if (status === 200) deleted.push(Number(new URL(request.url()).pathname.split('/').at(-2)));
      await route.fulfill({status, json:status === 200 ? {status:'deleted'} : {detail:'권한이 없습니다.'}});
    });
    for (const width of [1280, 390]) {
      total = 41;
      deleted = [];
      status = 403;
      await page.setViewportSize({width, height:900});
      await page.goto(`${listUrl}?page=3`);
      for (const denied of [403, 409]) {
        status = denied;
        const response = page.waitForResponse(r => r.url().endsWith('/delete'));
        await page.locator('.delete-btn').click();
        await response;
        await page.waitForFunction(() => !document.querySelector('.delete-btn').disabled);
        assert(page.url() === `${listUrl}?page=3`, `${kind}: denied delete retains page`);
        assert(await page.locator('.delete-btn').count() === 1, `${kind}: denied delete retains row`);
      }
      status = 200;
      await Promise.all([
        page.waitForURL(`${listUrl}?page=2`), page.locator('.delete-btn').click()
      ]);
      await page.waitForLoadState('load');
      assert((await page.getByRole('navigation', {name:'목록 페이지'}).innerText()).includes('현재 2 페이지'), `${kind}: deleted last row returns to previous page`);
      assert(await page.locator('.delete-btn').count() === 20, `${kind}: previous valid page shown`);
      assert(await page.locator('.delete-btn[data-id="1"]').count() === 0, `${kind}: deleted row absent`);

      // One visible match is still a full page: hidden rows must count.
      total = 40;
      deleted = [];
      await page.goto(`${listUrl}?page=2`);
      await page.locator('#search-input').fill(kind === 'users' ? 'teacher_20' : '시나리오 20');
      const deletedResponse = page.waitForResponse(r => r.url().endsWith('/delete'));
      await page.locator('.delete-btn[data-id="20"]').click();
      await deletedResponse;
      await page.waitForFunction(() => document.querySelectorAll('.delete-btn').length === 19);
      assert(page.url() === `${listUrl}?page=2`, `${kind}: nonempty page retained`);

      total = 1;
      deleted = [];
      await page.goto(`${listUrl}?page=1`);
      const emptyResponse = page.waitForResponse(r => r.url().endsWith('/delete'));
      await page.locator('.delete-btn').click();
      await emptyResponse;
      await page.waitForFunction(() => document.querySelectorAll('.delete-btn').length === 0);
      assert(page.url() === `${listUrl}?page=1`, `${kind}: first page never becomes page zero`);
    }
    assert(attempts === 10, `${kind}: one request per delete`);
  }
  return {pageErrors:[]};
}
