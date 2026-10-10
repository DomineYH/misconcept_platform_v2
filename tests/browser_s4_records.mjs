import assert from 'node:assert/strict';

export default async function checkSavedRecords(page) {
  const origin = new URL(page.url()).origin;
  const states = ['ok', 'chunked', 'partial', 'off', 'failed', 'legacy', 'summary_only'];
  for (const width of [1280, 390]) {
    await page.setViewportSize({width, height: 900});
    for (const state of states) {
      const modal = await (await page.request.get(`${origin}/fixtures/s4/modal?state=${state}&admin=1`)).text();
      await page.route('**/admin/sessions/1/analysis_modal', route => route.fulfill({body: modal, contentType: 'text/html'}));
      for (const surface of ['analysis', 'history']) {
        await page.goto(`${origin}/fixtures/s4/${surface}?state=${state}${surface === 'history' ? '&admin=1' : ''}`);
        if (surface === 'history') await page.locator('.view-analysis-btn').click();
        const result = page.locator('[data-analysis-result]');
        await result.getByRole('status').waitFor({state: 'attached'});
        if (state === 'failed') {
          assert.equal(await result.getByRole('heading', {name: /채택된 보고서/}).count(), 0);
          assert.equal(await result.getByText('질문 유형 분포', {exact: true}).count(), 0);
        } else if (['legacy', 'summary_only'].includes(state)) {
          assert(await result.getByText(/원문 근거·분석 범위: 알 수 없음/).isVisible());
          assert(await result.getByText('과거 분류 사용 여부: 알 수 없음', {exact: true}).isVisible());
          assert(await result.getByText('Original label: 1개 · 100%', {exact: true}).isVisible());
          assert.equal(await result.getByRole('link', {name: /원문/}).count(), 0);
          await result.locator('details > summary').click();
          assert(await result.getByText('분류: Original label · 평가: 우수', {exact: true}).isVisible());
          assert(await result.getByText('판정 이유: Original reasoning', {exact: true}).isVisible());
          assert(await result.getByText('시각: 2026-10-01T10:00:00', {exact: true}).isVisible());
          assert(await result.getByText('분류: 알 수 없음 · 평가: 알 수 없음', {exact: true}).first().isVisible());
          if (state === 'legacy') {
            assert(await result.getByText('원문 위치 알 수 없음: 과거 학생 인용', {exact: true}).isVisible());
            assert(await result.getByText('과거 개선 이유', {exact: true}).isVisible());
          }
        } else {
          await result.getByRole('link', {name: '원문 103: 분모가 5라서 더 커요.'}).click();
          assert(await result.locator('[data-message-id="103"]').evaluate(el => el === document.activeElement));
          if (state === 'chunked') assert(await result.getByText('분할 정상 분석 · 메시지 101, 102, 103', {exact: true}).isVisible());
        }
        assert(await result.evaluate(el => el.scrollWidth <= el.clientWidth && el.getBoundingClientRect().right <= innerWidth), `${width} ${state} ${surface}: shared result fits viewport`);
        if (surface === 'analysis') assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        if (surface === 'history') await page.keyboard.press('Escape');
      }
    }
  }
  const old = await (await page.request.get(`${origin}/fixtures/s4/result?state=legacy`)).json();
  old.accepted_report.improvements[0].student_message_id = 103;
  old.accepted_report.improvements[0].student_quote = '분모가 5라서 더 커요.';
  old.questions[0].label = old.questions[0].label_name = 'Unclassified';
  old.messages[1].label = 'Unclassified';
  old.messages.push({id: 108, role: 'tutor', content: 'Late mentor', turn_index: 1});
  await page.route('**/fixtures/s4/result?state=legacy', route => route.fulfill({json: old}));
  await page.goto(`${origin}/fixtures/s4/analysis?state=legacy`);
  const result = page.locator('[data-analysis-result]');
  await result.getByRole('link', {name: '원문 103: 분모가 5라서 더 커요.'}).click();
  assert(await result.locator('[data-message-id="103"]').evaluate(el => el === document.activeElement));
  const mentor = result.locator('[data-message-id="108"]');
  assert(await mentor.getByText('메시지 108 · 멘토', {exact: true}).isVisible());
  assert(await mentor.getByText('1번째 턴', {exact: true}).isVisible());
  assert(await result.getByText('분류: Unclassified · 평가: 우수', {exact: true}).isVisible());

  await page.route('**/fixtures/s4/result?state=no_accepted', route => route.fulfill({status: 404, json: {detail: 'Analysis not found'}}));
  await page.goto(`${origin}/fixtures/s4/analysis?state=no_accepted`);
  await result.getByText('분석 결과를 불러올 수 없습니다.', {exact: true}).waitFor();
  assert.equal(await result.getByRole('heading', {name: /채택된 보고서/}).count(), 0);
  return {checks: ['saved v1/v2/chunked/partial/off/failed/absent results in desktop/mobile page and history modal, original grades/reasoning/time, known vs unknown legacy evidence'], pageErrors: []};
}
