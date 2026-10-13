import assert from 'node:assert/strict';
import {state} from './browser_ai_connections.mjs';

export default async function checkSavedOptions(page) {
  const base = new URL(page.url()).origin;
  const data = state();
  const model = data.models[0];
  model.default_options = {max_output_tokens:2048,reasoning:{effort:'removed'},unknown:{keep:0}};
  model.capabilities.fields.push({name:'reasoning.effort',label:'추론 수준',type:'string',choices:['minimal','low','medium','high'],default:'medium'});
  const writes = [];
  await page.route('**/admin/ai/state', route => route.fulfill({json:data}));
  await page.route('**/admin/ai/models/1/update', route => {
    writes.push(route.request().postDataJSON());
    return route.fulfill({status:422,json:{detail:{code:'invalid_options'}}});
  });
  await page.goto(`${base}/admin/ai`);
  await page.locator('[data-model="1"]').getByRole('button',{name:'표시명·활성·옵션 수정'}).click();
  const effort = page.getByLabel('추론 수준',{exact:true});
  assert.equal(await effort.inputValue(),'removed','unsupported saved enum remains selected');
  assert((await effort.locator('option:checked').innerText()).includes('지원하지 않는 저장 값'));
  await page.getByLabel('최대 출력 토큰',{exact:true}).fill('3072');
  await page.getByRole('button',{name:'모델 설정 저장',exact:true}).click();
  await page.getByRole('alert').waitFor();
  assert.deepEqual(writes[0].default_options,{max_output_tokens:3072,reasoning:{effort:'removed'},unknown:{keep:0}});
  await page.keyboard.press('Escape');
  await page.locator('[data-model="1"]').getByRole('button',{name:'표시명·활성·옵션 수정'}).click();
  assert.equal(await effort.inputValue(),'removed','invalid save retains stored value');
  await effort.selectOption('minimal');
  await page.getByRole('button',{name:'모델 설정 저장',exact:true}).click();
  await page.getByRole('alert').waitFor();
  assert.equal(writes.at(-1).default_options.reasoning.effort,'minimal');
  assert.deepEqual(writes.at(-1).default_options.unknown,{keep:0});
  return {pageErrors:[]};
}
