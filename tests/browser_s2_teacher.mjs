import assert from 'node:assert/strict';

export default async function checkTeacherHelpModes(page) {
  const base = new URL(page.url()).origin;
  const calls = [];
  let reply = {status:'completed', result_kind:'message', message:{content:'저장된 코칭 <img src=x onerror="window.xss=1">'}};
  await page.route('**/sessions/1/turns/turn-1/mentor/stream', async route => {
    calls.push(route.request().postDataJSON());
    await route.fulfill({json:reply});
  });
  await page.goto(`${base}/fixtures/s2/lesson?mode=off`);
  await page.getByRole('heading', {name:'분수의 크기 비교', exact:true}).waitFor({timeout:3000});
  assert.equal(await page.getByRole('button', {name:'멘토 도움 요청', exact:true}).count(), 0);
  assert.equal(await page.getByText('함께 질문을 살펴봅시다.', {exact:true}).count(), 0);
  assert.equal(calls.length, 0);
  await page.goto(`${base}/fixtures/s2/lesson?mode=manual`);
  assert.equal(calls.length, 0, 'manual never auto-requests');
  await page.getByRole('button', {name:'멘토 도움 요청', exact:true}).focus();
  await page.keyboard.press('Enter');
  await page.getByText('멘토 코칭 완료', {exact:true}).waitFor();
  assert.equal(calls[0].trigger, 'manual');
  assert(await page.getByRole('button', {name:'멘토 도움 요청', exact:true}).isDisabled(), 'one result per completed turn');
  assert.equal(await page.locator('#mentor-result img').count(), 0, 'coaching is safe text');
  assert((await page.locator('#mentor-result').innerText()).includes('<img'));
  assert.equal(await page.evaluate(() => window.xss), undefined);
  reply = {status:'completed', result_kind:'no_intervention', message:null};
  await page.goto(`${base}/fixtures/s2/lesson?mode=auto`);
  await page.getByText('멘토 미개입', {exact:true}).waitFor();
  assert.equal(calls.at(-1).trigger, 'auto');
  assert(await page.getByRole('button', {name:'멘토 도움 요청', exact:true}).isEnabled(), 'no intervention does not block manual help');
  await page.getByRole('button', {name:'멘토 도움 요청', exact:true}).click();
  assert.equal(calls.at(-1).trigger, 'manual');
  for (const [state, message] of [['limit','최근 완료 턴의 개입 상한에 도달했습니다.'], ['rate_limited','동시 호출 한도에 도달했습니다. 잠시 후 다시 요청하세요.'], ['failed','멘토 코칭 생성에 실패했습니다.'], ['running','멘토 처리 중 — 학생 대화는 계속할 수 있습니다.']]) {
    await page.goto(`${base}/fixtures/s2/lesson?mode=auto&help=${state}`);
    await page.getByText(message, {exact:true}).waitFor();
    assert(await page.getByLabel('교사 메시지').isEnabled(), 'mentor never blocks student composer');
    if (['rate_limited', 'failed'].includes(state)) {
      const before = calls.length;
      await page.getByRole('button', {name:'멘토 다시 요청', exact:true}).click();
      await page.getByText('멘토 미개입', {exact:true}).waitFor();
      assert.equal(calls.length, before + 1, 'explicit retry only');
      assert.equal(calls.at(-1).trigger, 'manual');
      assert.notEqual(calls.at(-1).request_id, calls.at(-2).request_id);
    } else assert(await page.getByRole('button', {name:'멘토 도움 요청', exact:true}).isDisabled());
  }
  await page.goto(`${base}/fixtures/s2/lesson?mode=manual&classification=off`);
  assert(await page.getByText('분류 미사용', {exact:true}).isVisible());
  for (const label of ['총평', '강점', '개선점', '대안 질문']) assert(await page.getByRole('heading', {name:label, exact:true}).isVisible());
  assert.equal(await page.locator('#classification-distribution').count(), 0, 'no false empty classification distribution');
  await page.goto(`${base}/fixtures/s2/lesson?mode=auto&legacy=1`);
  assert(await page.getByText('과거 자료 · 재구성된 수업 설정 · 읽기 전용', {exact:true}).isVisible());
  assert((await page.locator('#snapshot-provenance').innerText()).includes('당시 설정을 확인할 수 없음'));
  assert(await page.getByLabel('교사 메시지').isDisabled());
  assert.equal(await page.getByRole('button', {name:/멘토.*요청/}).count(), 0);
  const html = await page.content();
  for (const sentinel of ['INTERNAL_STUDENT_SENTINEL', 'INTERNAL_MENTOR_SENTINEL', 'INTERNAL_RUBRIC_SENTINEL', 'resolved_model_config', 'conversion_provenance']) assert(!html.includes(sentinel), sentinel);
  return {pageErrors:[]};
}
