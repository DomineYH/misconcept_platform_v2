import assert from 'node:assert/strict';
import {state} from './browser_ai_connections.mjs';

export default async function checkSelectors(page) {
  const base = new URL(page.url()).origin;
  const data = state();
  const lists = [['gpt-6.1-sol'], ['claude-sonnet-5-5'], ['gemini-3.8-flash','gemini-3.1-pro-preview']];
  data.providers.forEach((provider,index) => {
    Object.assign(provider,{key_registered:true,enabled:true,status:'ready'});
    provider.catalog.models = lists[index].map(model_id => ({model_id,...(index === 2 ? {preview:model_id === 'gemini-3.1-pro-preview'} : {})}));
  });
  const model = data.models[0];
  model.model_id = 'gpt-6.1-sol';
  model.default_options = {};
  model.capabilities.fields = [{name:'reasoning.effort',label:'추론 수준',type:'string',choices:['low','medium','high','xhigh','max'],default:'medium'}];
  model.capabilities.option_notes = ['temperature는 지원하지 않습니다.'];
  const writes = [];
  await page.route('**/admin/ai/state',route => route.fulfill({json:data}));
  await page.route('**/admin/ai/models/1/update',route => {
    const body = route.request().postDataJSON();
    writes.push(body);
    model.default_options = body.default_options;
    model.config_version++;
    return route.fulfill({json:{status:'saved'}});
  });
  await page.route('**/admin/ai/providers/*/catalog',route => {
    const provider = new URL(route.request().url()).pathname.split('/').at(-2);
    writes.push(provider);
    if (provider === 'openai') return route.fulfill({status:503,json:{detail:{code:'transient'}}});
    data.providers.find(p => p.provider === provider).catalog = {available:true,models:[],stale:false,fetched_at:'2026-10-13'};
    return route.fulfill({json:{status:'saved'}});
  });
  await page.goto(`${base}/admin/ai`);
  const labels = ['OpenAI','Claude','Gemini'];
  for (const [index,label] of labels.entries()) {
    const selector = page.getByLabel(`${label} 모델 ID 선택`,{exact:true});
    await selector.waitFor();
    assert.deepEqual(await selector.locator('option').evaluateAll(nodes => nodes.map(n => n.value)),['',...lists[index]]);
  }
  assert((await page.getByLabel('OpenAI 모델 ID 선택',{exact:true}).innerText()).includes('상태 미확인'));
  assert((await page.getByLabel('Gemini 모델 ID 선택',{exact:true}).locator('option[value="gemini-3.1-pro-preview"]').innerText()).includes('Preview'));
  assert.equal(writes.length,0,'reading and selecting do not call providers');
  const card = page.locator('[data-model="1"]');
  await card.getByRole('button',{name:'표시명·활성·옵션 수정'}).click();
  const effort = page.getByLabel('추론 수준',{exact:true});
  assert.equal(await effort.inputValue(),'');
  assert((await page.locator('#ai-editor').innerText()).includes('기본값: medium'));
  assert.deepEqual(await effort.locator('option').evaluateAll(nodes => nodes.map(n => n.value)),['','low','medium','high','xhigh','max']);
  await effort.focus();
  await page.keyboard.press('End');
  assert.equal(await effort.inputValue(),'max');
  await page.getByRole('button',{name:'모델 설정 저장',exact:true}).focus();
  await page.keyboard.press('Enter');
  await page.getByRole('status').filter({hasText:'완료'}).waitFor();
  assert.deepEqual(writes[0].default_options,{reasoning:{effort:'max'}});
  await page.getByRole('button',{name:'닫기',exact:true}).click();
  assert(await card.getByRole('button',{name:'표시명·활성·옵션 수정'}).evaluate(el => el === document.activeElement));
  await page.keyboard.press('Enter');
  assert.equal(await effort.inputValue(),'max','saved value restores on reedit');
  await effort.selectOption('');
  await page.getByRole('button',{name:'모델 설정 저장',exact:true}).click();
  await page.getByRole('status').filter({hasText:'완료'}).waitFor();
  assert.deepEqual(writes[1].default_options,{},'omission stays omission');
  await page.keyboard.press('Escape');
  await page.locator('[data-provider=openai]').getByRole('button',{name:'비생성 확인·목록 갱신'}).click();
  await page.getByRole('alert').waitFor();
  assert.equal(await page.getByLabel('OpenAI 모델 ID 선택',{exact:true}).locator('option[value="gpt-6.1-sol"]').count(),1,'failed refresh keeps reference list');
  await page.locator('[data-provider=google]').getByRole('button',{name:'비생성 확인·목록 갱신'}).click();
  await page.getByRole('status').filter({hasText:'완료'}).waitFor();
  assert.equal(await page.getByLabel('Gemini 모델 ID 선택',{exact:true}).locator('option').count(),1,'successful empty list clears choices');
  assert(await card.isVisible(),'registered model remains');
  await page.locator('[data-provider=google]').getByRole('button',{name:'모델 ID 직접 입력'}).click();
  await page.getByLabel('모델 ID',{exact:true}).fill('custom-direct');
  assert.equal(writes.length,4,'direct input does not call a provider');
  await page.keyboard.press('Escape');
  await page.route('**/admin/ai/models',route => {
    writes.push(route.request().postDataJSON());
    return route.fulfill({json:{status:'saved'}});
  });
  const claude = page.getByLabel('Claude 모델 ID 선택',{exact:true});
  await claude.selectOption('claude-sonnet-5-5');
  await page.locator('[data-provider=anthropic]').getByRole('button',{name:'선택 모델 등록',exact:true}).click();
  await page.getByRole('button',{name:'모델 등록',exact:true}).click();
  await page.getByRole('status').filter({hasText:'완료'}).waitFor();
  await page.getByRole('button',{name:'닫기',exact:true}).click();
  assert(await claude.evaluate(el => el === document.activeElement),'after registration focus returns to the catalog selector when the registration button is disabled');
  return {pageErrors:[]};
}
