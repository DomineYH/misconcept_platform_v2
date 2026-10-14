import assert from 'node:assert/strict';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.goto(`${base}/fixtures/uiux/admin/groups`);
  await page.locator('#open-create-modal').click();
  assert.equal(await page.locator('#new-name').evaluate(el => el === document.activeElement), true,
    'opening group creation focuses the required name');
  await page.keyboard.press('Shift+Tab');
  assert.equal(await page.locator('#create-modal').evaluate(el => el.contains(document.activeElement)), true,
    'focus remains in the open dialog');
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#open-create-modal').evaluate(el => el === document.activeElement), true,
    'Escape returns to the caller');
  return {pageErrors:[]};
}
