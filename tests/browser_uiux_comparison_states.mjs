import assert from 'node:assert/strict';

export default async function check(page) {
  page.setDefaultTimeout(5000);
  const base = new URL(page.url()).origin;
  const posts = [];
  let snapshot, retryRoute;
  await page.route('**/admin/comparison-conversations', route => route.fulfill({json:{items:[],next_cursor:null}}));
  await page.route('**/admin/comparison-conversations/visual-only**', async route => {
    const request = route.request();
    if (request.method() === 'GET') return route.fulfill({json:snapshot});
    const body = request.postDataJSON();
    posts.push({url:request.url(),body});
    if (request.url().endsWith('/retry')) { retryRoute = route; return; }
    if (request.url().endsWith('/stop')) {
      snapshot.children[0].panels[1].status = 'cancelled';
      snapshot.phase = 'awaiting_adoption';
    }
    if (request.url().endsWith('/adopt')) {
      snapshot.version++;
      snapshot.pairs = [{turn_id:'turn-1',teacher:'공통 질문',student:snapshot.children[0].panels[0].text}];
      snapshot.pending_student_child_id = null;
      snapshot.pending_auto = true;
      snapshot.phase = 'open';
      snapshot.allowed_actions.adopt = false;
    }
    if (request.url().endsWith('/mentor')) snapshot.pending_auto = false;
    return route.fulfill({json:snapshot});
  });
  await page.goto(`${base}/fixtures/uiux/comparison?panels=2`);
  snapshot = await page.locator('#comparison-conversation-data').evaluate(el => JSON.parse(el.textContent).snapshot);
  snapshot.children[0].panels.forEach((p,i) => { p.run_id = `run-${i+1}`; });
  await page.getByRole('button',{name:'상태 새로고침',exact:true}).click();
  const a = page.getByRole('article',{name:'패널 1',exact:true});
  const b = page.getByRole('article',{name:'패널 2',exact:true});
  await a.getByRole('button',{name:'학생 응답 채택',exact:true}).waitFor();
  assert(await a.getByRole('button',{name:'학생 응답 채택',exact:true}).isEnabled(), 'mixed completed/failed permits server-approved adoption');
  assert(await b.getByRole('button',{name:'학생 응답 채택',exact:true}).isDisabled());
  await b.getByRole('button',{name:'패널 재시도',exact:true}).click();
  await page.waitForFunction(() => document.querySelector('#conversation-question').disabled);
  assert(await a.getByRole('button',{name:'학생 응답 채택',exact:true}).isDisabled(), 'adoption locked while retry request is pending');
  assert.equal(posts[0].body.expected_version,1);
  assert.equal(posts[0].body.generation_id,'run-2');
  snapshot.children[0].panels[1].status = 'running';
  snapshot.children[0].panels[1].run_id = 'retry-2';
  snapshot.phase = 'generating';
  // Even a stale allowed_actions projection must not bypass an active panel lock.
  snapshot.allowed_actions.adopt = true;
  await retryRoute.fulfill({json:snapshot});
  await b.getByRole('button',{name:'패널 중지',exact:true}).waitFor();
  await page.getByText('다른 패널 실행 중 · 완료 응답도 채택할 수 없습니다.',{exact:true}).waitFor();
  assert(await a.getByRole('button',{name:'학생 응답 채택',exact:true}).isDisabled());
  assert(await b.getByRole('button',{name:'패널 중지',exact:true}).isEnabled(), 'active stop survives global action lock');
  assert.match(await b.innerText(), /잠정 응답 · 미완료 · 채택 불가/);
  await b.getByRole('button',{name:'패널 중지',exact:true}).click();
  await a.getByRole('button',{name:'학생 응답 채택',exact:true}).click();
  await page.getByText('요청 결과를 저장했습니다.',{exact:true}).waitFor();
  assert.equal(posts[1].body.generations[0].generation_id,'retry-2');
  assert.equal(posts[2].body.generation_id,'run-1');
  assert.equal(posts[3].body.expected_version,2, 'automatic mentor uses the adopted version');
  assert.equal(posts[3].body.target_turn_id,'turn-1');
  assert.equal(posts[3].body.trigger,'auto');
  assert.equal(await page.locator('#conversation-dialogue [data-pair]').count(),1);
  return {pageErrors:[]};
}
