import assert from 'node:assert/strict';

const DETAIL = {status:422, json:{detail:[
  {path:'problem.public_text', code:'required', message:'공개 문제 상황을 입력하세요.'},
  {path:'analysis.rubric.0.criteria', code:'required', message:'판정 기준을 입력하세요.'}
]}};

export default async function checkFailedSaveLeaveGuard(page) {
  const base = new URL(page.url()).origin;
  let response = DETAIL;
  await page.context().addCookies([{name:'csrftoken', value:'s2-token', url:base}]);
  await page.route('**/admin/scenarios/1/update', async route => {
  if (response === 'abort') return route.abort();
  await route.fulfill(response);
  });
  const warns = () => page.evaluate(() => { const e = new Event('beforeunload', {cancelable:true}); window.dispatchEvent(e); return e.defaultPrevented; });
  const publish = page.getByRole('button', {name:'수정 후 게시', exact:true});
  await page.goto(`${base}/fixtures/s2/editor?published=1`);
  await publish.waitFor({timeout:3000});
  // case1 fresh load without edits does not warn
  assert(!(await warns()), 'warned on fresh load without edits');
  // case2 422 without edits does not warn
  await publish.click();
  await page.locator('#editor-errors').waitFor({timeout:3000});
  assert(!(await warns()), 'warned after 422 without edits');
  await Promise.all([page.waitForEvent('load', {timeout:3000}), page.locator('[data-ui-language="en"]').click()]);
  assert.equal(await page.locator('html').getAttribute('lang'), 'en', 'language switch blocked after failed save');
  await Promise.all([page.waitForEvent('load', {timeout:3000}), page.locator('[data-ui-language="ko"]').click()]);
  assert.equal(await page.locator('html').getAttribute('lang'), 'ko', 'switching back blocked');
  await publish.waitFor({timeout:3000});
  // case3 409 without edits does not warn
  response = {status:409, json:{detail:{code:'version_conflict', current_version:2}}};
  await publish.click();
  await page.locator('#editor-conflict:not([hidden])').waitFor({timeout:3000});
  assert(!(await warns()), 'warned after 409 without edits');
  // case4 unsaved edits still warn after 422
  await page.getByLabel('제목', {exact:true}).fill('제목');
  response = DETAIL;
  await publish.click();
  await page.locator('#editor-errors').waitFor({timeout:3000});
  assert(await warns(), 'did not warn despite unsaved edits');
  // case5 uncertain save result keeps warning
  response = 'abort';
  page.once('dialog', dialog => dialog.accept());
  await page.reload();
  await publish.waitFor({timeout:3000});
  await publish.click();
  await page.locator('#editor-status[data-state="uncertain"]').waitFor({timeout:3000});
  assert(await warns(), 'did not warn after uncertain save result');
  return {pageErrors:[]};
}
