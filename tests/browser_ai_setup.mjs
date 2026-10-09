import assert from 'node:assert/strict';

export default async function checkAISetup(page) {
  page.setDefaultTimeout(8000);
  const base = new URL(page.url()).origin;
  const data = {master_key_available:false, models:[], models_available:false, settings:null,
    providers:['openai','anthropic','google'].map(provider => ({provider, connection_version:1,
      credential_revision:0, key_registered:false, masked_hint:null, enabled:false,
      status:'unconfigured', verified_at:null, error_code:null, impact:[],
      catalog:{available:false, stale:false, fetched_at:null, models:[]}}))};
  const submitted = [];
  await page.context().addCookies([{name:'csrftoken', value:'setup-csrf', url:base}]);
  await page.route('**/admin/ai/state', route => route.fulfill({json:data}));
  await page.route('**/admin/ai/providers/openai/key', async route => {
    submitted.push(route.request().postDataJSON());
    Object.assign(data.providers[0], {key_registered:true, masked_hint:'••••4321', enabled:true,
      status:'ready', connection_version:2, credential_revision:1});
    await route.fulfill({json:{status:'saved'}});
  });
  for (const width of [1280,390]) {
    await page.setViewportSize({width, height:900});
    data.master_key_available = false;
    Object.assign(data.providers[0], {key_registered:false, masked_hint:null, enabled:false,
      status:'unconfigured', connection_version:1, credential_revision:0});
    await page.goto(`${base}/admin/ai`);
    await page.getByText('작성 기본값·호출 설정은 아직 제공되지 않습니다.', {exact:true}).waitFor();
    assert(await page.locator('#ai-error').isHidden(), 'partial real state renders without errors');
    assert(await page.locator('[data-provider=openai]').getByRole('button', {name:'키 저장', exact:true}).isDisabled());
    data.master_key_available = true;
    await page.getByRole('button', {name:'설정 다시 불러오기'}).click();
    await page.locator('[data-provider=openai]').getByRole('button', {name:'키 저장', exact:true}).click();
    await page.getByLabel('새 API 키').fill('SETUP-SECRET-4321');
    await page.getByLabel('현재 비밀번호').fill('SETUP-PASSWORD');
    await page.getByRole('button', {name:'변경 제출', exact:true}).click();
    await page.getByRole('status').filter({hasText:'완료'}).waitFor();
    assert(await page.getByText('••••4321', {exact:false}).isVisible());
    assert(await page.locator('[data-provider=openai]').getByRole('button', {name:'비생성 확인·목록 갱신'}).isDisabled());
    assert(await page.locator('[data-provider=openai]').getByRole('button', {name:'모델 ID 직접 입력'}).isDisabled());
    assert.equal(await page.getByRole('button', {name:'작성 기본값·호출 설정 수정'}).count(), 0);
    assert.equal(await page.getByLabel('현재 비밀번호').inputValue(), '');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  }
  assert.equal(submitted.length, 2);
  assert(submitted.every(payload => payload.expected_version === 1));
  return {pageErrors:[]};
}
