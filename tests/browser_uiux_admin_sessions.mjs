import assert from 'node:assert/strict';
export default async function check(page) {
  const base = new URL(page.url()).origin;
  page.on('dialog', dialog => dialog.accept());
  await page.context().addCookies([{name:'csrftoken',value:'session-uiux',url:base}]);
  await page.route('**/admin/sessions/1/delete', route => route.fulfill({status:403,body:'denied'}));
  await page.goto(`${base}/fixtures/uiux/admin/sessions?active=1`);
  await page.locator('[hx-swap="delete"]').click();
  await page.locator('main .modal-error').waitFor({timeout:3000});
  assert.equal(await page.locator('#sessions-tbody tr').count(),1,'rejected deletion retains row');
  const tokens = [];
  await page.route('**/admin/sessions/1/end', async route => {
    tokens.push(route.request().headers()['x-csrf-token']);
    const response = await page.request.get(`${base}/fixtures/uiux/admin/row`);
    await route.fulfill({contentType:'text/html',body:await response.text()});
  });
  await page.locator('[hx-swap="outerHTML"]').click();
  await page.locator('.session-checkbox').waitFor();
  await page.locator('.session-checkbox').check();
  assert.equal(await page.locator('#selected-count').innerText(),'1개 선택됨');
  assert.equal(await page.locator('#btn-download-selected').isEnabled(),true);
  await page.route('**/admin/sessions/export-selected', async route => {
    tokens.push(route.request().headers()['x-csrf-token']);
    assert.equal(route.request().postData(),'session_ids=1');
    await route.fulfill({contentType:'text/csv',headers:{'Content-Disposition':'attachment; filename=selected.csv'},body:'synthetic,csv\n1,record'});
  });
  const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#btn-download-selected').click()]);
  assert.equal(download.suggestedFilename(),'selected.csv');
  await page.route('**/admin/sessions/1/analysis_modal', route => route.fulfill({status:503,body:'unavailable'}));
  await page.locator('.view-analysis-btn').click();
  await page.locator('#analysis-modal .modal-error').waitFor();
  assert(await page.locator('#analysis-modal').evaluate(el => el.contains(document.activeElement)));
  assert(await page.locator('#analysis-modal .modal-container').evaluate(el => el.contains(el.querySelector('.modal-error'))), 'analysis load error belongs inside the modal card');
  await page.keyboard.press('Escape');
  assert(await page.locator('.view-analysis-btn').evaluate(el => el === document.activeElement));
  assert.deepEqual(tokens,['session-uiux','session-uiux']);
  return {pageErrors:[]};
}
