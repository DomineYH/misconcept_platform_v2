import assert from 'node:assert/strict';

export default async function checkAnalysisEvidence(page) {
  const origin = new URL(page.url()).origin;
  for (const width of [1280, 390]) {
    await page.setViewportSize({width, height: 800});
    await page.goto(`${origin}/fixtures/s4/analysis?state=partial`);
    const result = page.locator('[data-analysis-result]');
    await result.getByRole('heading', {name: '채택된 보고서: 부분 분석'}).waitFor();
    assert(await result.getByText('대화 검토: 6 / 7개 메시지', {exact: true}).isVisible());
    assert(await result.getByText('분류 비율 분모: 2개 · 전체 교사 발화: 5개', {exact: true}).isVisible());
    for (const label of ['비분석: 1개', '미분류: 1개', '분류 누락: 1개', '검증 제외: 1개', '학생 응답 없음: 3개']) {
      assert(await result.getByText(label, {exact: true}).isVisible(), label);
    }
    const link = result.getByRole('link', {name: '원문 103: 분모가 5라서 더 커요.'});
    await link.focus();
    await page.keyboard.press('Enter');
    const message = result.locator('[data-message-id="103"]');
    assert(await message.isVisible());
    assert(await message.evaluate(el => document.activeElement === el));
    assert.match(await result.getByRole('status').innerText(), /메시지 103/);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  }
  return {checks: ['partial coverage, exact evidence, keyboard focus and mobile'], pageErrors: []};
}
