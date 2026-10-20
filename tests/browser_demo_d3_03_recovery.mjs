import assert from 'node:assert/strict';

// §10/§24/§25 plumbing against an injected #demo-config; no real demo backend runs here.
const DEMO = {
  mode:'demo', browser_scope_id:'scope-a', generation:3, auth_epoch:2, state:'ready', role:'teacher',
  csrf_cookie_name:'demo_csrftoken', csrf_header_name:'x-csrf-token',
};

async function open(page, base, config, oldDraft, path = '/chat?stream') {
  await page.unrouteAll();
  if (config) {
    await page.route(url => url.pathname === new URL(path, base).pathname, async route => {
      const response = await route.fetch();
      const body = (await response.text()).replace('<script src="/static/js/display-language.js">',
        `<script type="application/json" id="demo-config">${JSON.stringify(config)}</script>` +
        '<script src="/static/js/display-language.js">');
      await route.fulfill({response, body});
    });
  }
  await page.goto(`${base}${path}`);
  if (!path.startsWith('/chat')) return null;
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  const id = await page.evaluate(() => window.currentSessionId);
  if (oldDraft) {
    await page.evaluate(([key, value]) => sessionStorage.setItem(key, value), [`chat-draft-${id}`, oldDraft]);
    await page.reload();
    await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  }
  return id;
}

// Earlier sections leave saved runs in the same namespace; these cases start without them.
async function fresh(page, base, config, path) {
  // Leave the chat first: its pagehide handler saves the pending run again.
  await page.goto(`${base}/login`);
  await page.evaluate(() => sessionStorage.clear());
  return open(page, base, config, null, path);
}

// Holds a response until release(); an aborted request makes fulfill reject, which is expected.
async function hold(page, pattern, response) {
  let release;
  const released = new Promise(resolve => { release = resolve; });
  await page.route(pattern, async route => {
    await released;
    await route.fulfill(response).catch(() => {});
  });
  return () => release();
}

const synthetic = (page, type, detail) => page.evaluate(([type, detail]) => {
  const event = new CustomEvent(type, {detail});
  event.synthetic = true;
  document.dispatchEvent(event);
}, [type, detail]);

function snapshot(page) {
  return page.evaluate(() => ({
    token:uiLanguage.csrfToken(),
    analysis:getAnalysisCsrfHeaders(),
    same:Object.fromEntries(uiLanguage.headers('/sessions/1', {'Content-Type':'application/json'})),
    cross:Object.fromEntries(uiLanguage.headers('https://example.com/x', {'X-Demo-Scope':'leak', 'X-UI-Language':'ko'})),
    key:uiLanguage.storageKey('chat-run-7'),
  }));
}

const envelope = (code, recovery, retryable = false) =>
  JSON.stringify({detail:{code, retryable, recovery, generation:null, auth_epoch:null}});
const sse = events => events.map(([type, data]) => `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`).join('');
const run = {run_id:'run-1', turn_id:'turn-1', turn_index:1};
const CSV_HEAD = ['session_id', 'scenario_id', 'scenario_title', 'teacher_id', 'teacher_username', 'teacher_nickname',
  'session_started_at', 'session_ended_at', 'message_id', 'message_created_at', 'role', 'content', 'label', 'confidence',
  'meta_json', 'feedback', 'classification_status', 'student_name', 'snapshot_origin', 'snapshot_created_at',
  'source_scenario_version', 'config_hash_kind', 'analysis_schema_version', 'analysis_status', 'analysis_coverage_json',
  'misconception_findings_json', 'message_analysis_disposition', 'demo_response_source', 'demo_external_ai_called',
  'demo_content_version', 'demo_fixture_version', 'demo_record_origin'];
