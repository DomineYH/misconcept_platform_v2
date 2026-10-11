import assert from 'node:assert/strict';
import {comparisonFixture, openComparison, hidden} from './s5_browser_fixtures.mjs';

export default async function checkReviewFixes(page) {
  const fixture = await comparisonFixture(page);
  await openComparison(page);
  await page.getByRole('button', {name:'비교 실행',exact:true}).click();
  const snapshot = fixture.current();
  const payload = '<img src=x onerror="window.s5xss=1">';
  snapshot.canonical_context = [{role:'teacher',content:payload}, {role:'student',content:'고정 학생 이력'}];
  snapshot.current_message = `고정 현재 질문 ${payload}`;
  snapshot.panels[0].applied_options = {max_output_tokens:1234};
  await page.getByRole('button', {name:'상태 새로고침',exact:true}).click();
  await page.getByText('적용 옵션: {"max_output_tokens":1234}', {exact:true}).waitFor();
  await page.getByRole('button', {name:'비교 이력',exact:true}).click();
  await page.getByRole('button', {name:'과거 비교 상세',exact:true}).click();
  const detail = page.getByRole('region', {name:'비교 이력 상세',exact:true});
  await detail.getByText(`현재 질문: 고정 현재 질문 ${payload}`, {exact:true}).waitFor();
  assert.match(await detail.innerText(), /공통 입력 hash: same-context-hash/);
  assert((await detail.innerText()).includes(`교사: ${payload}`));
  assert((await detail.innerText()).includes('학생: 고정 학생 이력'));
  assert.match(await detail.innerText(), /요청 모델/);
  assert((await detail.innerText()).includes('적용 옵션: {"max_output_tokens":1234}'));
  assert.equal(await detail.locator('img').count(),0);
  assert.equal(await page.evaluate(() => window.s5xss),undefined);
  const list = page.getByRole('region', {name:'비교 이력 목록',exact:true});
  for (const [origin,label] of [['new_draft','새 초안'],['saved_scenario_edit','저장 시나리오의 미저장 수정']]) {
    await page.route('**/admin/comparisons', route => route.fulfill({json:{items:[{comparison_run_id:'comparison-1',title:'과거 비교',target_role:'student',source_origin:origin,source_kind:'unsaved_draft',created_at:'2026-10-11',status:'finished'}],next_cursor:null}}));
    await page.getByRole('button', {name:'비교 이력',exact:true}).click();
    await list.getByText(new RegExp(label)).waitFor();
    assert(!(await list.innerText()).includes('unsaved_draft'));
  }
  const failures = [
    ['invalid_output','응답 검증에 실패'], ['provider_error','제공자 호출에 실패'],
    ['rate_limited','제공자 요청 한도'], ['refused','제공자가 응답을 거절'],
    ['timeout_total','응답 제한 시간'], ['output_limit','출력 한도'],
    ['stopped','관리자가 취소'], ['interrupted','실행이 중단'],
    ['configuration_unavailable','권한 또는 AI 연결'], ['storage_error','결과 저장에 실패'],
  ];
  const panel = page.getByRole('article', {name:'패널 2',exact:true});
  for (const [code,message] of failures) {
    snapshot.panels[1].error_code = code;
    await page.getByRole('button', {name:'상태 새로고침',exact:true}).click();
    await panel.getByText(new RegExp(message)).waitFor();
    assert((await panel.innerText()).includes('검증된 부분 결과 없음'));
    assert(!(await panel.innerText()).includes(hidden));
  }
  snapshot.target_role = 'analysis';
  snapshot.panels[1].status = 'degraded';
  snapshot.panels[1].error_code = 'invalid_reference';
  await page.getByRole('button', {name:'상태 새로고침',exact:true}).click();
  await panel.getByText(/응답 검증에 실패.*검증된 부분 결과 있음/).waitFor();
  return {pageErrors:[]};
}
