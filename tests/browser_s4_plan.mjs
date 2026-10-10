import assert from 'node:assert/strict';

export default async function checkChunkConfirmation(page) {
  const origin = new URL(page.url()).origin;
  const failed = await (await page.request.get(`${origin}/fixtures/s4/result?state=preserved&admin=1`)).json();
  const requests = [];
  await page.context().addCookies([{name: 'csrftoken', value: 'screen-csrf', url: origin}]);
  await page.route('**/admin/sessions/1/analyze_regenerate', async route => {
    requests.push({body: route.request().postDataJSON(), csrf: route.request().headers()['x-csrf-token']});
    await route.fulfill({json: failed});
  });
  for (const width of [1280, 390]) {
    await page.setViewportSize({width, height: 800});
    await page.goto(`${origin}/fixtures/s4/analysis?state=plan&admin=1`);
    const result = page.locator('[data-analysis-result]');
    await result.getByRole('heading', {name: '분할 실행 전 확인'}).waitFor();
    const before = requests.length;
    assert(await result.getByText('대상 범위: 메시지 101, 102, 103, 104, 105, 106, 107 · 7개', {exact: true}).isVisible());
    assert(await result.getByText('2개 분할 + 종합 · 예상 생성 호출 3회', {exact: true}).isVisible());
    assert(await result.getByText('추정 입력 4200 · 추정 출력 2600 토큰', {exact: true}).isVisible());
    assert(await result.getByText(/추정치이며 실제 사용량/).isVisible());
    assert(await result.getByText('일시 장애 재시도 상한: 호출당 2회 · 최대 9회 시도', {exact: true}).isVisible());
    assert(await result.getByText(/기존 정상 결과는 새 정상 결과가/).isVisible());
    assert.equal(requests.length, before, 'opening the plan cannot execute analysis');
    await result.getByRole('button', {name: '분할 실행 취소', exact: true}).click();
    assert.equal(requests.length, before, 'declining the plan cannot execute analysis');
    assert(await result.getByRole('heading', {name: '채택된 보고서: 정상 분석'}).isVisible());
    assert.match(await result.getByRole('status').innerText(), /실행하지 않았습니다/);
    await result.getByRole('button', {name: '분할 계획 다시 보기', exact: true}).click();
    const confirm = result.getByRole('button', {name: '확인하고 분할 실행', exact: true});
    await confirm.focus();
    await page.keyboard.press('Enter');
    await result.getByRole('heading', {name: '최신 실행: 분석 실패'}).waitFor();
    assert.equal(requests.length, before + 1);
    assert.equal(requests.at(-1).body.plan_hash, 'plan-v1');
    assert.match(requests.at(-1).body.request_id, /^[0-9a-f-]{36}$/);
    assert.equal(requests.at(-1).csrf, 'screen-csrf');
    assert(await result.getByRole('status').evaluate(el => el === document.activeElement));
    const previousId = requests.at(-1).body.request_id;
    await result.getByRole('button', {name: '분석 재시도', exact: true}).click();
    await page.waitForFunction(() => document.querySelector('[data-analysis-result] [role="status"]').textContent === '분석 요청 결과를 확인하세요.');
    assert.equal(requests.length, before + 2);
    assert.notEqual(requests.at(-1).body.request_id, previousId, 'explicit retry is a new run');
    assert.equal(requests.at(-1).body.plan_hash, undefined);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  }
  await page.goto(`${origin}/fixtures/s4/analysis?state=blocked`);
  const result = page.locator('[data-analysis-result]');
  await result.getByRole('heading', {name: '분석 계획 불가'}).waitFor();
  assert(await result.getByText('한 입력 단위가 분석 한도를 초과합니다. 대화를 줄여서 실행하지 않습니다.', {exact: true}).isVisible());
  assert.equal(await result.getByRole('button', {name: '확인하고 분할 실행'}).count(), 0);
  const changedPlan = await (await page.request.get(`${origin}/fixtures/s4/result?state=plan&admin=1`)).json();
  changedPlan.plan.plan_hash = 'plan-v2';
  let conflicts = 0;
  await page.route('**/admin/sessions/1/analyze_regenerate', async route => {
    conflicts++;
    assert.equal(route.request().postDataJSON().plan_hash, conflicts === 1 ? 'plan-v1' : 'plan-v2');
    await route.fulfill({status: conflicts === 1 ? 409 : 200, json: conflicts === 1 ? changedPlan : failed});
  });
  await page.goto(`${origin}/fixtures/s4/analysis?state=plan&admin=1`);
  await result.getByRole('button', {name: '확인하고 분할 실행', exact: true}).click();
  await result.getByRole('status').filter({hasText: /계획이 변경되었습니다/}).waitFor();
  assert.equal(conflicts, 1, 'plan conflicts require another explicit confirmation');
  assert(await result.getByRole('heading', {name: '채택된 보고서: 정상 분석'}).isVisible());
  await result.getByRole('button', {name: '확인하고 분할 실행', exact: true}).click();
  await result.getByRole('heading', {name: '최신 실행: 분석 실패'}).waitFor();
  assert.equal(conflicts, 2);
  await page.route('**/admin/sessions/1/analyze_regenerate', route => route.fulfill({
    status: 409, json: {detail: 'PRIVATE_SDK_ERROR', config: 'PRIVATE_CONFIG'},
  }));
  await result.getByRole('button', {name: '분석 재시도', exact: true}).click();
  await result.getByRole('button', {name: '같은 요청 다시 전송', exact: true}).waitFor();
  assert(await result.getByRole('heading', {name: '채택된 보고서: 정상 분석'}).isVisible());
  assert(!(await result.innerText()).includes('PRIVATE_'), 'unexpected error envelopes never replace the report or expose raw errors');
  for (const state of ['chunked', 'partial']) {
    const running = await (await page.request.get(`${origin}/fixtures/s4/result?state=running`)).json();
    const terminal = await (await page.request.get(`${origin}/fixtures/s4/result?state=${state}`)).json();
    if (state === 'partial') {
      terminal.plan = changedPlan.plan;
      terminal.accepted_report.brief_feedback = ['부분 분석: 검증된 6 / 7개 메시지의 결과입니다.'];
    }
    let confirms = 0, polls = 0;
    await page.route('**/admin/sessions/1/analyze_regenerate', route => {
      confirms++;
      assert.equal(route.request().postDataJSON().plan_hash, 'plan-v1');
      return route.fulfill({status: 202, json: running});
    });
    await page.route('**/sessions/1/analysis/runs/run-1', route => {
      polls++;
      return route.fulfill({json: terminal});
    });
    await page.goto(`${origin}/fixtures/s4/analysis?state=plan&admin=1`);
    await result.getByRole('button', {name: '확인하고 분할 실행', exact: true}).click();
    await result.getByRole('heading', {name: '최신 실행: 분석 진행 중'}).waitFor();
    assert.equal(await result.getByRole('button', {name: '확인하고 분할 실행', exact: true}).count(), 0);
    await result.getByRole('heading', {name: `최신 실행: ${state === 'partial' ? '부분 분석' : '정상 분석'}`}).waitFor();
    assert.equal(confirms, 1);
    assert.equal(polls, 1);
    assert(await result.getByText(/분할 정상 분석 · 메시지/).first().isVisible());
    if (state === 'partial') {
      assert(await result.getByText('부분 분석: 검증된 6 / 7개 메시지의 결과입니다.', {exact: true}).isVisible());
      assert(await result.getByText('분할 분석 실패 · 메시지 107', {exact: true}).isVisible());
      assert(await result.getByRole('button', {name: '확인하고 분할 실행', exact: true}).isVisible());
    }
    await page.waitForTimeout(1300);
    assert.equal(polls, 1, 'finished chunk execution stops polling');
    assert.equal(confirms, 1, 'a partial result never starts another execution automatically');
  }
  return {checks: ['chunk scope/estimates, zero calls before confirmation, cancel/retry/preservation and CSRF'], pageErrors: []};
}
