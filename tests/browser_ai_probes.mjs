import assert from 'node:assert/strict';
import {state} from './browser_ai_connections.mjs';

export default async function checkAIProbes(page) {
  page.setDefaultTimeout(8000);
  const base = new URL(page.url()).origin;
  const data = state(), starts = [], cancels = [];
  let polls = 0;
  await page.route('**/admin/ai/state', route => route.fulfill({json:data}));
  await page.route('**/admin/ai/models/1/probes', async route => {
    const body = route.request().postDataJSON();
    starts.push(body);
    data.models[0].verification_state[body.role] = {status:'verifying', probe_request_id:body.request_id};
    await route.fulfill({status:202, json:{request_id:body.request_id, status:'verifying'}});
  });
  await page.route('**/admin/ai/probes/*', async route => {
    polls++;
    const request_id = new URL(route.request().url()).pathname.split('/').at(-1);
    const start = starts.find(start => start.request_id === request_id);
    data.models[0].verification_state[start.role] = {status:'succeeded', verified_at:'2026-10-08'};
    await route.fulfill({json:{request_id, status:'succeeded'}});
  });
  await page.route('**/admin/ai/probes/*/cancel', async route => {
    cancels.push(route.request().postDataJSON());
    data.models[0].verification_state.mentor = {status:'failed', error_code:'interrupted'};
    await route.fulfill({json:{status:'cancel_requested'}});
  });
  await page.goto(`${base}/admin/ai`);
  const model = page.locator('[data-model="1"]');
  await model.getByRole('button', {name:'학생봇 시험', exact:true}).focus();
  await page.keyboard.press('Enter');
  const disclosure = await page.locator('#ai-editor').innerText();
  for (const text of ['학생봇', '최대 2회', '1024', '비용', '자동 재시도 0회']) assert(disclosure.includes(text), `trial disclosure: ${text}`);
  assert.equal(starts.length, 0, 'opening trial does not generate');
  await page.getByRole('button', {name:'시험 시작', exact:true}).click();
  await model.getByRole('button', {name:'학생봇 진행 조회', exact:true}).waitFor();
  assert.equal(starts.length, 1);
  assert.equal(starts[0].role, 'student');
  assert.equal(starts[0].expected_version, 2);
  assert.match(starts[0].request_id, /^[0-9a-f-]{36}$/);
  await page.reload();
  await model.getByRole('button', {name:'학생봇 진행 조회', exact:true}).click();
  await page.getByRole('status').filter({hasText:'성공'}).waitFor();
  assert.equal(starts.length, 1, 'reconnect queries existing probe without regeneration');
  assert.equal(polls, 1);
  await model.getByRole('button', {name:'멘토 시험', exact:true}).click();
  const mentor = await page.locator('#ai-editor').innerText();
  for (const text of ['개입 판단 JSON과 코칭 텍스트', '1500', '최대 2회', '자동 재시도 0회', '교육적 품질 보증이 아닙니다', '첫 단계 실패']) assert(mentor.includes(text), `mentor disclosure: ${text}`);
  await page.getByRole('button', {name:'시험 시작', exact:true}).click();
  await model.getByRole('button', {name:'멘토 시험 취소', exact:true}).click();
  await page.getByRole('status').filter({hasText:'중단을 요청했습니다'}).waitFor();
  assert.equal(cancels.length, 1);
  assert.notEqual(starts[0].request_id, starts[1].request_id, 'explicit trials have separate identity');
  await model.getByRole('button', {name:'사후 분석 시험', exact:true}).click();
  const analysis = await page.locator('#ai-editor').innerText();
  for (const text of ['분류 JSON과 종합 결과 JSON', '2048', '최대 2회', '자동 재시도 0회', '교육적 품질 보증이 아닙니다', '첫 단계 실패']) assert(analysis.includes(text), `analysis disclosure: ${text}`);
  await page.getByRole('button', {name:'시험 시작', exact:true}).click();
  assert.equal(starts[2].role, 'analysis');
  await model.getByRole('button', {name:'사후 분석 진행 조회', exact:true}).click();
  await page.getByRole('status').filter({hasText:'성공'}).waitFor();
  assert.equal(polls, 2);
  data.models[0].verification_state.analysis = {status:'failed', error_code:'invalid_reference'};
  await page.reload();
  await model.getByText('레이블·발화 참조 또는 인용이 유효하지 않습니다.', {exact:false}).waitFor();
  for (const [code, guidance] of Object.entries({
    transient:'제공자에 일시적인 문제가 발생했습니다. 잠시 후 다시 시험하세요.',
    timeout_connect:'제공자 연결 시간이 초과되었습니다. 연결 상태를 확인하세요.',
    timeout_first_output:'첫 응답 대기 시간이 초과되었습니다. 시간 제한을 확인하세요.',
    timeout_total:'전체 호출 시간이 초과되었습니다. 시간 제한을 확인하세요.',
    call_limit_reached:'동시 호출 한도에 도달했습니다. 진행 중인 호출이 끝난 후 다시 시험하세요.'
  })) {
    data.models[0].verification_state.analysis = {status:'failed', error_code:code};
    await page.reload();
    const row = model.locator('.ai-actions').filter({hasText:'사후 분석: 실패'});
    await row.getByText(guidance, {exact:true}).waitFor();
    assert(!(await row.innerText()).includes('응답 형식'), `${code} is not a format failure`);
  }
  return {pageErrors:[]};
}
