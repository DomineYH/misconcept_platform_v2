import assert from 'node:assert/strict';
export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.route('**/admin/user-conversations/1/sessions', route => route.fulfill({status:403, json:[]}));
  await page.goto(`${base}/fixtures/uiux/admin/user_conversations`);
  await page.getByRole('button', {name:'개별', exact:true}).click();
  await page.getByText('세션 목록을 불러올 수 없습니다.', {exact:true}).waitFor({timeout:3000});
  assert(await page.locator('#sessionModal').evaluate(el => el.contains(document.activeElement)), 'failed load stays focused in its dialog');
  await page.keyboard.press('Escape');
  assert(await page.getByRole('button', {name:'개별',exact:true}).evaluate(el => el === document.activeElement));
  return {pageErrors:[]};
}
