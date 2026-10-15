import assert from 'node:assert/strict';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.context().addCookies([{name:'ui_lang',value:'en',url:base}]);
  const requests = [];
  await page.route('**/fixtures/s4/result?*', async route => {
    requests.push(route.request().headers()['x-ui-language']);
    await route.continue();
  });
  await page.goto(`${base}/fixtures/s4/analysis?state=partial`);
  const result = page.locator('[data-analysis-result]');
  await result.getByRole('heading', {name:'Accepted report: Degraded analysis'}).waitFor();
  assert(await result.getByText('Conversation reviewed: 6 / 7 messages', {exact:true}).isVisible());
  assert(await result.getByText('학생의 생각을 원문으로 확인하며 질문했습니다.', {exact:true}).isVisible());
  assert(await result.getByText('추론 질문: 2 items · 100%', {exact:true}).isVisible());
  assert.deepEqual(requests, ['en']);
  const running = await (await page.request.get(`${base}/fixtures/s4/result?state=running`)).json();
  const complete = await (await page.request.get(`${base}/fixtures/s4/result?state=ok`)).json();
  let releasePost, releaseStatus;
  const posted = new Promise(resolve => {
    page.route('**/sessions/1/analyze', async route => {
      assert.equal(route.request().headers()['x-ui-language'], 'en');
      await new Promise(release => { releasePost = release; resolve(); });
      await route.fulfill({status:202, json:running});
    });
  });
  const checking = new Promise(resolve => {
    page.route('**/sessions/1/analysis/runs/run-1', async route => {
      assert.equal(route.request().headers()['x-ui-language'], 'en');
      await new Promise(release => { releaseStatus = release; resolve(); });
      await route.fulfill({json:complete});
    });
  });
  // The cookie changes independently of this document before subsequent POST/status requests.
  await page.context().addCookies([{name:'ui_lang', value:'ko', url:base}]);
  await result.getByRole('button', {name:'Retry analysis', exact:true}).click();
  await posted;
  async function blocked() {
    await page.locator('[data-ui-language="ko"]').click();
    assert.equal(await page.locator('html').getAttribute('lang'), 'en');
    assert.equal(await page.locator('#language-notice').innerText(), 'Wait until the task finishes before switching languages.');
  }
  await blocked();
  releasePost();
  await result.getByRole('heading', {name:'Latest run: Analysis running'}).waitFor();
  await checking;
  await blocked();
  releaseStatus();
  await result.getByRole('heading', {name:'Latest run: Complete analysis'}).waitFor();
  await Promise.all([page.waitForEvent('load'), page.locator('[data-ui-language="ko"]').click()]);
  assert.equal(await page.locator('html').getAttribute('lang'), 'ko');
  return {pageErrors:[]};
}
