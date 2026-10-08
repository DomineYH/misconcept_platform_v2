import assert from 'node:assert/strict';
import {state} from './browser_ai_connections.mjs';

export default async function checkAISecrets(page) {
  const base = new URL(page.url()).origin;
  const requests = [];
  let fail = false;
  await page.context().addCookies([{name:'csrftoken', value:'ai-csrf', url:base}]);
  await page.route('**/admin/ai/state', route => route.fulfill({json:state()}));
  await page.route('**/admin/ai/providers/openai/*', async route => {
    requests.push({path:new URL(route.request().url()).pathname, body:route.request().postDataJSON(), csrf:route.request().headers()['x-csrf-token']});
    await route.fulfill({status:fail ? 422 : 200, json:fail ? {detail:[{input:'SECRET-KEY PASSWORD-SENTINEL'}]} : {status:'saved'}});
  });
  for (const width of [1280, 390]) {
    await page.setViewportSize({width, height:900});
    await page.goto(`${base}/admin/ai`);
    const card = page.locator('[data-provider="openai"]');
    await card.getByRole('button', {name:'키 교체', exact:true}).focus();
    await page.keyboard.press('Enter');
    assert((await page.locator('#ai-editor').innerText()).includes('진행 중 호출 1건'), 'impact is shown before sensitive action');
    assert((await page.locator('#ai-editor').innerText()).includes('이전 키로 시작한 호출에도 중단을 요청'), 'revocation covers every credential revision');
    assert((await page.locator('#ai-editor').innerText()).includes('이미 발생한 비용은 취소되지 않을 수'), 'upstream cost warning');
    const key = page.getByLabel('새 API 키', {exact:true});
    const password = page.getByLabel('현재 비밀번호', {exact:true});
    assert(await key.evaluate(el => el === document.activeElement), 'editor focuses first field');
    await key.fill('SECRET-KEY');
    await key.press('Tab');
    assert(await password.evaluate(el => el === document.activeElement), 'keyboard field order');
    await password.fill('PASSWORD-SENTINEL');
    fail = width === 390;
    await page.getByRole('button', {name:'변경 제출', exact:true}).click();
    await page.waitForFunction(() => [...document.querySelectorAll('#ai-editor input[type=password]')].every(el => el.value === ''));
    if (fail) {
      await page.getByRole('alert').filter({hasText:'입력값'}).waitFor();
      assert(await page.locator('#ai-error').evaluate(el => el === document.activeElement), 'error receives focus');
    } else await page.getByRole('status').filter({hasText:'완료'}).waitFor();
    assert(!/SECRET-KEY|PASSWORD-SENTINEL/.test(await page.content()), 'response and initial HTML do not expose secrets');
    const storage = await page.evaluate(() => JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)]));
    assert(!/SECRET-KEY|PASSWORD-SENTINEL/.test(storage + page.url()), 'no secrets in storage or URL');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no horizontal overflow');
    fail = false;
    await card.getByRole('button', {name:'키 삭제', exact:true}).click();
    assert((await page.locator('#ai-editor').innerText()).includes('모델 설정과 과거 기록은 보존'), 'delete only credentials');
    assert(await key.count() === 0, 'no original key retrieval or key needed for delete');
    await password.fill('PASSWORD-SENTINEL');
    await page.getByRole('button', {name:'변경 제출', exact:true}).click();
    await page.getByRole('status').filter({hasText:'완료'}).waitFor();
  }
  assert.equal(requests.length, 4);
  for (const request of requests) {
    assert.equal(request.csrf, 'ai-csrf');
    assert.equal(request.body.expected_version, 3);
    assert.equal(request.body.current_password, 'PASSWORD-SENTINEL');
  }
  return {pageErrors:[]};
}
