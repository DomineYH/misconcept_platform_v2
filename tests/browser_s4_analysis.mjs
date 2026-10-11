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
    await message.evaluate(el => el.remove());
    await link.click();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  }
  const partial = await (await page.request.get(`${origin}/fixtures/s4/result?state=partial`)).json();
  await page.route('**/fixtures/s4/result?state=partial', route => route.fulfill({json: {
    ...partial, latest_run: {status: 'failed', preserved: true, error_code: 'PRIVATE RAW ERROR'},
  }}));
  const requests = [];
  await page.route('**/sessions/1/analyze', route => {
    requests.push(route.request().postDataJSON());
    return route.fulfill({json: partial});
  });
  await page.goto(`${origin}/fixtures/s4/analysis?state=partial`);
  const result = page.locator('[data-analysis-result]');
  await result.getByRole('heading', {name: '채택된 보고서: 부분 분석'}).waitFor();
  assert(await result.getByText('이전 부분 분석을 보존했습니다. 최신 실행의 결과와 구별해 확인하세요.', {exact: true}).isVisible());
  assert(await result.getByRole('heading', {name: '최신 실행: 분석 실패'}).isVisible());
  assert(!(await result.innerText()).includes('PRIVATE RAW ERROR'));
  assert.equal(requests.length, 0, 'viewing a partial report never regenerates it');
  for (let i = 1; i <= 2; i++) {
    const response = page.waitForResponse(response => response.url().endsWith('/sessions/1/analyze'));
    await result.getByRole('button', {name: '분석 재시도', exact: true}).click();
    await response;
    await result.getByRole('heading', {name: '최신 실행: 부분 분석'}).waitFor();
    assert.equal(requests.length, i);
  }
  assert.notEqual(requests[0].request_id, requests[1].request_id, 'each explicit partial retry is a new request');
  const legacy = await (await page.request.get(`${origin}/fixtures/s4/result?state=legacy`)).json();
  await page.route('**/fixtures/s4/result?state=legacy', route => route.fulfill({json: {
    ...legacy, latest_run: {status: 'failed', preserved: true},
  }}));
  await page.goto(`${origin}/fixtures/s4/analysis?state=legacy`);
  await result.getByRole('heading', {name: '채택된 보고서: 과거 분석'}).waitFor();
  assert(await result.getByText('과거 분석을 보존했습니다. 최신 실행의 결과와 구별해 확인하세요.', {exact: true}).isVisible());
  assert(!(await result.innerText()).includes('이전 정상 결과'));
  return {checks: ['partial coverage, exact evidence, keyboard focus, mobile, partial preservation and explicit retry'], pageErrors: []};
}
