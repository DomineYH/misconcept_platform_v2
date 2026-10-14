import assert from 'node:assert/strict';
export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.setViewportSize({width:390, height:700});
  for (const name of ['sessions', 'analysis', 'user_conversations', 'about']) {
    await page.goto(`${base}/fixtures/uiux/admin/${name}`);
    const table = page.locator('main table').first();
    const region = table.locator('..');
    assert.equal(await region.getAttribute('role'), 'region', `${name}: named keyboard table region`);
    assert.equal(await region.getAttribute('tabindex'), '0');
    assert(await region.getAttribute('aria-label'));
    await region.focus();
    for (let i = 0; i < 8; i++) await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(250);
    assert(await region.evaluate(el => el.scrollLeft > 0), `${name}: real keyboard scroll reaches remaining columns`);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name}: no document overflow`);
  }
  return {pageErrors:[]};
}
