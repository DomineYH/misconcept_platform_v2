import assert from 'node:assert/strict';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.context().addCookies([{name:'ui_lang',value:'en',url:base}]);
  await page.goto(`${base}/fixtures/uiux/admin/sessions`);
  await page.locator('.session-checkbox').check();
  assert.equal(await page.locator('#selected-count').innerText(),'1 selected');
  let complete, received;
  const started = new Promise(resolve => { received = resolve; });
  await page.route('**/admin/sessions/export-selected', async route => {
    assert.equal(route.request().headers()['x-ui-language'],'en');
    await new Promise(resolve => {
      complete = () => route.fulfill({status:403,body:'PRIVATE ERROR'}).then(resolve);
      received();
    });
  });
  await page.context().addCookies([{name:'ui_lang',value:'ko',url:base}]);
  await page.locator('#btn-download-selected').click();
  await started;
  await page.locator('[data-ui-language="ko"]').click();
  assert.match(await page.locator('#language-notice').innerText(),/Wait until/);
  await complete();
  await page.getByText('Could not download the selected sessions. Check permissions and session status, then try again.',{exact:true}).waitFor();
  assert(!(await page.locator('main').innerText()).includes('PRIVATE ERROR'));
  await page.locator('.session-checkbox').uncheck();
  await page.route('**/admin/sessions/1/analysis_modal', route => {
    assert.equal(route.request().headers()['x-ui-language'],'en');
    return route.fulfill({status:503,body:'PRIVATE ANALYSIS ERROR'});
  });
  await page.locator('.view-analysis-btn').click();
  await page.getByText('Could not load analysis results.',{exact:true}).waitFor();
  // A completed read-only load leaves no input to protect in the open modal.
  await Promise.all([page.waitForEvent('load'),page.locator('[data-ui-language="ko"]').evaluate(button => button.click())]);
  return {pageErrors:[]};
}
