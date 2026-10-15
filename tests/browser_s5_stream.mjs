import assert from 'node:assert/strict';
import {comparisonFixture, openComparison, hidden} from './s5_browser_fixtures.mjs';

export default async function checkComparisonStreamAndMentor(page) {
  page.setDefaultTimeout(3000);
  const fixture = await comparisonFixture(page,['completed','completed','completed']);
  await openComparison(page);
  let starts = 0;
  await page.route('**/admin/comparisons', async route => {
    if (route.request().method() !== 'POST') return route.fallback();
    starts++;
    const frames = [];
    function emit(type,panel,seq,extra={}) {
      frames.push(`event: ${type}\r\ndata: ${JSON.stringify({comparison_run_id:'stream-1',panel_id:String(panel),run_id:`g${panel}`,seq,...extra})}\r\n\r\n`);
    }
    for (const i of [1,2,3]) emit('run.accepted',i,0,{context_hash:'shared-hash'});
    emit('output.delta',1,1,{text:'안전한 학생 본문'});
    emit('output.delta',1,1,{text:'중복은 표시 금지'});
    emit('output.delta',2,1,{text:'독립 패널'});
    emit('output.delta',2,2,{run_id:'OLD-GENERATION',text:'이전 시도는 표시 금지'});
    emit('reasoning.delta',1,2,{text:hidden});
    frames.push(': heartbeat\r\n\r\n');
    emit('output.completed',2,3,{text:'독립 패널 완료',outcome_status:'completed'});
    emit('output.completed',1,3,{text:'안전한 학생 본문 완료',outcome_status:'completed'});
    emit('run.failed',1,4,{raw_error:hidden});
    emit('run.interrupted',3,1,{raw_error:hidden});
    return route.fulfill({contentType:'text/event-stream',body:frames.join('')});
  });
  await page.getByRole('button',{name:'비교 실행',exact:true}).click();
  const p1 = page.getByRole('article',{name:'패널 1',exact:true});
  const p2 = page.getByRole('article',{name:'패널 2',exact:true});
  await p1.getByText('완료',{exact:true}).waitFor();
  assert(await p2.getByText('완료',{exact:true}).isVisible());
  assert.match(await p1.innerText(), /안전한 학생 본문 완료/);
  assert(!(await page.locator('#scenario-comparison').innerText()).includes(hidden));
  assert(!(await p1.innerText()).includes('중복은 표시 금지'));
  assert(!(await p2.innerText()).includes('이전 시도는 표시 금지'));
  assert.match(await p1.innerText(), /공통 입력 hash: shared-hash/);
  assert.match(await p1.innerText(), /시작 알 수 없음 · 종료 알 수 없음/);
  assert.match(await p1.innerText(), /첫 본문 알 수 없음/);
  assert.equal(starts,1);
  await page.unroute('**/admin/comparisons');
  // Restore the fixture after removing the one-shot stream override.
  const mentor = await comparisonFixture(page,['completed','completed','completed']);
  await page.getByRole('button',{name:'완료 쌍 추가',exact:true}).click();
  await page.getByLabel('이력 1 교사').fill('왜 그렇게 생각하나요?');
  await page.getByLabel('이력 1 학생').fill('분모가 커요.');
  await page.getByLabel('비교 대상').selectOption('mentor');
  await page.getByRole('button',{name:'4. 멘토',exact:true}).click();
  await page.getByLabel('멘토 사용 모드').selectOption('off');
  await page.getByRole('button',{name:'비교 닫기',exact:true}).click();
  await page.getByRole('button',{name:'모델 비교 열기',exact:true}).click();
  assert.match(await page.locator('#scenario-comparison').innerText(),/멘토 off/);
  await page.getByRole('button',{name:'비교 실행',exact:true}).click();
  assert.equal(mentor.requests.length,0,'off mentor must not submit');
  await page.getByLabel('멘토 사용 모드').selectOption('auto');
  await page.getByRole('button',{name:'비교 닫기',exact:true}).click();
  await page.getByRole('button',{name:'모델 비교 열기',exact:true}).click();
  assert.match(await page.locator('#scenario-comparison').innerText(),/개입 판단 시험: 수업의 시작 턴·간격·횟수 제한은 적용하지 않음/);
  await page.getByRole('button',{name:'비교 실행',exact:true}).click();
  await p1.getByText('멘토 미개입',{exact:true}).waitFor();
  assert(await p2.getByText('검증된 코칭',{exact:true}).isVisible());
  assert.match(await p2.innerText(),/첫 본문 N\/A/);
  assert(!(await page.locator('#scenario-comparison').innerText()).includes(hidden));
  assert.equal(mentor.requests[0].current_message,null);
  assert.equal(mentor.requests[0].canonical_context.length,2);
  assert.equal(fixture.requests.length,0);
  assert.deepEqual(mentor.unexpected,[]);
  assert.deepEqual(fixture.unexpected,[]);
  return {pageErrors:[]};
}
