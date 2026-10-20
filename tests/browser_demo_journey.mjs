import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {mkdir, readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
import {control, cookie, expectCode, namespace, open, ready, server, state} from './browser_demo_isolation.mjs';

// D4-02 (#204) A/B on the real app: the six-step journey (§24, §24.1, §22, §23) in context A, isolation of A's dialogue in context B (§28, §3).
const OUT = fileURLToPath(new URL('../test-results/d4-02/', import.meta.url));
const INPUT = {
  B: '같은 크기의 종이띠에서는 1/4이 1/8보다 커. 이 답을 기억해 봐.',
  common: '같은 크기의 종이띠 두 개를 각각 똑같이 4조각과 8조각으로 나누어 한 조각씩 겹쳐 보자. 어느 쪽 조각이 더 크고, 조각 수가 늘면 한 조각의 크기는 어떻게 될까?'
};
const ADMIN_COLUMNS = ['session_id', 'scenario_id', 'scenario_title', 'teacher_id', 'teacher_username', 'teacher_nickname', 'session_started_at', 'session_ended_at', 'message_id', 'message_created_at', 'role', 'content', 'label', 'confidence', 'meta_json', 'feedback', 'classification_status', 'student_name', 'snapshot_origin', 'snapshot_created_at', 'source_scenario_version', 'config_hash_kind', 'analysis_schema_version', 'analysis_status', 'analysis_coverage_json', 'misconception_findings_json', 'message_analysis_disposition'];
const DEMO_COLUMNS = ['demo_response_source', 'demo_external_ai_called', 'demo_content_version', 'demo_fixture_version', 'demo_record_origin'];
const IDENTITY = ['teacher_id', 'teacher_username', 'teacher_nickname'];
// §22 teacher-side paths of one session: history, analysis status, page and modal, run status, CSV.
const TEACHER_ROUTES = ids => [`/sessions/${ids.session}`, `/sessions/${ids.session}/analysis`, `/sessions/${ids.session}/analysis_page`,
  `/sessions/${ids.session}/analysis_modal`, `/sessions/${ids.session}/analysis/runs/${ids.analysisRun}`, `/sessions/${ids.session}/export.csv`];
const MAP_JS = `[...document.querySelectorAll('.demo-tour-steps li')].map(li => ({complete: 'C', current: 'X', pending: 'P'}[li.dataset.state] ?? '?')).join(' ')`;

async function until(fn, message, timeout = 60000) {
  const end = Date.now() + timeout;
  for (;;) {
    const value = await fn();
    if (value) return value;
    assert(Date.now() < end, `timeout: ${message}`);
    await new Promise(resolve => setTimeout(resolve, 100));
  }
}

// Polls across navigations: a reload can destroy the context mid-wait.
async function expectMap(page, want, label) {
  let seen;
  for (const end = Date.now() + 60000; Date.now() < end; await new Promise(resolve => setTimeout(resolve, 100))) {
    seen = await page.evaluate(MAP_JS).catch(error => `error: ${error.message}`);
    if (seen === want) return;
  }
  assert.equal(seen, want, `${label}: data-demo-step map`);
}

// §24: the DOM root carries the same namespace GET /demo/state reports.
async function expectRoot(page, label) {
  const [dom, current] = await Promise.all([page.evaluate(() => ({...document.documentElement.dataset})), state(page)]);
  assert.deepEqual([dom.demoMode, dom.demoScope, dom.demoGeneration, dom.demoAuthEpoch], ['demo', current.browser_scope_id, String(current.generation), String(current.auth_epoch)], `${label}: data-demo-* root`);
  return current;
}

// Retries across navigations: the users page reloads itself after the delete alert (users.html).
const facts = async page => (await until(() => page.evaluate(async () => (await fetch('/demo/state?include_tour=true')).json()).catch(() => null),
  'GET /demo/state?include_tour=true')).tour_facts;
const factsWhere = (page, key, message) => until(async () => { const f = await facts(page); return f?.[key] && f; }, message);
const nsOf = async page => namespace(await state(page));

function watch(page, pageErrors) {
  const log = {posts: [], dialogs: []};
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('request', request => { if (request.method() === 'POST') log.posts.push(new URL(request.url()).pathname); });
  page.on('dialog', dialog => { log.dialogs.push(dialog.type()); dialog.accept().catch(() => {}); });
  return log;
}

// RFC 4180 with a BOM strip: quoted commas, quotes and newlines survive.
function parseCsv(text) {
  const src = text.replace(/^﻿/, '');
  const rows = [];
  let row = [], field = '', quoted = false;
  for (let i = 0; i < src.length; i += 1) {
    const c = src[i];
    if (quoted) {
      if (c !== '"') field += c;
      else if (src[i + 1] === '"') { field += '"'; i += 1; } else quoted = false;
    } else if (c === '"') quoted = true;
    else if (c === ',') { row.push(field); field = ''; }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && src[i + 1] === '\n') i += 1;
      row.push(field); rows.push(row); row = []; field = '';
    } else field += c;
  }
  assert(!quoted, 'CSV: unterminated quote');
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  const [header, ...cells] = rows;
  return {header, rows: cells.map((cell, i) => {
    assert.equal(cell.length, header.length, `CSV row ${i + 1} cell count`);
    return Object.fromEntries(header.map((name, at) => [name, cell[at]]));
  })};
}

