import assert from 'node:assert/strict';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  const payload = await (await page.request.get(`${base}/fixtures/s4/result?state=partial`)).json();
  const stored = '부분 분석: 검증된 6 / 7개 메시지의 결과입니다. 완료·실패·미실행 범위는 분석 범위에서 확인하세요.';
  payload.accepted_report.brief_feedback = [stored];
  await page.route('**/fixtures/s4/result?*', route => route.fulfill({json:payload}));
  for (const [language, expected] of [
    ['en', 'Partial analysis: validated results for 6 / 7 messages. Check analysis coverage for completed, failed and unprocessed ranges.'],
    ['ko', stored],
  ]) {
    await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
    await page.goto(`${base}/fixtures/s4/analysis?state=partial`);
    const result = page.locator('[data-analysis-result]');
    await result.getByRole('heading', {name:language === 'en' ? 'Accepted report: Degraded analysis' : '채택된 보고서: 부분 분석'}).waitFor();
    assert.equal(await result.locator('.analysis-brief-feedback').innerText(), expected);
    assert.deepEqual(payload.accepted_report.brief_feedback, [stored]);
  }
  // Degraded single-call feedback is AI content, even if its wording resembles an app notice.
  payload.accepted_report.coverage.chunks = [];
  await page.context().addCookies([{name:'ui_lang',value:'en',url:base}]);
  await page.goto(`${base}/fixtures/s4/analysis?state=partial`);
  await page.getByRole('heading', {name:'Accepted report: Degraded analysis'}).waitFor();
  assert.equal(await page.locator('.analysis-brief-feedback').innerText(), stored);
  return {pageErrors:[]};
}
