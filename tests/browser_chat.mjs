// Run against tests/browser_server.py with a Playwright Page (no real API/DB).
export default async function checkChat(page) {
  const base = page.url().split('/').slice(0, 3).join('/');
  await page.unrouteAll({behavior: 'wait'});
  page.removeAllListeners('dialog');
  page.removeAllListeners('pageerror');
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => dialog.accept());
  const assert = (value, message) => { if (!value) throw new Error(message); };
  const results = [];
  const message = (id, role, content) => `<div class="message message-${role}" data-message-id="${id}"><div class="message-bubble">${content}</div></div>`;
  let sendCount = 0, analysisCount = 0, csrf = [];
  let releaseSend, sendMode = 'ok', pollingBody = '', pollingAuth = false;
  await page.context().addCookies([{name: 'csrftoken', value: 'browser-test-token', url: base}]);
  await page.route('**/login', route => route.fulfill({status:200, contentType:'text/html', body:'<h1>Login</h1>'}));
  await page.route('**/sessions/**', async route => {
    const request = route.request(), path = '/' + request.url().split('/').slice(3).join('/');
    csrf.push(request.headers()['x-csrf-token']);
    if (path.endsWith('/messages/updates')) {
      if (pollingAuth) {
        await route.fulfill({status:401, headers:{'HX-Trigger':'{"auth-expired":{"redirect_url":"/login"}}'}, body:'{"code":"AUTH_EXPIRED"}'});
        return;
      }
      await route.fulfill(pollingBody ? {status: 200, contentType: 'text/html', body: pollingBody} : {status: 204});
    } else if (path.endsWith('/messages')) {
      sendCount++;
      if (sendMode === 'held') await new Promise(resolve => { releaseSend = resolve; });
      if (sendMode === 'failed') {
        await route.fulfill({status:500, contentType:'application/json', body:'{"detail":"fake send failure"}'});
      } else if (sendMode === 'auth') {
        await route.fulfill({status:401, headers:{'HX-Trigger':'{"auth-expired":{"redirect_url":"/login"}}'}, body:'{"code":"AUTH_EXPIRED"}'});
      } else {
        await route.fulfill({status:200, contentType:'text/html', body:message(1,'teacher','Hello') + message(2,'student','Reply')});
      }
    } else if (path.endsWith('/analyze')) {
      analysisCount++;
      await route.fulfill({status:200, contentType:'application/json', body:JSON.stringify(analysisCount === 1 ? {retryable:true, feedback_status:'failed', feedback:'Retry'} : {retryable:false, feedback_status:'ok'})});
    } else if (path.endsWith('/analysis_modal')) {
      await route.fulfill({status:200, contentType:'text/html', body:'<button onclick="closeAnalysisModal()">Close result</button><p>Analysis result</p>'});
    } else {
      await route.fulfill({status:200, contentType:'application/json', body:'{"ended":true}'});
    }
  });
  await page.setViewportSize({width:1280, height:900});
  await page.goto(`${base}/chat`);
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  // Loading twice must not install duplicate listeners or redeclare globals.
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
  results.push('desktop/mobile panels and duplicate initialization');

  const input = page.locator('#teacher-input');
  await input.fill('Hello');
  await input.press('Shift+Enter');
  assert((await input.inputValue()).includes('\n') && sendCount === 0, 'Shift+Enter newline');
  await input.fill('Hello');
  sendMode = 'held';
  await input.press('Enter');
  await page.waitForFunction(() => document.querySelector('#teacher-input').disabled);
  assert(await page.locator('.temp-message').count() === 1, 'single optimistic message');
  assert(await page.locator('#end-session-btn').isDisabled(), 'cannot end during send');
  pollingBody = message(1,'teacher','Hello');
  await page.evaluate(() => htmx.ajax('GET','/sessions/1/messages/updates',{target:'#messages-container',swap:'beforeend'}));
  releaseSend();
  await page.waitForFunction(() => document.querySelectorAll('[data-message-id]').length === 2 && !document.querySelector('#teacher-input').disabled);
  // A late polling response repeats both rows after POST.
  pollingBody = message(1,'teacher','Hello') + message(2,'student','Reply');
  await page.evaluate(() => htmx.ajax('GET','/sessions/1/messages/updates',{target:'#messages-container',swap:'beforeend'}));
  assert(await page.locator('[data-message-id]').count() === 2, 'deduplicated late poll');
  assert(await page.locator('#last-message-id').inputValue() === '2', 'cursor follows DOM');
  assert(sendCount === 1, 'one Enter one request');
  results.push('Enter, send/poll interleaving, deduplication and cursor');

  sendMode = 'failed'; pollingBody = '';
  await input.fill('retry draft');
  await input.press('Enter');
  await page.waitForFunction(() => document.querySelector('#teacher-input').value === 'retry draft' && !document.querySelector('#teacher-input').disabled);
  assert(await page.locator('.temp-message').count() === 0, 'failed send removes optimistic row');
  results.push('failed send restores input');

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
  results.push('end, failed analysis retry, modal closing, ended reload');

  await page.goto(`${base}/chat`);
  sendMode = 'auth';
  await input.fill('unsent after expiry');
  await input.press('Enter');
  await page.locator('#session-login-btn').waitFor({state:'visible'});
  assert(await input.isDisabled(), 'auth expiry locks input');
  assert(await page.locator('#polling-enabled').inputValue() === 'false', 'auth expiry stops poll');
  assert(await page.evaluate(() => localStorage.getItem('chat-draft-1')) === 'unsent after expiry', 'in-flight draft preserved');
  await page.reload();
  assert(await input.inputValue() === 'unsent after expiry', 'draft restored after reload');
  pollingAuth = true;
  await input.fill('poll expiry draft');
  for (let attempt = 0; attempt < 3; attempt++) {
    await page.evaluate(async () => {
      try { await htmx.ajax('GET','/sessions/1/messages/updates',{target:'#messages-container',swap:'beforeend'}); } catch (_) {}
    });
  }
  await page.locator('#session-login-btn').waitFor({state:'visible'});
  assert(await page.locator('#polling-enabled').inputValue() === 'false', 'three polling auth errors stop polling');
  await page.locator('#session-login-btn').click();
  await page.waitForURL('**/login');
  assert(csrf.every(value => value === 'browser-test-token'), 'CSRF passed by HTMX and fetch');
  assert(errors.length === 0, `uncaught console errors: ${errors.join('; ')}`);
  results.push('authentication expiry, draft restoration, CSRF and zero page errors');
  return {checks: results, pageErrors: errors, sendCount, analysisCount};
}
