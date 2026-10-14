import assert from 'node:assert/strict';
export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.route('**/about/content', route => route.fulfill({status:503,body:'Unavailable'}));
  await page.goto(`${base}/login`);
  await page.locator('#open-about-modal').click();
  await page.locator('#about-load-error').waitFor();
  assert.equal(await page.locator('#about-modal-overlay').isVisible(), false);
  assert(await page.locator('#about-load-error').evaluate(el => el === document.activeElement));
  await page.waitForTimeout(100);
  assert(await page.locator('#about-load-error').isVisible(), 'load failure persists next to caller');
  await page.unroute('**/about/content');
  await page.locator('#open-about-modal').click();
  await page.locator('#about-modal-container .contributor-card').waitFor();
  await page.locator('#about-modal-container .modal-close').focus();
  await page.keyboard.press('Shift+Tab');
  assert(await page.locator('#about-modal-overlay').evaluate(el => el.contains(document.activeElement)));
  await page.keyboard.press('Escape');
  assert(await page.locator('#open-about-modal').evaluate(el => el === document.activeElement));
  return {pageErrors:[]};
}
