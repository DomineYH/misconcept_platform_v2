import assert from 'node:assert/strict';
export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.route('**/admin/users/bulk/preview', route => route.fulfill({json:{rows:[{row_num:1, username:'synthetic', nickname:'합성',role:'teacher',group_id:null,errors:[]}], groups:[],summary:{total:1,valid:1,error:0}}}));
  await page.route('**/admin/users/bulk/register', route => route.fulfill({json:{success_count:1,fail_count:0,failures:[],credentials:[{username:'synthetic',nickname:'합성',initial_password:'synthetic-only-secret'}]}}));
  await page.goto(`${base}/admin/users`);
  await page.locator('#open-bulk-modal').click();
  await page.locator('#bulk-file-input').setInputFiles({name:'synthetic.csv',mimeType:'text/csv',buffer:Buffer.from('username,nickname\nsynthetic,합성')});
  await page.locator('#bulk-register-btn').click();
  await page.locator('#bulk-credentials-body tr').waitFor();
  await page.evaluate(() => dispatchEvent(new PageTransitionEvent('pagehide', {persisted:true})));
  assert.equal(await page.locator('#bulk-credentials-body').textContent(), '', 'pagehide clears one-time credentials before BFCache');
  await page.locator('#open-create-modal').click();
  await page.locator('#new-password').fill('synthetic-password');
  await page.evaluate(() => dispatchEvent(new PageTransitionEvent('pagehide', {persisted:true})));
  assert.equal(await page.locator('#new-password').inputValue(),'');
  return {pageErrors:[]};
}
