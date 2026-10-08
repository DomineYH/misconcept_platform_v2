import assert from 'node:assert/strict';
import {state} from './browser_ai_connections.mjs';

export default async function checkAIModels(page) {
  const base = new URL(page.url()).origin;
  const requests = [];
  await page.route('**/admin/ai/state', route => route.fulfill({json:state()}));
  await page.route('**/admin/ai/models', capture);
  await page.route('**/admin/ai/models/1/update', capture);
  async function capture(route) {
    requests.push(route.request().postDataJSON());
    await route.fulfill({json:{status:'saved'}});
  }
  for (const width of [1280, 390]) {
    await page.setViewportSize({width, height:900});
    await page.goto(`${base}/admin/ai`);
    await page.locator('[data-provider=openai]').getByRole('button', {name:'gpt-5-mini', exact:true}).click();
    assert.equal(await page.getByLabel('모델 ID', {exact:true}).inputValue(), 'gpt-5-mini');
    await page.getByLabel('표시명', {exact:true}).fill('목록에서 등록');
    await page.getByRole('button', {name:'모델 등록', exact:true}).click();
    await page.getByRole('status').filter({hasText:'완료'}).waitFor();
    await page.locator('[data-provider=anthropic]').getByRole('button', {name:'모델 ID 직접 입력'}).click();
    await page.getByLabel('모델 ID', {exact:true}).fill(' custom-model-id ');
    await page.getByLabel('표시명', {exact:true}).fill('<img src=x onerror=alert(1)>');
    await page.getByRole('button', {name:'모델 등록', exact:true}).click();
    await page.getByRole('status').filter({hasText:'완료'}).waitFor();
    const model = page.locator('[data-model="1"]');
    await model.getByRole('button', {name:'표시명·활성·옵션 수정'}).click();
    assert.equal(await page.getByLabel('모델 ID', {exact:true}).count(), 0, 'registered ID is immutable');
    assert.equal(await page.getByLabel('temperature', {exact:true}).count(), 0, 'unsupported sampling option absent');
    await page.getByLabel('최대 출력 토큰', {exact:true}).fill('3072');
    await page.getByRole('button', {name:'모델 설정 저장', exact:true}).click();
    await page.getByRole('status').filter({hasText:'완료'}).waitFor();
    const unknown = page.locator('[data-model="2"]');
    assert(await unknown.getByRole('button', {name:'학생봇 시험', exact:true}).isDisabled(), 'unknown capability blocks paid trial');
    await unknown.getByRole('button', {name:'표시명·활성·옵션 수정'}).click();
    assert(await page.getByLabel('모델 활성', {exact:true}).isDisabled(), 'unverified model cannot be activated');
    assert.equal(await page.getByLabel('최대 출력 토큰', {exact:true}).count(), 0, 'unknown model cannot declare options');
    await page.keyboard.press('Escape');
    assert(await page.locator('#ai-editor').isHidden(), 'Escape closes editor');
    assert(await unknown.getByRole('button', {name:'표시명·활성·옵션 수정'}).evaluate(el => el === document.activeElement), 'close restores focus');
  }
  assert.equal(requests.length, 6);
  assert.equal(requests[1].model_id, 'custom-model-id');
  assert.equal(requests[1].provider, 'anthropic');
  assert.equal(requests[2].default_options.max_output_tokens, 3072);
  assert.equal(requests[2].expected_version, 2);
  assert.equal('capabilities' in requests[2], false, 'client never declares capability');
  return {pageErrors:[]};
}
