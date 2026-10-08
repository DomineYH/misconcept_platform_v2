// The real shared admin history template, with synthetic persisted messages.
export default async function checkPhaseAHistory(page) {
  const base = new URL(page.url()).origin;
  const errors = [];
  const assert = (value, message) => { if (!value) throw new Error(message); };
  page.on('pageerror', error => errors.push(error.message));
  for (const width of [1280, 390]) {
    await page.setViewportSize({width, height:900});
    await page.goto(`${base}/chat?admin_history=1&ended=1`);
    const history = page.locator('#history-fixture');
    const toggle = history.locator('.analysis-detail-toggle');
    await toggle.focus();
    await page.keyboard.press('Enter');
    const messages = history.locator('.coach-msg');
    assert(await messages.count() === 6, 'all legacy and new history rows retained');
    assert(await messages.first().locator('.coach-msg__turn').count() === 0, 'legacy target never inferred');
    assert(await messages.nth(4).locator('.coach-msg__turn').innerText() === '2번째 턴', 'second student turn identified');
    assert(await messages.last().locator('.coach-msg__turn').innerText() === '1번째 턴', 'late coaching identifies original target');
    assert(await messages.last().locator('.coach-msg__role').innerText() === '멘토', 'stored tutor role displays mentor terminology');
    assert(await messages.last().locator('script').count() === 0, 'coaching markup escaped');
    assert(await history.getByRole('link', {name:'CSV 다운로드'}).getAttribute('href') === '/admin/sessions/1/download', 'admin export route retained');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'history fits viewport');
    assert(await toggle.getAttribute('aria-expanded') === 'true', 'keyboard opens detail with accessible state');
  }
  assert(errors.length === 0, errors.join('; '));
  return {checks:['desktop/mobile admin history identifies late mentor target, legacy order, escaped text, CSV and keyboard'], pageErrors:errors};
}
