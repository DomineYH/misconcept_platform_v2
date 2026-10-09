import assert from 'node:assert/strict';
import {state} from './browser_ai_connections.mjs';

export default async function checkAnthropic(page) {
  page.setDefaultTimeout(8000);
  const base = new URL(page.url()).origin, data = state(), edits = [], starts = [];
  Object.assign(data.providers[1], {connection_version:2, credential_revision:1, key_registered:true,
    enabled:true, status:'ready', catalog:{available:true, stale:false, fetched_at:'2026-10-09',
      models:[{model_id:'claude-sonnet-4-6', max_tokens:128000}]}});
  Object.assign(data.models[0], {provider:'anthropic', model_id:'claude-sonnet-4-6', enabled:false,
    default_options:{}, probe_budgets:{student:1024, mentor:1500, analysis:2500},
    verification_state:{student:{status:'unverified'}, mentor:{status:'unverified'}, analysis:{status:'unverified'}},
    capabilities:{fields:[
      {name:'max_output_tokens',label:'최대 출력 토큰',type:'integer',min:1,max:128000},
      {name:'temperature',label:'다양성',type:'number',min:0,max:1},
      {name:'thinking.type',label:'Claude thinking 방식',type:'string',choices:['disabled','adaptive','enabled']},
      {name:'thinking.budget_tokens',label:'Claude thinking 토큰 예산',type:'integer',min:1024,max:127999},
      {name:'output_config.effort',label:'Claude effort',type:'string',choices:['low','medium','high','max']}
    ]}});
  data.models[1].provider = 'anthropic';
  await page.route('**/admin/ai/state', route => route.fulfill({json:data}));
  await page.route('**/admin/ai/models/1/update', async route => {
    const body = route.request().postDataJSON();
    edits.push(body);
    Object.assign(data.models[0], {default_options:body.default_options, config_version:3,
      probe_budgets:{mentor:1500, analysis:2500}});
    await route.fulfill({json:{status:'saved'}});
  });
  await page.route('**/admin/ai/models/1/probes', async route => {
    const body = route.request().postDataJSON();
    starts.push(body);
    data.models[0].verification_state[body.role] = {status:'verifying', probe_request_id:body.request_id};
    await route.fulfill({status:202,json:{request_id:body.request_id,status:'verifying'}});
  });
  await page.route('**/admin/ai/probes/*', async route => {
    const request_id = new URL(route.request().url()).pathname.split('/').at(-1);
    const role = starts.find(item => item.request_id === request_id).role;
    data.models[0].verification_state[role] = {status:'failed', error_code:'refused'};
    await route.fulfill({json:{request_id,status:'failed',error_code:'refused'}});
  });
  await page.goto(`${base}/admin/ai`);
  const model = page.locator('[data-model="1"]');
  await model.getByRole('button',{name:'표시명·활성·옵션 수정'}).click();
  await page.getByLabel('최대 출력 토큰',{exact:true}).fill('4096');
  await page.getByLabel('다양성',{exact:true}).fill('1');
  await page.getByLabel('Claude thinking 방식',{exact:true}).selectOption('enabled');
  await page.getByLabel('Claude thinking 토큰 예산',{exact:true}).fill('1024');
  await page.getByLabel('Claude effort',{exact:true}).selectOption('low');
  await page.getByRole('button',{name:'모델 설정 저장',exact:true}).click();
  await page.getByRole('status').filter({hasText:'완료'}).waitFor();
  assert.deepEqual(edits[0].default_options, {max_output_tokens:4096,temperature:1,
    thinking:{type:'enabled',budget_tokens:1024},output_config:{effort:'low'}});
  assert(await model.getByRole('button',{name:'학생봇 시험',exact:true}).isDisabled());
  for (const [role,label,budget] of [['mentor','멘토',1500],['analysis','사후 분석',2500]]) {
    await model.getByRole('button',{name:`${label} 시험`,exact:true}).click();
    const disclosure = await page.locator('#ai-editor').innerText();
    assert(disclosure.includes(String(budget)) && disclosure.includes('최대 2회'));
    await page.getByRole('button',{name:'시험 시작',exact:true}).click();
    await model.getByRole('button',{name:`${label} 진행 조회`,exact:true}).click();
    await page.getByRole('status').filter({hasText:'실패'}).waitFor();
    assert.equal(starts.at(-1).role,role);
    assert.equal(starts.at(-1).expected_version,3);
  }
  assert.notEqual(starts[0].request_id,starts[1].request_id);
  assert(await page.locator('[data-model="2"]').getByRole('button',{name:'학생봇 시험',exact:true}).isDisabled());
  data.models[0].capabilities.metadata_conflict = true;
  await page.reload();
  assert(await model.getByRole('button',{name:'멘토 시험',exact:true}).isDisabled());
  for (const width of [1280,390]) {
    await page.setViewportSize({width,height:900});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  }
  return {pageErrors:[]};
}
