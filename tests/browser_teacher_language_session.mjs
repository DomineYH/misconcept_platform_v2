import assert from 'node:assert/strict';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.context().addCookies([{name:'ui_lang',value:'en',url:base}]);
  const dialogs = [], requests = [];
  page.on('dialog', async dialog => { dialogs.push(dialog.message()); await dialog.accept(); });
  let releaseEnd;
  const ending = new Promise(resolve => {
    page.route('**/sessions/1/end', async route => {
      requests.push(route.request().headers()['x-ui-language']);
      if (requests.length === 1) {
        await new Promise(release => { releaseEnd = release; resolve(); });
        await route.fulfill({status:500, body:'PRIVATE SERVER ERROR'});
      } else await route.fulfill({status:202, json:{latest_run:{status:'running'}}});
    });
  });
  await page.route('**/sessions/1/analysis_modal', async route => {
    const html = await (await page.request.get(`${base}/fixtures/s4/modal?state=ok`, {headers:{'X-UI-Language':'en'}})).text();
    await route.fulfill({contentType:'text/html',body:html});
  });
  await page.goto(`${base}/chat`);
  const opener = page.locator('#end-session-btn');
  await opener.click();
  await ending;
  await page.locator('[data-ui-language="ko"]').click();
  assert.equal(await page.locator('#language-notice').innerText(), 'Wait until the task finishes before switching languages.');
  assert.equal(dialogs[0], 'End this session?');
  releaseEnd();
  await page.waitForFunction(() => document.querySelector('#end-session-btn').dataset.state === 'ready-to-analyze');
  assert(dialogs.includes('Analysis failed'));
  assert(!dialogs.join(' ').includes('PRIVATE SERVER ERROR'));
  await page.locator('[data-ui-language="ko"]').click();
  assert.equal(await page.locator('html').getAttribute('lang'), 'en', 'unconfirmed server outcome remains protected');
  await page.context().addCookies([{name:'ui_lang',value:'ko',url:base}]);
  await opener.click();
  const modal = page.getByRole('dialog', {name:'Session analysis results',exact:true});
  await modal.getByRole('heading', {name:'Accepted report: Complete analysis'}).waitFor();
  assert.deepEqual(requests, ['en','en']);
  // A read-only result modal alone is not dirty or busy, even while still open.
  await Promise.all([page.waitForEvent('load'), page.evaluate(() => document.querySelector('[data-ui-language="ko"]').click())]);
  assert.equal(await page.locator('html').getAttribute('lang'), 'ko');
  return {pageErrors:[]};
}