function csvRow(session, role, content) {
  const row = Object.fromEntries(CSV_HEAD.map(name => [name, '']));
  Object.assign(row, {session_id:String(session), role, content, demo_response_source:'prepared_example',
    demo_external_ai_called:'false', demo_content_version:'c1', demo_fixture_version:'f1', demo_record_origin:'visitor_generated'});
  return CSV_HEAD.map(name => /[",\n]/.test(row[name]) ? `"${row[name].replace(/"/g, '""')}"` : row[name]).join(',');
}

export default async function check(page) {
  const base = new URL(page.url()).origin;
  // Every demo:* document event the page emits, to prove #201 sends only state-check/tour-observed.
  await page.addInitScript(() => {
    window.demoEvents = [];
    for (const type of ['demo:state-check', 'demo:tour-observed', 'demo:state', 'demo:role-changed',
      'demo:reset-started', 'demo:reset-completed', 'demo:expired']) {
      document.addEventListener(type, event => { if (!event.synthetic) demoEvents.push([type, event.detail]); }, true);
    }
  });
  const events = () => page.evaluate(() => demoEvents);
  const text = key => page.evaluate(key => uiLanguage.t(key), key);
  const requests = [];
  page.on('request', request => requests.push(`${request.method()} ${new URL(request.url()).pathname}`));
  const count = prefix => requests.filter(item => item.startsWith(prefix)).length;
  await page.context().addCookies([
    {name:'csrftoken', value:'normal-token', url:base}, {name:'demo_csrftoken', value:'demo-token', url:base},
  ]);

  // Normal: absent element and an explicit mode:"normal" keep the legacy names, headers and keys.
  for (const config of [null, {mode:'normal', public_url:null}]) {
    await open(page, base, config);
    assert.deepEqual(await snapshot(page), {
      token:'normal-token', analysis:{'x-csrf-token':'normal-token'},
      same:{'content-type':'application/json', 'x-ui-language':'ko'}, cross:{'x-demo-scope':'leak'}, key:'chat-run-7',
    });
  }

  // Demo before a visit exists: demo cookie name, but no namespace headers or prefixes.
  await open(page, base, {...DEMO, browser_scope_id:null, generation:null, auth_epoch:null});
  const pending = await snapshot(page);
  assert.equal(pending.token, 'demo-token');
  assert.deepEqual(pending.same, {'content-type':'application/json', 'x-ui-language':'ko'});
  assert.equal(pending.key, 'chat-run-7');

  // Demo visit: CSRF from the configured cookie, namespace only on same-origin requests.
  const id = await open(page, base, DEMO, 'old draft');
  const namespaced = {'x-demo-scope':'scope-a', 'x-demo-generation':'3', 'x-demo-auth-epoch':'2'};
  assert.deepEqual(await snapshot(page), {
    token:'demo-token', analysis:{'x-csrf-token':'demo-token'},
    same:{'content-type':'application/json', 'x-ui-language':'ko', ...namespaced}, cross:{},
    key:'demo:scope-a:3:2:chat-run-7',
  });

  // The un-prefixed draft is never migrated; new input lands under the namespaced key only.
  const input = page.locator('#teacher-input');
  assert.notEqual(await input.inputValue(), 'old draft');
  await input.fill('새 초안');
  assert.equal(await page.evaluate(key => sessionStorage.getItem(key), `demo:scope-a:3:2:chat-draft-${id}`), '새 초안');
  assert.equal(await page.evaluate(key => sessionStorage.getItem(key), `chat-draft-${id}`), 'old draft');

  // A real stream POST and an htmx request carry the namespace and the demo CSRF value.
  await page.route('**/turns/stream', route => route.fulfill({status:503, contentType:'application/json', body:'{}'}));
  const [stream] = await Promise.all([page.waitForRequest('**/turns/stream'), input.press('Enter')]);
  assert.equal(stream.headers()['x-csrf-token'], 'demo-token');
  for (const [name, value] of Object.entries(namespaced)) assert.equal(stream.headers()[name], value);
  const [fragment] = await Promise.all([
    page.waitForRequest('**/about/content*'),
    page.evaluate(() => htmx.ajax('GET', '/about/content?empty=1', {target:document.createElement('div')})),
  ]);
  for (const [name, value] of Object.entries(namespaced)) assert.equal(fragment.headers()[name], value);

  // Authoritative events move the namespace forward in the same scope; older or foreign ones are ignored.
  const after = detail => page.evaluate(([type, detail]) => {
    document.dispatchEvent(new CustomEvent(type, {detail}));
    return uiLanguage.storageKey('k');
  }, detail);
  assert.equal(await after(['demo:state', {browser_scope_id:'scope-a', generation:4, auth_epoch:0}]), 'demo:scope-a:4:0:k');
  assert.equal(await after(['demo:reset-completed', {browser_scope_id:'scope-a', generation:3, auth_epoch:9}]), 'demo:scope-a:4:0:k');
  assert.equal(await after(['demo:state', {browser_scope_id:'scope-b', generation:9, auth_epoch:9}]), 'demo:scope-a:4:0:k');
  assert.equal(await after(['demo:expired', {browser_scope_id:'scope-a', generation:8, auth_epoch:8}]), 'demo:scope-a:4:0:k');
  assert.equal(await after(['demo:role-changed', {browser_scope_id:'scope-a', generation:4, auth_epoch:1}]), 'demo:scope-a:4:1:k');
  assert.equal(await page.evaluate(() => uiLanguage.headers('/x').get('X-Demo-Generation')), '4');

  // The content-free info-tab preference keeps its un-prefixed key across an auth_epoch change.
  await page.evaluate(() => document.querySelector('.info-tab[data-tab="guide"]').click());
  await open(page, base, {...DEMO, auth_epoch:3});
  assert.equal(await page.evaluate(() => uiLanguage.storageKey('k')), 'demo:scope-a:3:3:k');
  assert.equal(await page.evaluate(id => sessionStorage.getItem(`chat-info-tab-${id}`), id), 'guide');
  assert.equal(await page.locator('.info-tab.active').getAttribute('data-tab'), 'guide');

  // Change 3: a 410 envelope stops the stream, asks #199 once, and never goes to /login or resends.
  await fresh(page, base, DEMO);
  await page.route('**/turns/stream', route => route.fulfill({status:410, contentType:'application/json',
    body:envelope('DEMO_EXPIRED', 'demo.expired')}));
  requests.length = 0;
  await page.locator('#teacher-input').fill('질문');
  await page.locator('#teacher-input').press('Enter');
  const expired = await text('demo.expired');
  await page.locator('#session-status-text').getByText(expired, {exact:true}).waitFor({timeout:3000});
  await page.waitForTimeout(300);
  assert.deepEqual(await events(), [['demo:state-check', {reason:'DEMO_EXPIRED'}]]);
  assert.equal(new URL(page.url()).pathname, '/chat');
  assert.equal(await page.locator('#session-login-btn').isVisible(), false);
  assert.equal(await page.locator('#language-notice').textContent(), expired);
  assert.equal(count('POST /sessions/1/turns/stream'), 1);
  assert.equal(count('GET /runs') + count('GET /sessions/1/runs'), 0);

  // §11: an SSE terminal recovery key is shown; a non-stale code asks for no state check.
  await fresh(page, base, DEMO);
  await page.route('**/turns/stream', route => route.fulfill({contentType:'text/event-stream', body:sse([
    ['run.accepted', {...run, seq:0, request_id:route.request().postDataJSON().request_id, teacher_message_id:10, status:'running'}],
    ['run.failed', {...run, seq:1, status:'failed', code:'DEMO_LIMIT_REACHED', retryable:false, message:'',
      recovery:'demo.limit_reached', browser_scope_id:'scope-a', generation:3, auth_epoch:2}],
  ])}));
  await page.locator('#teacher-input').fill('질문');
  await page.locator('#teacher-input').press('Enter');
  const limit = await text('demo.limit_reached');
  await page.locator('.student-run-status').filter({hasText:limit}).waitFor({timeout:3000});
  assert.deepEqual(await events(), []);
  assert.equal(await page.locator('.student-run-status + div button').first().isDisabled(), true);

  // §25: a frame from another namespace is discarded and treated as stale.
  await fresh(page, base, DEMO);
  await page.route('**/turns/stream', route => route.fulfill({contentType:'text/event-stream', body:sse([
    ['run.accepted', {...run, seq:0, request_id:route.request().postDataJSON().request_id, teacher_message_id:10, status:'running'}],
    ['run.failed', {...run, seq:1, status:'failed', code:'transient', retryable:true, message:'',
      recovery:'demo.retry_later', browser_scope_id:'scope-a', generation:2, auth_epoch:2}],
  ])}));
  requests.length = 0;
  await page.locator('#teacher-input').fill('질문');
  await page.locator('#teacher-input').press('Enter');
  const staleText = await text('demo.stale_generation');
  await page.locator('#session-status-text').getByText(staleText, {exact:true}).waitFor({timeout:3000});
  await page.waitForTimeout(300);
  assert.deepEqual(await events(), [['demo:state-check', {reason:'DEMO_STALE_GENERATION'}]]);
  assert.equal(await page.locator('.student-run-status').filter({hasText:await text('demo.retry_later')}).count(), 0);
  assert.equal(count('GET /runs') + count('GET /sessions/1/runs'), 0);

  // §25: a role change aborts the pending lookup and its timer; nothing reconnects.
  await fresh(page, base, DEMO);
  await page.route('**/turns/stream', route => route.fulfill({contentType:'text/event-stream', body:sse([
    ['run.accepted', {...run, seq:0, request_id:route.request().postDataJSON().request_id, teacher_message_id:10, status:'running'}],
  ])}));
  await page.route('**/runs/run-1', route => route.fulfill({contentType:'application/json', body:JSON.stringify({
    ...run, operation:'student', status:'running', request_id:'unused', teacher_message_id:10})}));
  requests.length = 0;
  await page.locator('#teacher-input').fill('질문');
  await page.locator('#teacher-input').press('Enter');
  await page.waitForRequest('**/runs/run-1');
  await page.evaluate(() => {
    const event = new CustomEvent('demo:role-changed', {detail:{browser_scope_id:'scope-a', generation:3, auth_epoch:3}});
    event.synthetic = true;
    document.dispatchEvent(event);
  });
  const reads = count('GET /runs/run-1');
  await page.waitForTimeout(1500);
  assert.equal(count('GET /runs/run-1'), reads);
  assert.equal(await page.locator('#session-status-text').textContent(), await text('demo.stale_role'));
  assert.deepEqual(await events(), []);

  // §25: after BFCache the saved run is only looked up once #199 reports the state; never re-posted.
  await fresh(page, base, DEMO);
  await page.route('**/turns/stream', route => route.fulfill({contentType:'text/event-stream', body:sse([
    ['run.accepted', {...run, seq:0, request_id:route.request().postDataJSON().request_id, teacher_message_id:10, status:'running'}],
  ])}));
  await page.route('**/runs/run-1', route => route.fulfill({status:500, contentType:'application/json', body:'{}'}));
  await page.locator('#teacher-input').fill('질문');
  await page.locator('#teacher-input').press('Enter');
  await page.locator('.student-run-status').filter({hasText:await text('teacher.student_lookup_failed')}).waitFor({timeout:3000});
  requests.length = 0;
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true})));
  await page.waitForTimeout(300);
  assert.equal(count('GET /runs/run-1'), 0);
  await Promise.all([page.waitForRequest('**/runs/run-1'), page.evaluate(() => {
    const event = new CustomEvent('demo:state', {detail:{browser_scope_id:'scope-a', generation:3, auth_epoch:2}});
    event.synthetic = true;
    document.dispatchEvent(event);
  })]);
  assert.equal(count('POST /sessions/1/turns/stream'), 0);

  // §24.1: opening the journey target's analysis reports history_viewed; another session reports nothing.
  const observed = async () => (await events()).filter(([type]) => type === 'demo:tour-observed').map(([, detail]) => detail);
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  for (const journey of [1, 2]) {
    await open(page, base, {...DEMO, tour_facts:{journey_session_id:journey}}, null, '/fixtures/s4/analysis');
    await page.locator('[data-analysis-result] h2').first().waitFor({timeout:3000});
    const views = await observed();
    assert.equal(views.length, journey === 1 ? 1 : 0);
    if (journey === 1) {
      const [{request_id, ...detail}] = views;
      assert.match(request_id, uuid);
      assert.deepEqual(detail, {schema_version:1, browser_scope_id:'scope-a', generation:3, auth_epoch:2,
        journey_session_id:1, kind:'history_viewed'});
    }
  }

  // §24.1: the admin record of the journey target reports admin_record_viewed.
  const ADMIN = {...DEMO, role:'admin', content_version:'c1', fixture_version:'f1', tour_facts:{journey_session_id:1}};
  await open(page, base, ADMIN, null, '/fixtures/s4/history');
  await page.route('**/admin/sessions/1/analysis_modal', route => route.fulfill({contentType:'text/html',
    body:'<div class="modal-header"><button type="button" class="close-modal-btn">x</button></div>'}));
  await page.locator('.view-analysis-btn').first().click();
  await page.waitForFunction(() => demoEvents.some(([type, detail]) => detail?.kind === 'admin_record_viewed'));

  // §24.1 S5: csv_received only for the echoed, fully verified one-session export.
  const csv = ['\ufeff' + CSV_HEAD.join(','), csvRow(1, 'teacher', '줄바꿈\n"인용", 쉼표'), csvRow(1, 'summary', '요약')].join('\r\n') + '\r\n';
  for (const [name, echo, rows, body, expected] of [
    ['valid', true, '2', csv, 1], ['echo mismatch', false, '2', csv, 0], ['row count', true, '3', csv, 0],
    ['other session', true, '2', csv.replace(/\r\n1,/g, '\r\n2,'), 0], ['broken quote', true, '2', csv.replace('"인용"', '"인용'), 0],
    ['prefix header', true, '2', csv.replace('scenario_title', 'scenario_name'), 0],
    ['external source', true, '2', csv.replaceAll('prepared_example', 'external_provider'), 0],
    ['content version', true, '2', csv.replaceAll(',c1,f1,', ',c0,f1,'), 0],
    ['fixture version', true, '2', csv.replaceAll(',c1,f1,', ',c1,f0,'), 0],
  ]) {
    await open(page, base, ADMIN, null, '/fixtures/s4/history');
    let exportId;
    await page.route('**/admin/sessions/export-selected', route => {
      exportId = route.request().headers()['x-demo-export-request-id'];
      route.fulfill({contentType:'text/csv; charset=utf-8', body, headers:{
        'X-Demo-Export-Request-ID':echo ? exportId : crypto.randomUUID(), 'X-Demo-Export-Row-Count':rows}});
    });
    await page.locator('.session-checkbox').first().check();
    await Promise.all([page.waitForEvent('download'), page.locator('#btn-download-selected').click()]);
    await page.waitForFunction(() => !document.querySelector('#btn-download-selected').disabled);
    const received = (await observed()).filter(detail => detail.kind === 'csv_received');
    assert.equal(received.length, expected, name);
    assert.match(exportId, uuid);
    if (expected) assert.equal(received[0].request_id, exportId);
  }

  // §20: an admin refusal shows its recovery key once, with no automatic retry.
  await open(page, base, ADMIN, null, '/admin/ai');
  await page.route('**/admin/ai/state', route => route.fulfill({status:403, contentType:'application/json',
    body:envelope('DEMO_ACTION_FORBIDDEN', 'demo.action_forbidden')}));
  requests.length = 0;
  await page.reload();
  await page.getByText(await text('demo.action_forbidden')).first().waitFor({timeout:3000});
  await page.waitForTimeout(300);
  assert.equal(count('GET /admin/ai/state'), 1);
  assert.deepEqual(await events(), []);

  // Repair P1-1: an invalidated analysis view stays dead; its actions are disabled and never POST.
  await open(page, base, DEMO, null, '/fixtures/s4/analysis?state=failed');
  const retryAnalysis = page.locator('[data-analysis-result] button').filter({hasText:await text('teacher.retry_analysis')});
  await retryAnalysis.waitFor({timeout:3000});
  requests.length = 0;
  await synthetic(page, 'demo:role-changed', {browser_scope_id:'scope-a', generation:3, auth_epoch:3});
  assert.equal(await page.locator('[data-analysis-result] button:not([disabled])').count(), 0);
  await retryAnalysis.evaluate(button => { button.disabled = false; button.click(); });
  await page.waitForTimeout(300);
  assert.equal(count('POST /sessions/1/analyze'), 0);
  assert.equal(await page.locator('.analysis-result-notice').textContent(), await text('demo.stale_role'));

  // Repair P1-1: reset-started keeps the namespace but voids the pending first GET: no render, no observation.
  await open(page, base, {...DEMO, tour_facts:{journey_session_id:1}}, null, '/fixtures/s4/analysis');
  let release = await hold(page, '**/fixtures/s4/result*', {contentType:'application/json', body:'{}'});
  await Promise.all([page.waitForRequest('**/fixtures/s4/result*'), page.reload()]);
  await synthetic(page, 'demo:reset-started', {browser_scope_id:'scope-a', generation:3, auth_epoch:2});
  release();
  await page.waitForTimeout(300);
  assert.equal(await page.locator('[data-analysis-result] h2').count(), 0);
  assert.equal(await page.locator('[data-analysis-result] p').count(), 0);
  assert.deepEqual(await observed(), []);

  // Repair P2-4: the analysis_busy follow-up refusal asks #199 once, drops the resend and ends the view.
  await open(page, base, DEMO, null, '/fixtures/s4/analysis?state=failed');
  await page.route('**/sessions/1/analyze', route => route.fulfill({status:409, contentType:'application/json',
    body:JSON.stringify({detail:{code:'analysis_busy', run_id:'r1'}})}));
  await page.route('**/sessions/1/analysis/runs/r1', route => route.fulfill({status:410, contentType:'application/json',
    body:envelope('DEMO_EXPIRED', 'demo.expired')}));
  await retryAnalysis.waitFor({timeout:3000});
  requests.length = 0;
  await retryAnalysis.click();
  await page.locator('.analysis-result-notice').getByText(expired, {exact:true}).waitFor({timeout:3000});
  assert.deepEqual(await events(), [['demo:state-check', {reason:'DEMO_EXPIRED'}]]);
  assert.equal(count('POST /sessions/1/analyze'), 1);
  assert.equal(count('GET /sessions/1/analysis/runs/r1'), 1);
  assert.equal(await page.locator('[data-analysis-result] button').filter({hasText:await text('teacher.resend_request')}).count(), 0);
  assert.equal(await page.locator('[data-analysis-result] button:not([disabled])').count(), 0);

  // Repair P1-2: an end/analyze POST pending across a role change never opens the modal or fetches more.
  await fresh(page, base, DEMO);
  release = await hold(page, '**/sessions/1/end', {contentType:'application/json', body:JSON.stringify({status:'ended'})});
  page.once('dialog', dialog => dialog.accept());
  requests.length = 0;
  await Promise.all([page.waitForRequest('**/sessions/1/end'), page.locator('#end-session-btn').click()]);
  await synthetic(page, 'demo:role-changed', {browser_scope_id:'scope-a', generation:3, auth_epoch:3});
  release();
  await page.waitForTimeout(500);
  assert.equal(await page.locator('#session-status-text').textContent(), await text('demo.stale_role'));
  assert.equal(await page.locator('#analysis-modal-overlay').isHidden(), true);
  assert.equal(count('GET /sessions/1/analysis_modal'), 0);

  // Repair P1-2: a selected export pending across a role change starts no download.
  await open(page, base, ADMIN, null, '/fixtures/s4/history');
  release = await hold(page, '**/admin/sessions/export-selected', {contentType:'text/csv; charset=utf-8', body:csv});
  await page.locator('.session-checkbox').first().check();
  let downloads = 0;
  page.on('download', () => { downloads++; });
  await Promise.all([page.waitForRequest('**/admin/sessions/export-selected'), page.locator('#btn-download-selected').click()]);
  await synthetic(page, 'demo:role-changed', {browser_scope_id:'scope-a', generation:3, auth_epoch:3});
  release();
  await page.getByText(await text('demo.stale_generation')).first().waitFor({timeout:3000});
  assert.equal(downloads, 0);
  assert.deepEqual(await observed(), []);

  // Repair P1-2: an AI-settings GET pending across a role change does not render.
  await open(page, base, ADMIN, null, '/admin/ai');
  release = await hold(page, '**/admin/ai/state', {status:403, contentType:'application/json', body:'{}'});
  await Promise.all([page.waitForRequest('**/admin/ai/state'), page.reload()]);
  await synthetic(page, 'demo:role-changed', {browser_scope_id:'scope-a', generation:3, auth_epoch:3});
  release();
  await page.getByText(await text('demo.stale_generation')).first().waitFor({timeout:3000});

  // Repair P1-3: JSON run receipts need the sent request ID, the current namespace and no historical flag.
  const completed = {...run, operation:'student', status:'completed', result_kind:'message', teacher_message_id:10,
    message:{id:11, role:'student', content:'오래된 응답'}};
  for (const [name, receipt, stale] of [
    ['other request', () => ({request_id:crypto.randomUUID()}), false],
    ['other namespace', id => ({request_id:id, browser_scope_id:'scope-a', generation:2, auth_epoch:2}), true],
    ['historical', id => ({request_id:id, historical:true}), true],
  ]) {
    await fresh(page, base, DEMO);
    await page.route('**/turns/stream', route => route.fulfill({contentType:'application/json',
      body:JSON.stringify({...completed, ...receipt(route.request().postDataJSON().request_id)})}));
    await page.locator('#teacher-input').fill('질문');
    await page.locator('#teacher-input').press('Enter');
    const notice = stale ? await text('demo.stale_generation') : await text('demo.refresh_state');
    await page.locator('#language-notice').getByText(notice, {exact:true}).waitFor({timeout:3000});
    await page.waitForTimeout(200);
    assert.equal(await page.getByText('오래된 응답').count(), 0, name);
    assert.deepEqual(await events(), stale ? [['demo:state-check', {reason:'DEMO_STALE_GENERATION'}]] : [], name);
  }
  await fresh(page, base, DEMO, '/chat?stream&mentor=1');
  await page.route('**/turns/stream', route => route.fulfill({contentType:'text/event-stream', body:sse([
    ['run.accepted', {...run, seq:0, request_id:route.request().postDataJSON().request_id, teacher_message_id:10, status:'running'}],
    ['output.completed', {...run, seq:1, status:'completed', message:{id:11, role:'student', content:'학생 응답'}}],
  ])}));
  await page.route('**/mentor/stream', route => route.fulfill({contentType:'application/json', body:JSON.stringify({
    run_id:'mentor-1', turn_id:'turn-1', turn_index:1, request_id:crypto.randomUUID(), operation:'mentor',
    teacher_message_id:10, status:'completed', result_kind:'no_intervention', message:null})}));
  await page.locator('#teacher-input').fill('질문');
  await page.locator('#teacher-input').press('Enter');
  await page.locator('.mentor-slot .mentor-run-status').getByText(await text('demo.refresh_state'), {exact:true}).waitFor({timeout:3000});
  assert.equal(await page.locator('.mentor-slot').getByText(await text('teacher.mentor_no_intervention'), {exact:true}).count(), 0);
  return {pageErrors:[]};
}