// §23: 27 admin + 5 suffix columns, the default two-turn session is 6 rows of one visitor_generated session.
function expectCsv(text, id, identity, label) {
  const {header, rows} = parseCsv(text);
  assert.deepEqual(header, [...ADMIN_COLUMNS, ...DEMO_COLUMNS], `${label}: header`);
  assert.deepEqual(rows.map(row => row.role).sort(), ['student', 'student', 'summary', 'teacher', 'teacher', 'tutor'], `${label}: 6 rows`);
  for (const row of rows) {
    assert.equal(row.session_id, String(id), `${label}: session_id`);
    assert.deepEqual([row.demo_response_source, row.demo_external_ai_called, row.demo_record_origin], ['prepared_example', 'false', 'visitor_generated'], `${label}: provenance`);
    for (const name of IDENTITY) assert.equal(row[name] !== '', identity, `${label}: ${name} ${identity ? 'present' : 'empty after teacher delete'}`);
  }
  assert.deepEqual(rows.filter(row => row.role === 'teacher').map(row => row.content).sort(), [INPUT.B, INPUT.common].sort(), `${label}: teacher turns`);
  return rows;
}

async function sendTurn(page, text, index) {
  await page.fill('#teacher-input', text);
  await page.click('#teacher-form button[type="submit"]');
  await page.locator('.message-student[data-message-id]').nth(index).waitFor();
  await page.locator('.mentor-slot[data-status="completed"]').nth(index).waitFor({state: 'attached'});
  await page.waitForFunction(() => !document.getElementById('teacher-input').disabled);
}

