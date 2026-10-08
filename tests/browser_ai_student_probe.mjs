import assert from 'node:assert/strict';
import {state} from './browser_ai_connections.mjs';

export default async function checkStudentProbe(page) {
  page.setDefaultTimeout(8000);
  const base = new URL(page.url()).origin;
  const data = {...state(), probes_available:true, probe_roles:['student']};
  const starts=[];
  let polls=0;
  await page.route('**/admin/ai/state',route=>route.fulfill({json:data}));
  await page.route('**/admin/ai/models/1/probes',async route=>{
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
  await page.goto(`${base}/admin/ai`);
  const model=page.locator('[data-model="1"]');
  await model.getByRole('button',{name:'학생봇 시험',exact:true}).waitFor();
  assert(await model.getByRole('button',{name:'멘토 시험',exact:true}).isDisabled(),'A7 mentor probe unavailable');
  assert(await model.getByRole('button',{name:'사후 분석 시험',exact:true}).isDisabled(),'A7 analysis probe unavailable');
  await model.getByRole('button',{name:'학생봇 시험',exact:true}).click();
  assert.equal(starts.length,0);
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
  return {pageErrors:[]};
}
