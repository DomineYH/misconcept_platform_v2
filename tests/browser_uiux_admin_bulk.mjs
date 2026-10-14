import assert from 'node:assert/strict';
export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.route('**/admin/users/bulk/preview', route => route.fulfill({status:422,json:{detail:'합성 CSV 입력 오류'}}));
  await page.goto(`${base}/admin/users`);
  await page.locator('#open-bulk-modal').click();
  await page.locator('#bulk-file-input').setInputFiles({name:'synthetic.csv',mimeType:'text/csv',buffer:Buffer.from('username,nickname\n')});
  await page.locator('#bulk-upload-error').waitFor({state:'visible'});
  assert(await page.locator('#bulk-upload-error').evaluate(el => el === document.activeElement), 'upload refusal focuses its local error');
  return {pageErrors:[]};
}
