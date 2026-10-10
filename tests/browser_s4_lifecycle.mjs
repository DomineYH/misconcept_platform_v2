import assert from 'node:assert/strict';

export default async function checkExplicitAnalysisLifecycle(page) {
  const origin = new URL(page.url()).origin;
  const fixture = async state => (await page.request.get(`${origin}/fixtures/s4/result?state=${state}`)).json();
  const running = await fixture('running');
  const cancelled = await fixture('cancelled');
  const submitted = [];
  let polls = 0, cancellations = 0;
  await page.route('**/sessions/1/analyze', async route => {
    submitted.push(route.request().postDataJSON());
    if (submitted.length === 1) await route.abort('failed');
    else await route.fulfill({status: 202, json: running});
  });
  await page.route('**/sessions/1/analysis/runs/run-1', route => {
    polls++;
    return route.fulfill({status: polls === 1 ? 500 : 200,
      json: polls === 1 ? {detail: 'PRIVATE_SDK_ERROR'} : running});
  });
  await page.route('**/sessions/1/analysis/runs/run-1/cancel', route => {
    cancellations++;
    return route.fulfill({status: 202, json: cancelled});
  });
  await page.goto(`${origin}/fixtures/s4/analysis?state=failed`);
  const result = page.locator('[data-analysis-result]');
  await result.getByRole('button', {name: '분석 재시도', exact: true}).click();
  await result.getByRole('button', {name: '같은 요청 다시 전송', exact: true}).click();
  await result.getByRole('heading', {name: '최신 실행: 분석 진행 중'}).waitFor();
  assert.equal(submitted.length, 2);
  assert.equal(submitted[0].request_id, submitted[1].request_id, 'lost response replays the same request');
  await page.waitForResponse(response => response.url().endsWith('/analysis/runs/run-1'));
  assert(polls >= 1, 'only the active analysis run is polled');
  const recheck = result.getByRole('button', {name: '실행 상태 다시 확인', exact: true});
  await recheck.click();
  await recheck.waitFor({state: 'detached'});
  assert.equal(submitted.length, 2, 'checking status again cannot create a new analysis');
  assert(!(await result.innerText()).includes('PRIVATE_SDK_ERROR'));
  assert(await result.getByText('취소해도 이미 발생한 제공자 비용은 되돌릴 수 없습니다.', {exact: true}).isVisible());
  await result.getByRole('button', {name: '진행 중 분석 취소', exact: true}).click();
  await result.getByRole('heading', {name: '최신 실행: 분석 취소됨'}).waitFor();
  assert.equal(cancellations, 1);
  const stopped = polls;
  await page.waitForTimeout(1300);
  assert.equal(polls, stopped, 'terminal run stops polling');
  assert(await result.getByRole('heading', {name: '채택된 보고서: 정상 분석'}).isVisible());
  assert.equal(submitted.length, 2, 'cancel does not restart analysis');

  await page.route('**/sessions/1/analyze', route => route.fulfill({status: 409,
    json: {detail: {code: 'analysis_busy', run_id: 'run-1'}}}));
  await page.goto(`${origin}/fixtures/s4/analysis?state=failed`);
  await result.getByRole('button', {name: '분석 재시도', exact: true}).click();
  await result.getByRole('heading', {name: '최신 실행: 분석 진행 중'}).waitFor();
  assert.match(await result.getByRole('status').innerText(), /진행 중인 분석 실행/);

  await page.route('**/sessions/1/analyze', route => route.fulfill({status: 409,
    json: {detail: {code: 'request_conflict'}}}));
  await page.goto(`${origin}/fixtures/s4/analysis?state=failed`);
  await result.getByRole('button', {name: '분석 재시도', exact: true}).click();
  await result.getByRole('status').filter({hasText: '요청 입력이 변경되어'}).waitFor();
  assert.equal(await result.getByRole('button', {name: '같은 요청 다시 전송', exact: true}).count(), 0);

  await page.route('**/fixtures/s4/result?state=failed', route => route.fulfill({json: {
    ...running, accepted_report: null, latest_run: {status: 'ok', superseded: true},
  }}));
  await page.goto(`${origin}/fixtures/s4/analysis?state=failed`);
  await result.getByText('이 요청의 보고서는 이후 분석으로 대체되었습니다. 최신 결과는 세션 분석 화면에서 확인하세요.', {exact: true}).waitFor();
  assert.equal(await result.getByRole('heading', {name: /채택된 보고서/}).count(), 0);

  await page.goto(`${origin}/fixtures/s4/analysis?state=running`);
  await result.getByRole('heading', {name: '최신 실행: 분석 진행 중'}).waitFor();
  await page.goto(`${origin}/chat`);
  const departed = polls;
  await page.waitForTimeout(1300);
  assert.equal(polls, departed, 'leaving the screen stops status polling');
  assert.equal(cancellations, 1, 'leaving is not an explicit cancellation');
  await page.goto(`${origin}/fixtures/s4/analysis?state=running`);
  await result.getByRole('heading', {name: '최신 실행: 분석 진행 중'}).waitFor();
  await result.evaluate(el => el.remove());
  const removed = polls;
  await page.waitForTimeout(1300);
  assert.equal(polls, removed, 'removing the modal stops polling');
  assert.equal(cancellations, 1, 'removing the modal keeps the reserved run');
  return {checks: ['same request replay, active status polling, explicit cancellation, preservation and stopped polling'], pageErrors: []};
}
