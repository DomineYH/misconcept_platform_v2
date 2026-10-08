// Authorization/ended-session failures stop transport and preserve the tab draft.
export default async function checkStudentErrors(page) {
  const base = page.url().split('/').slice(0, 3).join('/');
  const assert = (value, message) => { if (!value) throw new Error(message); };
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.clock.install();
  await page.clock.pauseAt(new Date(Date.now() + 1000));
  await page.addInitScript(() => {
    const originalFetch = window.fetch;
    window.errorFixture = {posts:0, gets:0};
    window.fetch = async (url, options = {}) => {
      if (url.endsWith('/turns/stream')) {
        errorFixture.posts++;
        errorFixture.request = JSON.parse(options.body);
        // Tab storage must precede the first byte of the request.
        errorFixture.saved = JSON.parse(sessionStorage.getItem('chat-run-1'));
        if (errorFixture.mode === 'post403') return Response.json({detail:'Forbidden'}, {status:403});
        if (errorFixture.mode === 'ended') return Response.json({detail:'Session already ended'}, {status:400});
        if (errorFixture.mode === 'configuration') return Response.json({detail:{code:'configuration_unavailable'}}, {status:503});
        if (errorFixture.mode === 'capacity') return Response.json({detail:{code:'call_limit_reached'}}, {status:429});
        return new Response('', {headers:{'Content-Type':'text/event-stream'}});
      }
      if (url.startsWith('/runs/') || url.includes('/runs?')) {
        errorFixture.gets++;
        return Response.json({detail:'Forbidden'}, {status:errorFixture.mode === 'get401' ? 401 : 403});
      }
      return originalFetch(url, options);
    };
  });
  for (const mode of ['post403', 'get403', 'get401', 'ended', 'configuration', 'capacity']) {
    await page.goto(`${base}/scenarios`);
    await page.evaluate(() => sessionStorage.clear());
    await page.goto(`${base}/chat`);
    await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
    await page.evaluate(mode => { errorFixture.mode = mode; }, mode);
    await page.locator('#teacher-input').fill(`보존할 질문 ${mode}`);
    await page.locator('#teacher-input').press('Enter');
    await page.waitForFunction(() => errorFixture.posts === 1);
    if (mode === 'get401') await page.locator('#session-login-btn').waitFor({state:'visible'});
    else if (mode === 'ended') await page.waitForFunction(() => document.querySelector('#end-session-btn').dataset.state === 'ready-to-analyze');
    else if (mode === 'configuration') await page.waitForFunction(() => document.querySelector('.student-run-status').textContent.includes('관리자'));
    else if (mode === 'capacity') await page.waitForFunction(() => document.querySelector('.student-run-status').textContent.includes('잠시 후'));
    else await page.waitForFunction(() => document.querySelector('.student-run-status').textContent.includes('권한'));
    await page.clock.runFor(60000);
    const fixture = await page.evaluate(() => errorFixture);
    assert(fixture.posts === 1, `${mode}: no automatic generation`);
    assert(fixture.gets === (mode.startsWith('get') ? 1 : 0), `${mode}: auth/terminal error stops lookups`);
    assert(fixture.saved.request_id === fixture.request.request_id && fixture.saved.content === fixture.request.content, `${mode}: exact key and draft saved before POST`);
    assert(await page.locator('#teacher-input').isDisabled(), `${mode}: cannot send a new question`);
    assert(await page.locator('[data-message-id]').count() === 0, `${mode}: no unsaved answer marked completed`);
    assert(await page.evaluate(() => JSON.parse(sessionStorage.getItem('chat-run-1')).content) === `보존할 질문 ${mode}`, `${mode}: original tab draft retained`);
    if (mode === 'configuration' || mode === 'capacity') {
      const retry = page.getByRole('button', {name:'같은 요청 다시 전송', exact:true});
      assert(await retry.isEnabled(), `${mode}: explicit retry available`);
      const requestId = fixture.request.request_id;
      await retry.click();
      await page.waitForFunction(() => errorFixture.posts === 2);
      assert(await page.evaluate(() => errorFixture.request.request_id) === requestId, `${mode}: retry preserves unbound request ID`);
      assert(await page.evaluate(() => errorFixture.gets) === 0, `${mode}: pre-admission refusal does not poll`);
    }
  }
  assert(errors.length === 0, errors.join('; '));
  return {checks:['POST/lookup 403 and lookup 401 stop recovery without generation', 'ended-session POST locks the session without recovery', 'request key and draft persist before POST'], pageErrors:errors};
}
