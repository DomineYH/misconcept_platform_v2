import assert from 'node:assert/strict';

export default async function checkAnalysis(page) {
  const origin = new URL(page.url()).origin;
  await page.route('**/sessions/1/analysis/detail-opened', route => route.fulfill({status: 204}));
  for (const width of [1280, 390]) {
    await page.setViewportSize({width, height: 800});
    await page.goto(`${origin}/fixtures/s2/analysis?off=1`);
    assert(await page.getByText('Narrative feedback', {exact: true}).isVisible());
    await page.getByRole('button', {name: /상세 분석/}).click();
    assert(await page.getByText('분류 미사용', {exact: true}).isVisible());
    assert.equal(await page.locator('.chart-container').count(), 0);
    await page.getByRole('tab', {name: '우수한 점'}).click();
    assert(await page.getByText('Narrative strength', {exact: true}).isVisible());
    await page.getByRole('tab', {name: '개선할 점'}).click();
    assert(await page.getByText('Narrative improvement', {exact: true}).isVisible());
    assert(await page.getByText('What about halves?', {exact: false}).isVisible());
    await page.goto(`${origin}/fixtures/s2/analysis`);
    await page.getByRole('button', {name: /상세 분석/}).click();
    assert(await page.getByText('Frozen display name', {exact: true}).isVisible());
    assert.equal(await page.getByText('stable_1', {exact: true}).count(), 0);
    assert(await page.getByText('잘 한 대화', {exact: true}).isVisible());
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  }
  return {pageErrors: []};
}
