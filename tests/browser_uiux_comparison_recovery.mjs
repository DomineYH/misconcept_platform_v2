import assert from 'node:assert/strict';

export default async function check(page) {
  page.setDefaultTimeout(5000);
  const base = new URL(page.url()).origin;
  async function refresh() {
    const button = await page.getByRole('button',{name:'상태 새로고침',exact:true}).elementHandle();
    await button.click();
    // Refresh replaces the controls only after the fetched snapshot is rendered.
    await button.waitForElementState('hidden');
  }
  let snapshot, mode = 'ok', posts = 0, reads = 0;
  await page.route('**/admin/comparison-conversations', route => route.fulfill({json:{items:[],next_cursor:null}}));
  await page.route('**/admin/comparison-conversations/visual-only**', route => {
    if (route.request().method() === 'POST') { posts++; return route.abort('failed'); }
    reads++;
    if (mode === 'missing') return route.fulfill({status:404,json:{detail:{code:'not_found'}}});
    return route.fulfill({json:snapshot});
  });
  await page.goto(`${base}/fixtures/uiux/comparison?panels=2`);
  snapshot = await page.locator('#comparison-conversation-data').evaluate(el => JSON.parse(el.textContent).snapshot);
  snapshot.children[0].panels.forEach((p,i) => { p.run_id = `run-${i}`; });
  const question = page.getByLabel('공통 교사 질문',{exact:true});
  await question.evaluate(el => { window.originalQuestion = el; el.value = '복원 입력과 선택'; el.setSelectionRange(2,4); });
  await refresh();
  assert.equal(await page.evaluate(() => document.activeElement.textContent), '상태 새로고침', 'refresh keeps keyboard focus');
  const a = page.getByRole('article',{name:'패널 1',exact:true});
  await a.getByRole('button',{name:'학생 응답 채택',exact:true}).click();
  await page.getByText('요청 결과를 확인할 수 없습니다. 입력은 유지됩니다. 상태 새로고침으로 저장된 결과를 확인하세요.',{exact:true}).waitFor();
  assert.equal(posts,1, 'uncertain mutation is never automatically resubmitted');
  assert(reads >= 2, 'uncertain mutation uses stored-state GET recovery');
  assert.equal(await question.inputValue(),'복원 입력과 선택');
  assert(await page.evaluate(() => originalQuestion === document.querySelector('#conversation-question')), 'input DOM identity preserved');
  assert.equal(await question.evaluate(el => el.selectionStart),2);
  mode = 'missing';
  await refresh();
  await page.getByText('비교 대화를 찾거나 열 수 없습니다. 입력은 유지됩니다. 저장된 시나리오 선택 또는 상태 새로고침을 사용하세요.',{exact:true}).waitFor();
  assert(await a.getByRole('button',{name:'학생 응답 채택',exact:true}).isDisabled());
  assert(await a.getByRole('button',{name:'이 모델 적용',exact:true}).isDisabled());
  assert.equal(await question.inputValue(),'복원 입력과 선택');
  mode = 'ok';
  snapshot.allowed_actions.adopt = false;
  await refresh();
  assert(await a.getByRole('button',{name:'학생 응답 채택',exact:true}).isDisabled(), 'server deny remains authoritative after recovery');
  snapshot.allowed_actions.adopt = true;
  await refresh();
  assert(await a.getByRole('button',{name:'학생 응답 채택',exact:true}).isEnabled());
  assert(!(await page.locator('#conversation-status').innerText()).includes('찾거나 열 수 없습니다'), 'successful refresh clears stale unavailability');
  for (const [phase,label] of [['open','대화 진행'],['generating','응답 생성 중'],['awaiting_adoption','학생 응답 채택 대기'],['retry_required','현재 패널 재시도 필요'],['analyzing','사후 분석 중'],['ended','비교 종료']]) {
    snapshot.phase = phase;
    await refresh();
    await page.getByText(`전체 상태: ${label} · 버전 1 · 만료 2026-11-01`,{exact:true}).waitFor();
    assert.equal(await a.getByText('완료',{exact:true}).count(),1, 'overall phase does not overwrite panel status');
  }
  snapshot.pending_student_child_id = null;
  await refresh();
  assert(await page.getByRole('article',{name:'패널 2',exact:true}).getByRole('button',{name:'패널 재시도',exact:true}).isDisabled(), 'old student round is not a current retry target');
  return {pageErrors:[]};
}
