import assert from 'node:assert/strict';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  const running = await (await page.request.get(`${base}/fixtures/s4/result?state=running`)).json();
  let poll = 0;
  await page.route('**/sessions/1/analysis/runs/run-1', route => route.fulfill({json:{
    ...running, latest_run:{...running.latest_run, progress:++poll},
  }}));
  await page.goto(`${base}/fixtures/s4/analysis?state=running`);
  const result = page.locator('[data-analysis-result]');
  await result.getByRole('heading', {name:'최신 실행: 분석 진행 중', exact:true}).waitFor();
  assert(await result.getByRole('heading', {name:'채택된 보고서: 정상 분석', exact:true}).isVisible());
  assert(await result.getByText('생성 시각: 2026-10-01 10:00', {exact:true}).isVisible());
  assert(await result.getByText('실행 시작: 2026-10-02T11:00:00', {exact:true}).isVisible(), 'latest execution time is separate from accepted report time');
  await result.locator('summary').focus();
  await page.waitForResponse(response => response.url().endsWith('/runs/run-1'));
  await page.waitForTimeout(100);
  assert(await result.locator('summary').evaluate(el => el === document.activeElement), 'status refresh preserves transcript keyboard focus');
  assert(await result.getByText('새 분석이 진행 중입니다. 아래 채택된 보고서는 새 정상 결과가 검증될 때까지 유지됩니다.', {exact:true}).isVisible());
  const cancel = result.getByRole('button', {name:'진행 중 분석 취소', exact:true});
  await cancel.focus();
  await page.waitForResponse(response => response.url().endsWith('/runs/run-1'));
  await page.waitForTimeout(100);
  assert(await cancel.evaluate(el => el === document.activeElement), 'status refresh preserves action keyboard focus');
  await page.evaluate(() => {
    window.announcements = [];
    const root = document.querySelector('[data-analysis-result]');
    new MutationObserver(records => {
      for (const record of records) if (record.target.closest?.('[role="status"]')) announcements.push(record.target.textContent);
    }).observe(root, {subtree:true, childList:true, characterData:true});
  });
  await page.waitForResponse(response => response.url().endsWith('/runs/run-1'));
  await page.waitForTimeout(100);
  assert.deepEqual(await page.evaluate(() => announcements), [], 'unchanged running status is not repeatedly announced');
  return {pageErrors:[]};
}