async function runChecks(browser, {base}, pageErrors, defects) {
  const contexts = {A: await open(browser), B: await open(browser)};
  const page = await contexts.A.newPage();
  const logA = watch(page, pageErrors);
  const pageB = await contexts.B.newPage();
  watch(pageB, pageErrors);
  await mkdir(OUT, {recursive: true});
  const soft = async (name, fn) => { try { await fn(); } catch (error) { defects.push(`${name}: ${error.message}`); } };

  // B: admin start; its totals, lists and session list must not move while A works (§28 row 1).
  await pageB.goto(`${base}/demo`);
  const started = await control(pageB, '/demo/start', {request_id: randomUUID(), role: 'admin'});
  assert.equal(started.status, 201, JSON.stringify(started.body));
  const stateB = await state(pageB);
  const bGet = path => contexts.B.request.get(`${base}${path}${path.includes('?') ? '&' : '?'}${namespace(stateB)}`);
  const view = async () => {
    const seen = {};
    for (const path of ['/metrics', '/admin/users', '/admin/groups', '/admin/sessions']) {
      const response = await bGet(path);
      assert.equal(response.status(), 200, `${path}: ${await response.text()}`);
      // demo-config's sliding idle expiry moves with every request (§8); /metrics also has uptime.
      seen[path] = path === '/metrics' ? (await response.json()).database : (await response.text()).replace(/"expires_at": "[^"]*"/g, '');
    }
    return seen;
  };
  const bBefore = await view();

  const ids = {};
  let failed = false;
  const step = async (name, fn) => {
    if (failed) return;
    try { await fn(); } catch (error) { failed = true; defects.push(`journey step ${name}: ${error.message}`); }
  };
  const A = contexts.A.request;
  const aUrl = async (path, extra = '') => `${base}${path}?${extra}${await nsOf(page)}`;

  await step('1 start as teacher', async () => {
    await page.goto(`${base}/demo`);
    await page.click('[data-demo-action="start"][data-demo-role="teacher"]');
    await page.waitForURL(url => url.pathname === '/scenarios');
    await expectRoot(page, 'step 1');
    await expectMap(page, 'C X P P P P', 'step 1');
    const f = await facts(page);
    assert.deepEqual([f.teacher_entered, f.scenario_created, f.dialogue_complete, f.analysis_ready, f.journey_session_id], [true, false, false, false, null], 'step 1: TourFacts');
    await page.screenshot({path: `${OUT}/journey-1-teacher.png`});
  });

  await step('2 scenario', async () => {
    await page.click('.scenario-card a.btn-primary');
    await page.waitForURL(/\/scenarios\/\d+/);
    await page.locator('#teacher-input').waitFor();
    assert.match(await page.locator('.greeting-message').innerText(), /준비된 학생봇 응답/, 'step 2: mentor welcome');
    assert.match(await page.locator('.scenario-prompt').innerText(), /종이띠/, 'step 2: problem text');
    const f = await factsWhere(page, 'scenario_created', 'scenario_created');
    ids.session = f.journey_session_id;
    assert(Number.isInteger(ids.session), 'step 2: journey_session_id');
    assert.equal(JSON.parse(await page.locator('#chat-config').textContent()).sessionId, ids.session);
    await expectMap(page, 'C C X P P P', 'step 2');
  });

  await step('3 dialogue', async () => {
    await sendTurn(page, INPUT.B, 0);
    assert.match(await page.locator('.message-student').first().locator('.message-bubble').innerText(), /8이 더 큰 수라서 왜 1\/8이 더 작은지는 모르겠어요/, 'step 3: prepared B reply');
    ids.request = await page.locator('.message-teacher').first().getAttribute('data-request-id');
    const run = await A.get(await aUrl(`/sessions/${ids.session}/runs`, `request_id=${ids.request}&`));
    assert.equal(run.status(), 200, `GET /sessions/${ids.session}/runs: ${await run.text()}`);
    ids.run = (await run.json()).run_id;
    await sendTurn(page, INPUT.common, 1);
    assert.match(await page.locator('.message-student').nth(1).locator('.message-bubble').innerText(), /1\/4 조각이 1\/8 조각보다 커요/, 'step 3: prepared common reply');
    assert.equal(await page.locator('.mentor-slot[data-message-id]').count(), 1, 'step 3: exactly one coaching');
    assert.equal(await page.locator('.mentor-slot').nth(1).getAttribute('data-message-id'), null, 'step 3: no intervention on the common turn');
    assert(await page.locator('.mentor-slot').nth(1).locator('p.message-meta').isVisible(), 'step 3: no-intervention note');
    await factsWhere(page, 'dialogue_complete', 'dialogue_complete');
    await page.reload(); // §25: a refresh re-syncs the map from the server facts
    await expectMap(page, 'C C C X P P', 'step 3');
    assert.equal(logA.dialogs.filter(type => type !== 'confirm').length, 0, 'step 1-3: no alert dialog');
  });

  const isolation = async label => {
    assert.deepEqual(await view(), bBefore, `${label}: B totals, users, groups and sessions unchanged`);
    for (const [name, path] of [
      ['session', `/admin/sessions/${ids.session}/download`], ['admin analysis', `/admin/sessions/${ids.session}/analysis`],
      ['run', `/runs/${ids.run}`], ['request run', `/sessions/${ids.session}/runs?request_id=${ids.request}`]
    ]) {
      await soft(`B: A's ${name} ${path} is 404 DEMO_RESOURCE_NOT_FOUND`, async () => {
        const response = await bGet(path);
        const text = await response.text();
        await expectCode(response, 404, 'DEMO_RESOURCE_NOT_FOUND');
        assert(!/종이띠|모르겠어요|prepared_example/.test(text), `A's content in the body: ${text}`);
      });
    }
  };

  // B's view of A's dialogue does not wait for the analysis steps.
  if (!failed) await soft('B isolation after A dialogue', () => isolation('after A dialogue'));

  await step('4 end, analysis, history', async () => {
    const modal = page.waitForResponse(response => response.url().includes(`/sessions/${ids.session}/analysis_modal`));
    modal.catch(() => {}); // awaited below; never an unhandled rejection when the end POST fails first
    const [ended] = await Promise.all([page.waitForResponse(response => response.url().endsWith(`/sessions/${ids.session}/end`)), page.click('#end-session-btn')]);
    assert([200, 202].includes(ended.status()), `POST /sessions/${ids.session}/end ${ended.status()}: ${await ended.text()}`);
    ids.analysisRun = (await ended.json()).run_id;
    assert(ids.analysisRun, 'step 4: analysis run_id');
    assert.equal((await modal).status(), 200, `GET /sessions/${ids.session}/analysis_modal: ${await (await modal).text()}`);
    await page.locator('#analysis-modal-container [data-analysis-result]').waitFor({state: 'attached'});
    await factsWhere(page, 'analysis_ready', 'analysis_ready');
    await page.goto(await aUrl(`/sessions/${ids.session}`)); // the history detail of the journey session
    await page.locator('[data-analysis-result] .coach-msg-list').waitFor({state: 'attached'});
    await expectMap(page, 'C C C C X P', 'step 4');
    await page.screenshot({path: `${OUT}/journey-4-history.png`});
  });

  await step('5 admin record and CSV', async () => {
    const before = await state(page);
    await page.click('.demo-tour-actions [data-demo-action="role"][data-demo-role="admin"]');
    await page.waitForURL(url => url.pathname === '/admin/scenarios');
    const admin = await expectRoot(page, 'step 5');
    assert.deepEqual([admin.role, admin.auth_epoch, admin.generation], ['admin', before.auth_epoch + 1, before.generation], 'step 5: role switch');
    await expectMap(page, 'C C C C X P', 'step 5 after the role switch');
    await page.click('a[href^="/admin/sessions-page"]');
    await page.waitForURL(url => url.pathname === '/admin/sessions-page');
    await page.click(`.view-analysis-btn[data-session-id="${ids.session}"]`);
    await until(() => page.evaluate(() => window.demoTour.snapshot().observations.some(o => o.kind === 'admin_record_viewed')), 'admin_record_viewed');
    const analysis = await A.get(await aUrl(`/admin/sessions/${ids.session}/analysis`));
    assert.equal(analysis.status(), 200, await analysis.text());
    // Close the record's analysis modal first: its overlay covers the page.
    await page.locator('#analysis-modal .close-modal-btn').click();
    await page.locator('#analysis-modal').waitFor({state: 'hidden'});
    const [download] = await Promise.all([page.waitForEvent('download'), page.click('#demo-tour-csv')]);
    // §23 pattern demo_d0-02-v1_<admin|teacher>_<session-ID|selected|all|user-ID>.csv.
    assert.equal(download.suggestedFilename(), `demo_d0-02-v1_admin_session-${ids.session}.csv`);
    await download.saveAs(`${OUT}/journey-admin.csv`);
    ids.teacher = expectCsv(await readFile(`${OUT}/journey-admin.csv`, 'utf8'), ids.session, true, 'downloaded CSV')[0].teacher_id;
    await expectMap(page, 'C C C C C X', 'step 5');
    await page.screenshot({path: `${OUT}/journey-5-admin.png`});
    assert.equal(logA.posts.filter(path => path === '/demo/role').length, 1, 'one POST /demo/role');
  });
  if (!failed) await soft('B isolation after A analysis and CSV', () => isolation('after A dialogue, analysis and CSV'));

  await step('6 delete teacher and session', async () => {
    const mark = logA.dialogs.length;
    await page.click('nav a[href="/admin"]');
    await page.click('a.action-card[href="/admin/users"]');
    await page.click(`.delete-btn[data-id="${ids.teacher}"]`);
    await until(() => logA.dialogs.length - mark >= 2, 'confirm and deleted notice');
    await factsWhere(page, 'teacher_deleted', 'teacher_deleted');
    assert.deepEqual(logA.dialogs.slice(mark), ['confirm', 'alert'], 'step 6: confirm then deleted notice');
    expectCsv(await (await A.get(await aUrl(`/admin/sessions/${ids.session}/download`))).text(), ids.session, false, 'CSV after teacher delete');
    // §22 / spec §8 step 2: the deleted teacher's per-user export does not hand back the preserved session.
    await expectCode(await A.get(await aUrl(`/admin/user-conversations/${ids.teacher}/export`)), 404, 'DEMO_RESOURCE_NOT_FOUND');
    // Spec §8 step 3: no teacher context remains, so the teacher routes below are probed as the visitor's admin.
    const current = await state(page);
    const role = await control(page, '/demo/role', {request_id: randomUUID(), role: 'teacher', expected_generation: current.generation, expected_auth_epoch: current.auth_epoch});
    assert.deepEqual([role.status, role.body?.detail?.code], [409, 'DEMO_ROLE_DELETED'], 'step 6: deleted teacher role');
    // Before the session delete the teacher routes refuse the admin as non-owner (403), so the 404s below come from the delete.
    for (const path of TEACHER_ROUTES(ids)) assert.equal((await A.get(await aUrl(path))).status(), 403, `before session delete: ${path}`);
    await page.goto(`${base}/admin/sessions-page`);
    const row = page.locator('tr', {has: page.locator(`input.session-checkbox[value="${ids.session}"]`)});
    await row.locator('button.btn-danger').click(); // hx-confirm
    await row.waitFor({state: 'detached'});
    await factsWhere(page, 'session_deleted', 'session_deleted');
  });
  if (!failed) {
    // §22: the deleted session is absent from every export, URL, API, modal and list.
    const id = ids.session;
    const gone = async (name, path, code = 'DEMO_RESOURCE_NOT_FOUND') => soft(`deleted session: ${name}`, async () => {
      const response = await A.get(await aUrl(path));
      if (code) await expectCode(response, 404, code); else assert.equal(response.status(), 404, await response.text());
    });
    await gone('single CSV', `/admin/sessions/${id}/download`);
    await gone('admin analysis API', `/admin/sessions/${id}/analysis`);
    await gone('admin analysis modal', `/admin/sessions/${id}/analysis_modal`, null);
    await gone('admin analysis run status', `/admin/sessions/${id}/analysis/runs/${ids.analysisRun}`, null);
    for (const path of TEACHER_ROUTES(ids)) await gone(`teacher route ${path}`, path, null);
    await gone('deleted teacher export', `/admin/user-conversations/${ids.teacher}/export`);
    await soft('deleted session: all export', async () => {
      const response = await A.get(await aUrl('/admin/sessions/export'));
      assert.equal(response.status(), 200);
      assert.deepEqual(parseCsv(await response.text()).rows.filter(row => row.session_id === String(id)), [], 'rows of the deleted session');
    });
    await soft('deleted session: selected export', async () => {
      const response = await A.post(await aUrl('/admin/sessions/export-selected'), {
        headers: {origin: base, 'x-csrf-token': await cookie(contexts.A, 'demo_csrftoken')}, form: {session_ids: String(id)}
      });
      const body = await response.text();
      // admin_session_export.py: no remaining selected session gives 200 plain text, not a CSV.
      assert.equal(response.status(), 200, body);
      assert.match(response.headers()['content-type'], /^text\/plain/, body);
      assert(!body.includes('session_id,'), 'selected export must carry no CSV');
    });
    await soft('deleted session: list API and page', async () => {
      const response = await A.get(await aUrl('/admin/sessions'));
      assert.equal(response.status(), 200, await response.text());
      const list = await response.json();
      assert(Array.isArray(list.sessions) && Number.isInteger(list.total), `GET /admin/sessions shape: ${JSON.stringify(list)}`);
      assert(list.sessions.length > 0, 'GET /admin/sessions keeps the seed session');
      assert.deepEqual(list.sessions.filter(row => row.id === id), [], 'GET /admin/sessions lists it');
      await page.goto(`${base}/admin/sessions-page`);
      assert.equal(await page.locator(`input.session-checkbox[value="${id}"], .view-analysis-btn[data-session-id="${id}"]`).count(), 0, 'sessions page lists it');
    });
  }

  await step('7 reset', async () => {
    await page.goto(`${base}/admin/sessions-page`);
    await expectMap(page, 'C C C C C X', 'step 6 before reset');
    const old = await contexts.A.newPage();
    const logOld = watch(old, pageErrors);
    await old.goto(`${base}/admin/sessions-page`);
    const previous = await state(old);
    // window.confirm is accepted by the dialog handler; ready() reads the state, so wait for the reset answer first.
    const [reset] = await Promise.all([page.waitForResponse(response => new URL(response.url()).pathname === '/demo/reset'),
      page.click('.demo-tour-actions [data-demo-action="reset"]')]);
    assert([200, 202].includes(reset.status()), `POST /demo/reset ${reset.status()}`);
    const fresh = await ready(page, previous.generation + 1);
    assert.equal(logA.posts.filter(path => path === '/demo/reset').length, 1, 'one POST /demo/reset');
    await expectMap(page, 'C C C C C C', 'reset success shows the old journey complete once (§24.1 row 14a)');
    // §9.1/§10: the previous namespace is refused for the old tab and for the old URL of the journey session.
    const stale = await control(old, '/demo/role', {request_id: randomUUID(), role: 'admin', expected_generation: previous.generation, expected_auth_epoch: previous.auth_epoch});
    assert.equal(stale.status, 409, JSON.stringify(stale.body));
    assert.equal(stale.body.detail.code, 'DEMO_STALE_GENERATION');
    await expectCode(await A.get(`${base}/admin/sessions/${ids.session}/download?${namespace(previous)}`), 409, 'DEMO_STALE_GENERATION');
    assert.equal(logOld.posts.length, 1);
    await page.reload();
    const now = await expectRoot(page, 'after reset');
    assert.notEqual(now.generation, previous.generation);
    assert.equal(now.generation, fresh.generation);
    const f = await facts(page);
    assert.deepEqual([f.journey_session_id, f.scenario_created, f.dialogue_complete, f.analysis_ready, f.teacher_deleted, f.session_deleted], [null, false, false, false, false, false], 'new generation TourFacts');
    await expectMap(page, now.role === 'teacher' ? 'C X P P P P' : 'X P P P P P', 'after reset');
    await page.screenshot({path: `${OUT}/journey-7-reset.png`});
  });
  if (!failed) assert.deepEqual(await view(), bBefore, 'after A reset: B unchanged');
  for (const context of Object.values(contexts)) await context.close();
}

export default async function check() {
  const pageErrors = [];
  const defects = [];
  const demo = await server();
  let browser;
  try {
    console.log(`RECEIPT ${JSON.stringify(demo.receipt)}`);
    browser = await chromium.launch({headless: true});
    await runChecks(browser, demo, pageErrors, defects);
  } finally {
    await browser?.close();
    await demo.stop();
  }
  assert.deepEqual(defects, [], 'product defects');
  return {pageErrors};
}
