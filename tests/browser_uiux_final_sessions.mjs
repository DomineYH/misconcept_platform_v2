import assert from 'node:assert/strict';

export default async function check(page) {
  page.setDefaultTimeout(5000);
  const base = new URL(page.url()).origin;
  await page.context().addCookies([{name:'csrftoken',value:'final-session-csrf',url:base}]);
  let requests = 0;
  await page.route('**/admin/sessions/export-selected', route => {
    requests++;
    assert.equal(route.request().headers()['x-csrf-token'],'final-session-csrf');
    assert.equal(route.request().postData(),'session_ids=1');
    return route.fulfill({status:403,body:'PRIVATE_DOWNLOAD_ERROR'});
  });
  page.on('dialog',dialog=>dialog.accept());
  await page.goto(base+'/fixtures/uiux/admin/sessions');
  await page.locator('.session-checkbox').check();
  await page.locator('#btn-download-selected').click();
  const error=page.locator('.table-container').getByRole('alert');
  await error.waitFor();
  assert.equal(await error.innerText(),'선택한 세션을 다운로드하지 못했습니다. 권한과 세션 상태를 확인한 뒤 다시 시도하세요.');
  assert(!(await page.content()).includes('PRIVATE_DOWNLOAD_ERROR'));
  assert(await error.evaluate(el=>el===document.activeElement));
  assert(await page.locator('.session-checkbox').isChecked());
  assert.equal(await page.locator('#selected-count').innerText(),'1개 선택됨');
  assert(await page.locator('#btn-download-selected').isEnabled());
  assert.equal(await page.locator('#sessions-tbody tr').count(),1);
  assert.equal(requests,1,'download failure does not retry itself');
  return {pageErrors:[]};
}
