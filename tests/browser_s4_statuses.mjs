import assert from 'node:assert/strict';

export default async function checkAnalysisStatuses(page) {
  const origin = new URL(page.url()).origin;
  for (const [state, report, run] of [
    ['ok', '정상 분석', '정상 분석'], ['partial', '부분 분석', '부분 분석'],
    ['failed', null, '분석 실패'], ['preserved', '정상 분석', '분석 실패'],
    ['preserved_partial', '정상 분석', '부분 분석'],
    ['cancelled', '정상 분석', '분석 취소됨'], ['interrupted', '정상 분석', '분석 중단됨'],
    ['legacy', '과거 분석', null], ['summary_only', '과거 분석', null],
    ['off', '정상 분석', '정상 분석'], ['no_dialogue', null, '대화 없음'],
    ['zero', '부분 분석', '부분 분석'],
  ]) {
    await page.goto(`${origin}/fixtures/s4/analysis?state=${state}`);
    const result = page.locator('[data-analysis-result]');
    await result.getByRole('status').waitFor({state: 'attached'});
    if (run) assert(await result.getByRole('heading', {name: `최신 실행: ${run}`}).isVisible(), state);
    if (report) assert(await result.getByRole('heading', {name: `채택된 보고서: ${report}`}).isVisible(), state);
    else assert.equal(await result.getByRole('heading', {name: /채택된 보고서/}).count(), 0);
    if (['preserved', 'preserved_partial', 'cancelled', 'interrupted'].includes(state)) {
      assert(await result.getByText('이전 정상 결과를 보존했습니다. 최신 실행의 결과와 구별해 확인하세요.', {exact: true}).isVisible());
      assert(await result.getByText('생성 시각: 2026-10-01 10:00', {exact: true}).isVisible());
    }
    if (['legacy', 'summary_only'].includes(state)) {
      assert(await result.getByText(/원문 근거·분석 범위: 알 수 없음/).isVisible());
      assert(await result.getByText('과거 자료 · 읽기 전용 · 재분석할 수 없습니다.', {exact: true}).isVisible());
      assert.equal(await result.getByRole('button', {name: /재시도|재생성/}).count(), 0);
      assert.equal(await result.getByRole('link', {name: /원문/}).count(), 0, 'historical evidence locations are not invented');
      if (state === 'legacy') assert(await result.getByText('원문 위치 알 수 없음: 과거 원문 인용', {exact: true}).isVisible());
    }
    if (state === 'off') {
      assert(await result.getByText('분류 미사용', {exact: true}).isVisible());
      assert.equal(await result.getByText(/분류 비율 분모|100%/).count(), 0);
      assert(await result.getByText('분수 조각의 크기에 관한 설명이 달라졌습니다.', {exact: true}).isVisible());
    }
    if (state === 'zero') assert(await result.getByText('비율 산정 불가 · 분류 분모 0개', {exact: true}).isVisible());
    if (state === 'no_dialogue') assert(await result.getByText('분석 가능 범위: 0개 메시지 · 호출 없이 안내합니다.', {exact: true}).isVisible());
    if (state === 'failed') assert.equal(await result.getByText('질문 유형 분포', {exact: true}).count(), 0);
  }
  return {checks: ['accepted report vs latest run, preserved/legacy/off/empty/zero states'], pageErrors: []};
}
