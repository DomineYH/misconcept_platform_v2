// Shared chat controls with the product fetch transport and isolated responses.
export default async function checkChat(page) {
  const base = page.url().split('/').slice(0, 3).join('/');
  const errors = [], checks = [], csrf = [];
  const assert = (value, message) => { if (!value) throw new Error(message); };
  let sendCount = 0, analysisCount = 0, polls = 0, mode = 'ok', releaseSend;
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => dialog.accept());
  await page.context().addCookies([{name:'csrftoken', value:'browser-test-token', url:base}]);
  await page.route('**/login', route => route.fulfill({status:200, contentType:'text/html', body:'<h1>Login</h1>'}));
  await page.route('**/sessions/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (request.method() === 'POST') csrf.push(request.headers()['x-csrf-token']);
    if (path.endsWith('/messages/updates')) {
      polls++;
      await route.fulfill({status:204});
    } else if (path.endsWith('/turns/stream')) {
      sendCount++;
      const body = request.postDataJSON();
      if (mode === 'held') await new Promise(resolve => { releaseSend = resolve; });
      if (mode === 'auth') {
        await route.fulfill({status:401, contentType:'application/json', body:'{"code":"AUTH_EXPIRED"}'});
      } else {
        const snapshot = {
          run_id:`run-${sendCount}`, turn_id:`turn-${sendCount}`, turn_index:sendCount,
          request_id:body.request_id, operation:'student', session_id:1,
          teacher_message_id:sendCount * 2 - 1,
          status:mode === 'failed' ? 'failed' : 'completed', retryable:mode === 'failed',
          result_kind:mode === 'failed' ? null : 'message',
          error_code:mode === 'failed' ? 'provider_error' : null, partial_text:null,
          message:mode === 'failed' ? null : {
            id:sendCount * 2, role:'student', content:'Reply', created_at:'2026-10-08T00:00:00Z'
          }
        };
        await route.fulfill({status:200, contentType:'application/json', body:JSON.stringify(snapshot)});
      }
    } else if (path.endsWith('/runs')) {
      await route.fulfill({status:401, contentType:'application/json', body:'{"code":"AUTH_EXPIRED"}'});
    } else if (path.endsWith('/analyze')) {
      analysisCount++;
      await route.fulfill({status:200, contentType:'application/json', body:JSON.stringify(
        analysisCount === 1 ? {retryable:true, feedback_status:'failed', feedback:'Retry'} : {retryable:false, feedback_status:'ok'}
      )});
    } else if (path.endsWith('/analysis_modal')) {
      await route.fulfill({status:200, contentType:'text/html', body:'<button onclick="closeAnalysisModal()">Close result</button><p>Analysis result</p>'});
    } else {
      await route.fulfill({status:200, contentType:'application/json', body:'{"ended":true}'});
    }
  });
  await page.setViewportSize({width:1280, height:900});
  await page.goto(`${base}/chat`);
  await page.evaluate(() => { localStorage.clear(); sessionStorage.clear(); });
  await page.reload();
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  await page.addScriptTag({url:`${base}/static/js/chat.js`});
  await page.locator('#scenario-panel-toggle').click();
  assert(await page.locator('#scenario-panel-toggle').getAttribute('aria-expanded') === 'false', 'desktop collapse');
  await page.keyboard.press('Control+b');
  assert(await page.locator('#scenario-panel-toggle').getAttribute('aria-expanded') === 'true', 'keyboard panel toggle');
  await page.setViewportSize({width:390, height:844});
  await page.locator('.mobile-tab[data-panel="scenario"]').click();
  assert(await page.locator('#scenario-panel').isVisible(), 'mobile scenario panel');
  await page.locator('.mobile-tab[data-panel="chat"]').click();
  assert(await page.locator('#teacher-input').isVisible(), 'mobile chat panel');
  await page.setViewportSize({width:1280, height:900});
  checks.push('desktop/mobile panels, keyboard toggle and duplicate initialization');

  const input = page.locator('#teacher-input');
  await input.fill('Hello');
  await input.press('Shift+Enter');
  assert((await input.inputValue()).includes('\n') && sendCount === 0, 'Shift+Enter newline');
  await input.fill('Hello');
  mode = 'held';
  await input.press('Enter');
  await page.waitForFunction(() => document.querySelector('#teacher-input').disabled);
  assert(await page.locator('[data-request-id].message-teacher').count() === 1, 'single optimistic teacher');
  assert(await page.locator('.student-run-status').innerText() === '전송 중', 'sending is provisional');
  releaseSend();
  await page.waitForFunction(() => document.querySelectorAll('[data-message-id]').length === 2 && !document.querySelector('#teacher-input').disabled);
  assert(await page.locator('[data-message-id="1"]').count() === 1 && await page.locator('[data-message-id="2"]').count() === 1, 'server IDs reconcile without duplicates');
  assert(sendCount === 1, 'one Enter one request');
  assert(await page.locator('#polling-enabled').inputValue() === 'false' && polls === 0, 'normal messages never poll');
  checks.push('Enter, provisional send, saved JSON reconciliation and zero polling');

  mode = 'failed';
  await input.fill('retry draft');
  await input.press('Enter');
  await page.getByRole('button', {name:'학생 응답 다시 받기', exact:true}).waitFor({state:'visible'});
  assert(await input.inputValue() === 'retry draft', 'failed generation restores input');
  assert(await input.isDisabled(), 'unresolved teacher prevents a new question');
  assert(await page.locator('[data-message-id="3"]').count() === 1, 'failed generation preserves saved teacher');
  checks.push('failed generation preserves teacher and draft');

  await page.locator('#end-session-btn').click();
  await page.waitForFunction(() => document.querySelector('#end-session-btn').dataset.state === 'ready-to-analyze');
  assert(await input.isDisabled(), 'ended session locks messages');
  assert(!(await page.locator('#end-session-btn').isDisabled()), 'failed analysis allows retry');
  assert(await page.locator('#polling-enabled').inputValue() === 'false', 'ended polling stopped');
  await page.locator('#end-session-btn').click();
  await page.waitForFunction(() => document.querySelector('#end-session-btn').dataset.state === 'done');
  assert(analysisCount === 2, 'analysis retries exactly once');
  await page.keyboard.press('Escape');
  assert(await page.locator('#analysis-modal-overlay').isHidden(), 'Escape closes modal');
  await page.locator('#end-session-btn').click();
  await page.getByText('Close result', {exact:true}).click();
  assert(await page.locator('#analysis-modal-overlay').isHidden(), 'button closes modal');
  await page.goto(`${base}/chat?ended=1`);
  assert(await input.isDisabled(), 'ended reload remains locked');
  assert(await page.locator('#end-session-btn').getAttribute('data-state') === 'ready-to-analyze', 'ended reload can analyze');
  checks.push('end, failed analysis retry, modal closing and ended reload');

  await page.evaluate(() => sessionStorage.clear());
  await page.goto(`${base}/chat`);
  mode = 'auth';
  await input.fill('unsent after expiry');
  await input.press('Enter');
  await page.locator('#session-login-btn').waitFor({state:'visible'});
  assert(await input.isDisabled(), 'auth expiry locks input');
  assert(await page.locator('#polling-enabled').inputValue() === 'false' && polls === 0, 'auth expiry cannot start polling');
  assert(await page.evaluate(() => sessionStorage.getItem('chat-draft-1')) === 'unsent after expiry', 'in-flight draft preserved');
  await page.reload();
  await page.locator('#session-login-btn').waitFor({state:'visible'});
  assert(await input.inputValue() === 'unsent after expiry', 'draft restored after reload and lookup expiry');
  await page.locator('#session-login-btn').click();
  await page.waitForURL('**/login');
  assert(csrf.every(value => value === 'browser-test-token'), 'CSRF passed on all writes');
  assert(errors.length === 0, `uncaught page errors: ${errors.join('; ')}`);
  checks.push('POST/lookup authentication expiry, draft restoration, CSRF and zero page errors');
  return {checks, pageErrors:errors, sendCount, analysisCount, polls};
}
