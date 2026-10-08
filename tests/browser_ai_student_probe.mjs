import assert from 'node:assert/strict';
import {state} from './browser_ai_connections.mjs';

export default async function checkStudentProbe(page) {
  page.setDefaultTimeout(8000);
  const base = new URL(page.url()).origin;
  const data = state();
  const starts=[], cancels=[];
  await page.context().addCookies([{name:'csrftoken', value:'ai-probe-csrf', url:base}]);
  let polls=0;
  await page.route('**/admin/ai/state',route=>route.fulfill({json:data}));
  await page.route('**/admin/ai/models/1/probes',async route=>{
    assert.equal(route.request().headers()['x-csrf-token'], 'ai-probe-csrf');
    const body=route.request().postDataJSON();
    starts.push(body);
    if (starts.length===1) return route.fulfill({status:429,json:{detail:'PRIVATE-ERROR'}});
    data.models[0].verification_state.student={status:'verifying',probe_request_id:body.request_id};
    return route.fulfill({status:202,json:{request_id:body.request_id,status:'verifying'}});
  });
  await page.route('**/admin/ai/probes/*',async route=>{
    polls++;
    const request_id=new URL(route.request().url()).pathname.split('/').at(-1);
    data.models[0].verification_state.student={status:'succeeded',probe_request_id:request_id};
    return route.fulfill({json:{request_id,status:'succeeded'}});
  });
  await page.route('**/admin/ai/probes/*/cancel',async route=>{
    assert.equal(route.request().headers()['x-csrf-token'], 'ai-probe-csrf');
    assert.deepEqual(route.request().postDataJSON(), {});
    cancels.push(new URL(route.request().url()).pathname.split('/').at(-2));
    data.models[0].verification_state.student={status:'failed',error_code:'interrupted'};
    return route.fulfill({json:{status:'cancel_requested'}});
  });
  await page.goto(`${base}/admin/ai`);
  const model=page.locator('[data-model="1"]');
  await model.getByRole('button',{name:'학생봇 시험',exact:true}).waitFor();
  assert(await model.getByRole('button',{name:'멘토 시험',exact:true}).isEnabled(),'mentor probe available');
  assert(await model.getByRole('button',{name:'사후 분석 시험',exact:true}).isEnabled(),'analysis probe available');
  await model.getByRole('button',{name:'학생봇 시험',exact:true}).click();
  assert.equal(starts.length,0);
  assert((await page.locator('#ai-editor').innerText()).includes('관리자당 시험은 한 묶음'));
  assert((await page.locator('#ai-editor').innerText()).includes('대기하지 않으므로'));
  await page.getByRole('button',{name:'시험 시작',exact:true}).click();
  await page.getByRole('alert').filter({hasText:'한도'}).waitFor();
  assert(!(await page.content()).includes('PRIVATE-ERROR'));
  await page.getByRole('button',{name:'시험 시작',exact:true}).click();
  await model.getByRole('button',{name:'학생봇 진행 조회',exact:true}).waitFor();
  assert.equal(starts[0].request_id,starts[1].request_id,'retry after pre-admission rejection keeps identity');
  await page.reload();
  await model.getByRole('button',{name:'학생봇 진행 조회',exact:true}).click();
  await page.getByRole('status').filter({hasText:'성공'}).waitFor();
  assert.equal(starts.length,2,'reconnection only reads existing state');
  assert.equal(polls,1);
  await model.getByRole('button',{name:'학생봇 시험',exact:true}).click();
  await page.getByRole('button',{name:'시험 시작',exact:true}).click();
  await model.getByRole('button',{name:'학생봇 시험 취소',exact:true}).click();
  await page.getByRole('status').filter({hasText:'중단을 요청했습니다'}).waitFor();
  assert.equal(starts.length,3);
  assert((await page.getByRole('status').innerText()).includes('제공자 처리 및 이미 발생한 비용은 취소되지 않을 수 있습니다.'));
  assert.deepEqual(cancels,[starts[2].request_id]);
  assert.notEqual(starts[2].request_id,starts[1].request_id,'explicit retest gets a new identity');
  return {pageErrors:[]};
}
