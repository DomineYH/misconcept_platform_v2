import assert from 'node:assert/strict';
import {state} from './browser_ai_connections.mjs';

export default async function checkGoogle(page) {
  page.setDefaultTimeout(8000);
  const base = new URL(page.url()).origin;
  const data = state(), starts = [], updates = [];
  const google = data.providers[2];
  Object.assign(google, {status:'ready', credential_revision:1,
    catalog:{available:true, stale:true, fetched_at:null, models:[]}});
  const model = data.models[0];
  Object.assign(model, {provider:'google', model_id:'gemini-2.5-flash', display_name:'Gemini Flash',
    enabled:false, default_options:{},
    capabilities:{fields:[
      {name:'max_output_tokens', label:'최대 출력 토큰', type:'integer', min:1, max:65536},
      {name:'temperature', label:'다양성', type:'number', min:0, max:2},
      {name:'thinking.budget', label:'사고 토큰 예산 (-1 자동, 0 끄기)', type:'integer', min:-1, max:24576}
    ]},
    verification_state:{student:{status:'unverified'}, mentor:{status:'unverified'}, analysis:{status:'unverified'}},
    probe_budgets:{student:1024, mentor:1500, analysis:2500}});
  data.models = [model];
  await page.context().addCookies([{name:'csrftoken', value:'google-csrf', url:base}]);
  await page.route('**/admin/ai/state', route => route.fulfill({json:data}));
  await page.route('**/admin/ai/providers/google/catalog', async route => {
    assert.equal(route.request().headers()['x-csrf-token'], 'google-csrf');
    google.catalog = {available:true, stale:false, fetched_at:'2026-10-09',
      models:[{model_id:'gemini-2.5-flash', thinking:true, max_temperature:2}]};
    await route.fulfill({json:{status:'saved'}});
  });
  await page.route('**/admin/ai/models/1/update', async route => {
    const body = route.request().postDataJSON();
    updates.push(body);
    model.default_options = body.default_options;
    model.config_version++;
    await route.fulfill({json:{status:'saved'}});
  });
  await page.route('**/admin/ai/models/1/probes', async route => {
    assert.equal(route.request().headers()['x-csrf-token'], 'google-csrf');
    const body = route.request().postDataJSON();
    starts.push(body);
    model.verification_state[body.role] = {status:'verifying', probe_request_id:body.request_id};
    await route.fulfill({status:202, json:{request_id:body.request_id, status:'verifying'}});
  });
  await page.route('**/admin/ai/probes/*', async route => {
    const id = new URL(route.request().url()).pathname.split('/').at(-1);
    const body = starts.find(body => body.request_id === id);
    model.verification_state[body.role] = {status:'succeeded', verified_at:'2026-10-09'};
    await route.fulfill({json:{request_id:id, status:'succeeded'}});
  });
  await page.goto(`${base}/admin/ai`);
  const card = page.locator('[data-provider="google"]');
  await card.getByRole('button', {name:'비생성 확인·목록 갱신', exact:true}).click();
  await card.getByText('gemini-2.5-flash', {exact:true}).waitFor();
  const row = page.locator('[data-model="1"]');
  await row.getByRole('button', {name:'표시명·활성·옵션 수정', exact:true}).click();
  await page.getByLabel('다양성', {exact:true}).fill('0');
  await page.getByLabel('사고 토큰 예산 (-1 자동, 0 끄기)', {exact:true}).fill('0');
  await page.getByRole('button', {name:'모델 설정 저장', exact:true}).click();
  await page.getByRole('status').filter({hasText:'완료'}).waitFor();
  assert.deepEqual(updates[0].default_options, {temperature:0, thinking:{budget:0}});
  for (const [role, label, budget] of [['student','학생봇',1024],['mentor','멘토',1500],['analysis','사후 분석',2500]]) {
    await row.getByRole('button', {name:`${label} 시험`, exact:true}).focus();
    await page.keyboard.press('Enter');
    const disclosure = await page.locator('#ai-editor').innerText();
    assert(disclosure.includes(String(budget)) && disclosure.includes('비용') && disclosure.includes('자동 재시도 0회'));
    await page.getByRole('button', {name:'시험 시작', exact:true}).click();
    await row.getByRole('button', {name:`${label} 진행 조회`, exact:true}).click();
    await page.getByRole('status').filter({hasText:'성공'}).waitFor();
    assert.equal(starts.at(-1).role, role);
  }
  await page.setViewportSize({width:390, height:844});
  await page.reload();
  assert.equal(starts.length, 3);
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  return {pageErrors:[]};
}
