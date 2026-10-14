import assert from 'node:assert/strict';
export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.route('**/admin/analysis/1/detail', route => route.fulfill({status:403,body:'denied'}));
  await page.goto(`${base}/fixtures/uiux/admin/analysis`);
  const caller = page.locator('[hx-target="#detail-modal"]');
  await caller.click();
  await page.locator('#detail-modal .modal-error').waitFor();
  assert(await page.locator('#detail-modal .modal-error').evaluate(el => el === document.activeElement));
  await page.keyboard.press('Escape');
  assert(await caller.evaluate(el => el === document.activeElement));
  await page.unroute('**/admin/analysis/1/detail');
  await page.route('**/admin/analysis/1/detail', async route => {
    const response = await page.request.get(`${base}/fixtures/uiux/admin/detail`);
    await route.fulfill({contentType:'text/html',body:await response.text()});
  });
  await caller.click();
  await page.locator('#detail-modal h3').waitFor();
  assert(await page.locator('#detail-modal').evaluate(el => el.contains(document.activeElement)));
  await page.locator('#detail-modal button').last().focus();
  await page.keyboard.press('Tab');
  assert(await page.locator('#detail-modal button').first().evaluate(el => el === document.activeElement));
  await page.keyboard.press('Escape');
  assert(await caller.evaluate(el => el === document.activeElement));
  return {pageErrors:[]};
}
