import assert from 'node:assert/strict';
import {state} from './browser_ai_connections.mjs';

export default async function checkAIBoundaries(page) {
  page.setDefaultTimeout(8000);
  const base = new URL(page.url()).origin;
  let data = state(), unavailable = false;
  const writes = [];
  await page.route('**/admin/ai/state', route => route.fulfill({status:unavailable ? 503 : 200, json:unavailable ? {detail:'PRIVATE-SENTINEL'} : data}));
  await page.route('**/admin/ai/providers/openai/*', async route => {
    writes.push(new URL(route.request().url()).pathname);
    await route.fulfill({status:409, json:{detail:'PRIVATE-SENTINEL', input:route.request().postDataJSON()}});
  });
  data.master_key_available = false;
  await page.goto(`${base}/admin/ai`);
  await page.getByText('마스터 키 설정 필요', {exact:false}).waitFor();
  assert(await page.locator('[data-provider=openai]').getByRole('button', {name:'키 교체', exact:true}).isDisabled());
  assert(await page.locator('[data-model="1"]').getByRole('button', {name:'학생봇 시험', exact:true}).isDisabled());
  assert(await page.locator('[data-provider=google]').getByRole('button', {name:'키 교체', exact:true}).isDisabled(), 'decryption failure blocks key replacement');
  assert(await page.locator('[data-provider=google]').getByRole('button', {name:'키 삭제', exact:true}).isEnabled(), 'deletion does not require decryption');
  assert(await page.locator('[data-provider=openai]').getByRole('button', {name:'비활성화', exact:true}).isEnabled(), 'disable does not require master key');
  data.providers[0].enabled = false;
  data.providers[2].enabled = false;
  await page.getByRole('button', {name:'설정 다시 불러오기'}).click();
  await page.locator('[data-provider=google]').getByRole('button', {name:'재활성화', exact:true}).waitFor();
  assert(await page.locator('[data-provider=openai]').getByRole('button', {name:'재활성화', exact:true}).isDisabled(), 'missing master blocks reactivation');
  data.master_key_available = true;
  await page.getByRole('button', {name:'설정 다시 불러오기'}).click();
  await page.getByText('마스터 키 설정 필요', {exact:false}).waitFor({state:'hidden'});
  assert(await page.locator('[data-provider=openai]').getByRole('button', {name:'재활성화', exact:true}).isEnabled(), 'restored master permits ready connection reactivation');
  assert(await page.locator('[data-provider=google]').getByRole('button', {name:'재활성화', exact:true}).isDisabled(), 'decryption failure blocks reactivation');
  assert(await page.locator('[data-provider=google]').getByRole('button', {name:'키 삭제', exact:true}).isEnabled(), 'broken disabled key remains deletable');
  data = state();
  data.models[0].display_name = '<img src=x onerror=window.injected=true>';
  await page.getByRole('button', {name:'설정 다시 불러오기'}).click();
  await page.getByRole('heading', {name:/<img src=x/}).waitFor();
  assert.equal(await page.locator('#ai-content img').count(), 0, 'API metadata rendered as text');
  await page.locator('[data-provider=openai]').getByRole('button', {name:'비활성화', exact:true}).click();
  await page.getByLabel('현재 비밀번호').fill('PRIVATE-SENTINEL');
  await page.getByRole('button', {name:'변경 제출', exact:true}).click();
  await page.getByRole('alert').filter({hasText:'충돌'}).waitFor();
  assert.equal(await page.getByLabel('현재 비밀번호').inputValue(), '');
  assert(!(await page.content()).includes('PRIVATE-SENTINEL'), '409 input never reflected');
  await page.getByRole('button', {name:'닫기', exact:true}).click();
  await page.locator('[data-provider=openai]').getByRole('button', {name:'비생성 확인·목록 갱신', exact:true}).click();
  await page.getByRole('status').filter({hasText:'실패'}).waitFor();
  assert(await page.locator('[data-provider=openai]').getByRole('button', {name:'gpt-5-mini', exact:true}).isVisible(), 'failed refresh preserves last successful catalog');
  assert.equal(writes.length, 2, 'no implicit provider calls');

  data = state();
  const model = data.models[0];
  model.provider = 'google';
  model.model_id = 'fixture-gemini';
  model.capabilities.fields = [
    {name:'max_output_tokens', label:'최대 출력 토큰', type:'integer', min:1, max:4096},
    {name:'temperature', label:'다양성', type:'number', min:0, max:2},
    {name:'thinking.budget_tokens', label:'추론 예산', type:'integer', min:0, max:4096},
    {name:'thinking.level', label:'추론 수준', type:'string', choices:['low','high']}
  ];
  model.default_options = {max_output_tokens:2048, temperature:1, thinking:{budget_tokens:1024}};
  let options;
  await page.route('**/admin/ai/models/1/update', async route => {
    options = route.request().postDataJSON().default_options;
    await route.fulfill({json:{status:'saved'}});
  });
  await page.getByRole('button', {name:'설정 다시 불러오기'}).click();
  await page.getByRole('heading', {name:/fixture-gemini/}).waitFor();
  await page.locator('[data-model="1"]').getByRole('button', {name:'표시명·활성·옵션 수정'}).click();
  await page.getByLabel('다양성', {exact:true}).fill('0');
  await page.getByLabel('추론 예산', {exact:true}).fill('0');
  await page.getByRole('button', {name:'모델 설정 저장', exact:true}).click();
  await page.getByRole('status').filter({hasText:'완료'}).waitFor();
  assert.deepEqual(options, {max_output_tokens:2048, temperature:0, thinking:{budget_tokens:0}}, 'zero and omission remain distinct; provider options remain nested');
  unavailable = true;
  await page.getByRole('button', {name:'설정 다시 불러오기'}).click();
  await page.getByRole('status').filter({hasText:'불러올 수 없습니다'}).waitFor();
  assert(!(await page.content()).includes('PRIVATE-SENTINEL'), 'initial/read error detail is safe');
  return {pageErrors:[]};
}
