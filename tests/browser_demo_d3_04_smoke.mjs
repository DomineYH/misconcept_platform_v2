import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {randomBytes, randomUUID} from 'node:crypto';
import {mkdir, mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import {createServer} from 'node:net';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {fileURLToPath} from 'node:url';

// D3-04 (#202) six-step smoke on the real app: its own demo + normal uvicorn with
// TESTING=false and the #197 seed CLI; the runner's fixture page is never evidence (§28).
const ROOT = fileURLToPath(new URL('../', import.meta.url));
const OUT = join(ROOT, 'test-results', 'd3-04');
// #197 manifest seed_refs (src/demo/fixtures/manifest.json).
const SEED = {admin: 1, teacher: 2, session: 1};
// D0-02 §4: the default B branch, then the common second turn.
const INPUT = {
  B: '같은 크기의 종이띠에서는 1/4이 1/8보다 커. 이 답을 기억해 봐.',
  common: '같은 크기의 종이띠 두 개를 각각 똑같이 4조각과 8조각으로 나누어 한 조각씩 겹쳐 보자. 어느 쪽 조각이 더 크고, 조각 수가 늘면 한 조각의 크기는 어떻게 될까?'
};
// §23: admin prefix 27 + demo suffix 5, in order.
const COLUMNS = ['session_id', 'scenario_id', 'scenario_title', 'teacher_id', 'teacher_username', 'teacher_nickname',
  'session_started_at', 'session_ended_at', 'message_id', 'message_created_at', 'role', 'content', 'label', 'confidence',
  'meta_json', 'feedback', 'classification_status', 'student_name', 'snapshot_origin', 'snapshot_created_at',
  'source_scenario_version', 'config_hash_kind', 'analysis_schema_version', 'analysis_status', 'analysis_coverage_json',
  'misconception_findings_json', 'message_analysis_disposition',
  'demo_response_source', 'demo_external_ai_called', 'demo_content_version', 'demo_fixture_version', 'demo_record_origin'];
const SUFFIX = ['prepared_example', 'false', 'd0-02-v1', 'demo-fixture-v1'];
const INHERITED = /^(DEMO_|OPENAI_|ANTHROPIC_|AZURE_|GOOGLE_|GEMINI_)/;
const RESET = ['APP_MODE', 'DATABASE_URL', 'TESTING', 'SESSION_SECRET', 'ENV', 'BOOTSTRAP_ADMIN_ON_STARTUP',
  'ADMIN_DEFAULT_PASSWORD', 'PROVIDER_SECRET_ENCRYPTION_KEY', 'PROVIDER_SECRET_ENCRYPTION_KEY_VERSION'];
const secret = () => randomBytes(32).toString('hex');

async function freePort() {
  const listener = createServer();
  await new Promise((resolve, reject) => {
    listener.once('error', reject);
    listener.listen(0, '127.0.0.1', resolve);
  });
  const {port} = listener.address();
  await new Promise(resolve => listener.close(resolve));
  return port;
}

function environment(values) {
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !INHERITED.test(key) && !RESET.includes(key)));
  return {...env, ENV: 'development', TESTING: 'false', SESSION_SECRET: secret(), ...values};
}

function run(args, env) {
  const child = spawn('uv', ['run', '--frozen', 'python', ...args], {cwd: ROOT, env, stdio: ['ignore', 'pipe', 'pipe']});
  let out = '', err = '';
  child.stdout.on('data', chunk => { out += chunk; });
  child.stderr.on('data', chunk => { err = (err + chunk).slice(-8000); });
  return new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('close', code => code === 0 ? resolve(out) : reject(new Error(`${args.join(' ')} exited ${code}: ${err}`)));
  });
}

async function serve(env, port) {
  const child = spawn('uv', ['run', '--frozen', 'python', '-m', 'uvicorn', 'src.main:app', '--host', '127.0.0.1', '--port', String(port)], {
    cwd: ROOT, env, detached: process.platform !== 'win32', stdio: ['ignore', 'pipe', 'pipe']
  });
  let log = '';
  for (const stream of [child.stdout, child.stderr]) stream.on('data', chunk => { log = (log + chunk).slice(-20000); });
  const closed = new Promise(resolve => child.once('close', resolve));
  const base = `http://127.0.0.1:${port}`;
  async function stop() {
    if (child.exitCode === null && child.signalCode === null) {
      const kill = signal => process.platform === 'win32' ? child.kill(signal) : process.kill(-child.pid, signal);
      kill('SIGTERM');
      if (!await Promise.race([closed.then(() => true), delay(10000).then(() => false)])) kill('SIGKILL');
    }
    await closed;
  }
  // Cold start on a shared CI runner can take well over the runner's 30 s budget.
  const deadline = Date.now() + 180000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`server exited ${child.exitCode}: ${log}`);
    try {
      if ((await fetch(`${base}/health`, {signal: AbortSignal.timeout(1000)})).ok) return {base, stop, log: () => log};
    } catch {}
    await delay(250);
  }
  await stop();
  throw new Error(`server not healthy in 180s: ${log}`);
}

async function demoServer(tmp) {
  const [port, other] = [await freePort(), await freePort()];
  const env = environment({
    APP_MODE: 'demo', DEMO_DATA_ROOT: join(tmp, 'demo'), DEMO_SEED_DIR: join(tmp, 'seed'),
    // Different hosts: loopback HTTP is accepted in development only (§2).
    DEMO_ORIGIN: `http://127.0.0.1:${port}`, NORMAL_ORIGIN: `http://localhost:${other}`,
    DEMO_SESSION_SECRET: secret(), DEMO_CSRF_SECRET: secret(),
    // The journey runs in seconds, not the 5 minutes D0-02 §7 plans; the §8 maximum keeps 429 out of it.
    DEMO_REQUESTS_PER_MINUTE: '600'
  });
  const out = await run(['-m', 'src.demo.seed', 'build', '--out', env.DEMO_SEED_DIR], env);
  const receipt = JSON.parse(out.trim().split('\n').at(-1));
  receipt.command = receipt.command.replace(env.DEMO_SEED_DIR, '<tmp>/seed');
  return {...await serve(env, port), receipt};
}

async function normalServer(tmp) {
  const password = secret();
  const env = environment({
    APP_MODE: 'normal', DATABASE_URL: `sqlite+aiosqlite:///${join(tmp, 'normal.db')}`,
    BOOTSTRAP_ADMIN_ON_STARTUP: 'true', ADMIN_DEFAULT_PASSWORD: password
  });
  return {...await serve(env, await freePort()), password};
}

// RFC 4180, enough for the export writer's quoting and CRLF rows.
function parseCsv(text) {
  const rows = [[]];
  let field = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c !== '"') field += c;
      else if (text[i + 1] === '"') { field += '"'; i++; } else quoted = false;
    } else if (c === '"') quoted = true;
    else if (c === ',') { rows.at(-1).push(field); field = ''; }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      rows.at(-1).push(field); field = ''; rows.push([]);
    } else field += c;
  }
  if (field || rows.at(-1).length) rows.at(-1).push(field); else rows.pop();
  const [header, ...cells] = rows;
  assert(cells.every(row => row.length === header.length), 'ragged CSV row');
  return {header, rows: cells.map(row => Object.fromEntries(header.map((name, i) => [name, row[i]])))};
}

// Status first: an error envelope must not read as a missing field.
async function json200(response) {
  assert.equal(response.status(), 200, await response.text());
  return response.json();
}

const state = page => page.evaluate(async () => (await fetch('/demo/state?include_tour=true')).json());
const ns = current => `ds=${current.browser_scope_id}&dg=${current.generation}&da=${current.auth_epoch}`;
// §24.1 map letters in start..reset order; the tour recomputes it from facts on load.
async function tourMap(page, expected) {
  await page.waitForFunction(map => [...document.querySelectorAll('#demo-tour [data-demo-step]')]
    .map(item => ({complete: 'C', current: 'X', pending: 'P'})[item.dataset.state]).join(' ') === map, expected, {timeout: 10000})
    .catch(() => {});
  return page.locator('#demo-tour [data-demo-step]').evaluateAll(items =>
    items.map(item => ({complete: 'C', current: 'X', pending: 'P'})[item.dataset.state]).join(' '));
}

// Visitor requests on the page's own cookies; unsafe methods send Origin and the CSRF header (§10).
function visitor(page, base) {
  const request = page.context().request;
  return {
    async get(path, current) {
      return request.get(`${base}${path}${path.includes('?') ? '&' : '?'}${ns(current ?? await state(page))}`);
    },
    async post(path, data = {}, current) {
      // The state read can rotate the CSRF cookie, so the token is read after it.
      const query = ns(current ?? await state(page));
      const token = (await page.context().cookies()).find(item => item.name === 'demo_csrftoken')?.value;
      return request.post(`${base}${path}?${query}`, {headers: {origin: base, 'x-csrf-token': token}, data});
    },
    async control(path, body) {
      const token = (await page.context().cookies()).find(item => item.name === 'demo_csrftoken')?.value;
      const response = await request.post(`${base}${path}`, {headers: {origin: base, 'x-csrf-token': token}, data: {request_id: randomUUID(), ...body}});
      return {status: response.status(), body: await response.json()};
    }
  };
}

async function settled(page, generation) {
  for (let attempt = 0; attempt < 200; attempt++) {
    const current = await state(page);
    if (current.state !== 'resetting') {
      assert.equal(current.state, 'ready');
      assert.equal(current.generation, generation);
      return current;
    }
    await delay(50);
  }
  throw new Error('reset did not finish');
}

async function open(browser, pageErrors) {
  const context = await browser.newContext();
  context.setDefaultTimeout(60000);
  const page = await context.newPage();
  page.on('pageerror', error => pageErrors.push(error.message));
  // Every confirm (end session, reset) is the visitor's explicit yes.
  page.on('dialog', dialog => dialog.accept());
  return page;
}

async function startAs(page, base, role, entry = async () => {}) {
  assert.equal((await page.goto(`${base}/demo`)).status(), 200);
  await entry();
  const [started] = await Promise.all([
    page.waitForResponse(response => response.url() === `${base}/demo/start`),
    page.click(`.demo-entry [data-demo-action="start"][data-demo-role="${role}"]`)
  ]);
  await page.waitForURL(role === 'teacher' ? /\/scenarios\?/ : /\/admin\//);
  return started;
}

async function journey(browser, demo, step, pageErrors) {
  const {base} = demo;
  const page = await open(browser, pageErrors);
  const http = visitor(page, base);
  // CDP keeps no body for an SSE response the page reads itself, so the page tees its own streams.
  await page.addInitScript(() => {
    const original = window.fetch;
    window.__d304Streams = [];
    window.fetch = async (...args) => {
      const response = await original(...args);
      if (/\/turns\/([^/]+\/mentor\/)?stream$/.test(new URL(args[0]?.url ?? String(args[0]), location.href).pathname)) {
        window.__d304Streams.push(response.clone().text());
      }
      return response;
    };
  });
  let started, sessionId, ended;

  await step('1', '시작', async () => {
    const response = await startAs(page, base, 'teacher', async () => {
      const entry = JSON.parse(await page.locator('#demo-config').textContent());
      assert.deepEqual([entry.mode, entry.contract_version, entry.state], ['demo', 'd0-03-v1-r3', null]);
    });
    // The start body is gone once the UI follows next_url; GET /demo/state is the same DemoState.
    assert.equal(response.status(), 201);
    started = await state(page);
    assert.deepEqual([started.state, started.role, started.csrf_cookie_name, started.csrf_header_name],
      ['ready', 'teacher', 'demo_csrftoken', 'x-csrf-token']);
    assert(Number.isInteger(started.generation) && Number.isInteger(started.auth_epoch), JSON.stringify(started));
    assert.equal(started.next_url, `/scenarios?${ns(started)}`);
    assert.equal(started.tour_facts.teacher_entered, true);
    assert.equal(await page.locator('.demo-banner strong').textContent(), '데모 · 준비된 예시 응답 · 실제 AI 품질 평가용 아님');
    const root = await page.evaluate(() => ({...document.documentElement.dataset}));
    assert.deepEqual(root, {demoMode: 'demo', demoScope: started.browser_scope_id,
      demoGeneration: String(started.generation), demoAuthEpoch: String(started.auth_epoch)});
    const config = await page.locator('#demo-config').textContent();
    for (const key of Object.keys(JSON.parse(config))) assert(!/factory|secret|path|root|db/i.test(key), `demo-config key ${key}`);
    assert(!config.includes(tmpdir()), 'demo-config carries a real path');
    assert.equal(await tourMap(page, 'C X P P P P'), 'C X P P P P');
    await page.screenshot({path: join(OUT, 'step1-start.png'), fullPage: true});
  });

  await step('2', '시나리오', async () => {
    await page.getByRole('link', {name: '대화 시작'}).first().click();
    await page.waitForURL(/\/scenarios\/\d+\?/);
    sessionId = JSON.parse(await page.locator('#chat-config').textContent()).sessionId;
    assert.notEqual(sessionId, SEED.session);
    assert.equal(await page.locator('.message-student').count(), 0, 'no student reply before the first turn');
    const facts = (await state(page)).tour_facts;
    assert.equal(facts.scenario_created, true);
    assert.equal(facts.journey_session_id, sessionId);
    // A refresh resumes the same session instead of adding one (D0-02 §7).
    await page.reload();
    assert.equal(JSON.parse(await page.locator('#chat-config').textContent()).sessionId, sessionId);
    assert.equal(await tourMap(page, 'C C X P P P'), 'C C X P P P');
  }, ['1']);

  await step('3', '대화/멘토', async () => {
    for (const text of [INPUT.B, INPUT.common]) {
      const before = await page.locator('.mentor-slot[data-status="completed"]').count();
      await page.fill('#teacher-input', text);
      await page.click('#teacher-form button[type=submit]');
      await page.waitForFunction(count => document.querySelectorAll('.mentor-slot[data-status="completed"]').length > count &&
        !document.querySelector('#teacher-input').disabled, before, {timeout: 60000});
    }
    assert.equal(await page.locator('.message-student[data-message-id]').count(), 2, 'two completed student turns');
    // The first turn's automatic check coaches; the common turn's check stores no message (D0-02 §4).
    assert.equal(await page.locator('.mentor-slot[data-message-id]').count(), 1, 'one stored coaching');
    const streams = await page.evaluate(() => Promise.all(window.__d304Streams));
    const frames = streams.flatMap(body => body.split('\n').filter(line => line.startsWith('data: '))
      .map(line => JSON.parse(line.slice(6))));
    assert.equal(streams.length, 4, 'two student and two mentor streams');
    const provenance = frames.filter(frame => frame.provenance).map(frame => frame.provenance);
    assert(provenance.length >= 4, JSON.stringify(frames));
    for (const item of provenance) {
      assert.equal(item.response_source, 'prepared_example');
      assert.equal(item.external_ai_called, false);
      assert.equal(item.record_origin, 'visitor_generated');
    }
    assert.equal((await state(page)).tour_facts.dialogue_complete, true);
    await page.reload();
    assert.equal(await tourMap(page, 'C C C X P P'), 'C C C X P P');
    await page.screenshot({path: join(OUT, 'step3-dialogue.png'), fullPage: true});
  }, ['2']);

  await step('4', '분석', async () => {
    const [end] = await Promise.all([
      page.waitForResponse(response => response.url().startsWith(`${base}/sessions/${sessionId}/end`)),
      page.click('#end-session-btn')
    ]);
    assert.equal(end.status(), 202, await end.text());
    let analysis;
    for (let attempt = 0; attempt < 120 && !(analysis?.feedback_status === 'ok'); attempt++) {
      if (attempt) await delay(250);
      analysis = await json200(await http.get(`/sessions/${sessionId}/analysis`));
    }
    assert.equal(analysis.feedback_status, 'ok', JSON.stringify(analysis));
    assert.deepEqual(analysis.distribution, {r1: 0, r2: 1, r3: 1});
    assert.equal(analysis.provenance?.record_origin, 'visitor_generated');
    assert.equal((await state(page)).tour_facts.analysis_ready, true);
    // Reopening the completed history shows the adopted report and starts no analysis (§22).
    const posts = [];
    page.on('request', request => { if (request.method() === 'POST') posts.push(request.url()); });
    assert.equal((await page.goto(`${base}/sessions/${sessionId}?${ns(started)}`)).status(), 200);
    await page.waitForLoadState('networkidle');
    const report = page.locator(`[data-analysis-result][data-session-id="${sessionId}"] .analysis-accepted-report`);
    await report.waitFor();
    assert.equal(await report.getAttribute('data-status'), analysis.accepted_report.status);
    assert(analysis.accepted_report.brief_feedback.length > 0, JSON.stringify(analysis.accepted_report));
    const shown = await report.textContent();
    for (const text of analysis.accepted_report.brief_feedback) assert(shown.includes(text), `report misses: ${text}`);
    assert.deepEqual(posts, []);
    assert.deepEqual(await json200(await http.get(`/sessions/${sessionId}/analysis`)), analysis);
    ended = analysis;
    await page.screenshot({path: join(OUT, 'step4-history.png'), fullPage: true});
  }, ['3']);

  let admin;
  await step('5', '관리자/CSV', async () => {
    const [switched] = await Promise.all([
      page.waitForResponse(response => response.url() === `${base}/demo/role`),
      page.click('#demo-tour [data-demo-action="role"][data-demo-role="admin"]')
    ]);
    assert.equal(switched.status(), 200, await switched.text());
    await page.waitForURL(/\/admin\//);
    admin = await state(page);
    assert.equal(admin.role, 'admin');
    assert.equal(admin.auth_epoch, started.auth_epoch + 1);
    const listed = await json200(await http.get('/admin/sessions'));
    assert.deepEqual(listed.sessions.map(item => item.id).sort(), [SEED.session, sessionId].sort());
    for (const [id, origin] of [[SEED.session, 'seed_prepared'], [sessionId, 'visitor_generated']]) {
      const detail = await json200(await http.get(`/admin/sessions/${id}/analysis`));
      assert.equal(detail.provenance?.record_origin, origin, `session ${id} origin`);
    }
    await page.getByRole('link', {name: '대화목록'}).first().click();
    await page.waitForURL(/\/admin\/sessions-page/);
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.click(`a[href^="/admin/sessions/${sessionId}/download"]`)
    ]);
    assert.equal(download.suggestedFilename(), `demo_d0-02-v1_admin_session-${sessionId}.csv`);
    const text = await readFile(await download.path(), 'utf8');
    await writeFile(join(OUT, 'step5-admin.csv'), text);
    const csv = parseCsv(text.replace(/^﻿/, ''));
    assert.deepEqual(csv.header, COLUMNS);
    assert.equal(csv.rows.length, 6, 'teacher 2 + student 2 + coaching 1 + summary 1');
    assert.deepEqual(csv.rows.map(row => row.role).sort(), ['student', 'student', 'summary', 'teacher', 'teacher', 'tutor']);
    for (const row of csv.rows) {
      assert.equal(row.session_id, String(sessionId));
      assert.deepEqual([...COLUMNS.slice(27).map(name => row[name])], [...SUFFIX, 'visitor_generated']);
    }
  }, ['4']);

  await step('6', '삭제/reset', async () => {
    let response = await http.post(`/admin/users/${SEED.teacher}/delete`);
    assert.equal(response.status(), 200, await response.text());
    // Ended records stay with the teacher identity cleared; nothing recreates the teacher (§22).
    response = await http.get(`/admin/sessions/${sessionId}/download`);
    assert.equal(response.status(), 200, await response.text());
    const stored = parseCsv((await response.text()).replace(/^﻿/, '')).rows;
    for (const row of stored) {
      assert.deepEqual([row.teacher_id, row.teacher_username, row.teacher_nickname], ['', '', '']);
    }
    // Teacher inputs plus the stored student replies, coaching and summary must all vanish with the session.
    const body = [INPUT.B, INPUT.common,
      ...stored.filter(row => ['student', 'tutor'].includes(row.role)).map(row => row.content),
      ...stored.filter(row => row.role === 'summary').map(row => row.feedback)];
    assert.equal(body.length, 6, JSON.stringify(stored.map(row => row.role)));
    assert(body.every(Boolean), 'stored body text captured');
    const notice = JSON.parse(await readFile(join(ROOT, 'src/i18n/catalog.json'), 'utf8')).teacher.deleted_session_notice.ko;
    const role = await http.control('/demo/role', {role: 'teacher', expected_generation: admin.generation, expected_auth_epoch: admin.auth_epoch});
    assert.equal(role.status, 409, JSON.stringify(role.body));
    assert.equal(role.body.detail.code, 'DEMO_ROLE_DELETED');
    assert.equal((await state(page)).tour_facts.teacher_deleted, true);
    response = await http.post(`/admin/sessions/${sessionId}/delete`);
    assert.equal(response.status(), 200, await response.text());
    // The detail may stay as a tombstone (D0-02 §8); body, analysis and export are gone.
    for (const [path, statuses] of [[`/admin/sessions/${sessionId}/detail`, [200, 404]],
      [`/admin/sessions/${sessionId}/download`, [404]], [`/admin/sessions/${sessionId}/analysis`, [404]]]) {
      response = await http.get(path);
      assert(statuses.includes(response.status()), `${path}: ${response.status()}`);
      const text = await response.text();
      for (const item of body) assert(!text.includes(item), `${path} leaks the deleted body: ${item}`);
      if (response.status() === 200) {
        assert(text.includes(notice), `${path} tombstone notice`);
        assert(!/data-message-id|data-analysis-result/.test(text), `${path} renders messages or analysis`);
      }
    }
    assert.equal((await state(page)).tour_facts.session_deleted, true);
    const before = await state(page);
    const [reset] = await Promise.all([
      page.waitForResponse(response => response.url() === `${base}/demo/reset`),
      page.click('#demo-tour [data-demo-action="reset"]')
    ]);
    assert([200, 202].includes(reset.status()), await reset.text());
    const fresh = await settled(page, before.generation + 1);
    // The previous generation's URL never resolves against the new seed (§25).
    response = await http.get(`/admin/sessions/${SEED.session}/detail`, before);
    assert.equal(response.status(), 409);
    assert.equal((await response.json()).detail.code, 'DEMO_STALE_GENERATION');
    assert.deepEqual((await json200(await http.get('/admin/sessions', fresh))).sessions.map(item => item.id), [SEED.session]);
    assert.equal((await http.get(`/admin/sessions/${sessionId}/detail`, fresh)).status(), 404);
    const back = await http.control('/demo/role', {role: 'teacher', expected_generation: fresh.generation, expected_auth_epoch: fresh.auth_epoch});
    assert.equal(back.status, 200, 'seed teacher restored');
    await page.screenshot({path: join(OUT, 'step6-reset.png'), fullPage: true});
  }, ['5']);
  await page.context().close();
  return ended;
}

async function extras(browser, demo, step, pageErrors) {
  const {base} = demo;
  const page = await open(browser, pageErrors);
  const http = visitor(page, base);
  let teacher;

  await step('E1', '완료 이력 열기', async () => {
    assert.equal((await startAs(page, base, 'teacher')).status(), 201);
    teacher = await state(page);
    const before = await json200(await http.get(`/sessions/${SEED.session}/analysis`));
    assert.equal(before.feedback_status, 'ok');
    const posts = [];
    page.on('request', request => { if (request.method() === 'POST') posts.push(request.url()); });
    assert.equal((await page.goto(`${base}/sessions/${SEED.session}?${ns(teacher)}`)).status(), 200);
    await page.waitForLoadState('networkidle');
    await page.locator('[data-analysis-result]').first().waitFor();
    assert.deepEqual(posts, [], 'reopening history starts nothing');
    assert.deepEqual(await json200(await http.get(`/sessions/${SEED.session}/analysis`)), before);
  });

  await step('E2', 'ready 상태 역할 전환', async () => {
    const current = teacher ?? await state(page);
    const toAdmin = await http.control('/demo/role', {role: 'admin', expected_generation: current.generation, expected_auth_epoch: current.auth_epoch});
    assert.equal(toAdmin.status, 200, JSON.stringify(toAdmin.body));
    assert.match(toAdmin.body.next_url, /^\/admin\//);
    const admin = await state(page);
    assert.deepEqual([admin.role, admin.generation, admin.auth_epoch], ['admin', current.generation, current.auth_epoch + 1]);
    const stale = await http.get('/admin/sessions', current);
    assert.equal(stale.status(), 409);
    assert.equal((await stale.json()).detail.code, 'DEMO_STALE_ROLE');
    const same = await http.control('/demo/role', {role: 'admin', expected_generation: admin.generation, expected_auth_epoch: admin.auth_epoch});
    assert.equal(same.status, 200, 'same role is a no-op');
    assert.equal((await state(page)).auth_epoch, admin.auth_epoch);
  }, ['E1']);

  await step('E3', '한영 UI 전환', async () => {
    const catalog = JSON.parse(await readFile(join(ROOT, 'src/i18n/catalog.json'), 'utf8')).demo;
    const current = await state(page);
    await page.goto(`${base}/admin/scenarios?${ns(current)}`);
    await Promise.all([page.waitForEvent('load'), page.click('[data-ui-language="en"]')]);
    await page.waitForFunction(() => document.documentElement.lang === 'en');
    assert.equal(await page.locator('.demo-banner strong').textContent(), catalog.banner.en);
    assert.equal(await page.locator('#demo-tour-title').textContent(), catalog['tour.title'].en);
    // UI language never changes content or provenance versions (§19).
    const config = JSON.parse(await page.locator('#demo-config').textContent());
    assert.deepEqual([config.ui_language, config.content_version, config.fixture_version], ['en', 'd0-02-v1', 'demo-fixture-v1']);
    await page.screenshot({path: join(OUT, 'e3-english.png'), fullPage: true});
    await Promise.all([page.waitForEvent('load'), page.click('[data-ui-language="ko"]')]);
    await page.waitForFunction(() => document.documentElement.lang === 'ko');
    assert.equal(await page.locator('.demo-banner strong').textContent(), catalog.banner.ko);
  }, ['E2']);

  // ponytail: DEMO_IDLE_TTL_SECONDS has a 300 s floor (src/demo/visitors.py:49), too long for a CI shard.
  await step('E4', '만료 후 재진입', null, [], 'DEMO_IDLE_TTL_SECONDS minimum is 300 s (src/demo/visitors.py:49); a 5-minute idle wait does not fit a CI shard');

  await step('E5', '비교 직접 요청 거절', async () => {
    for (const path of ['/admin/comparison-conversations', '/admin/comparisons']) {
      const response = await http.post(path, {scenario_id: 1});
      assert.equal(response.status(), 403, path);
      assert.equal((await response.json()).detail.code, 'DEMO_ACTION_FORBIDDEN');
    }
  }, ['E2']);

  // Step 5's seed half, run on its own so a blocked journey still checks it (§19, §23).
  await step('E6', 'seed 완료 예시 출처 구분', async () => {
    const response = await http.get(`/admin/sessions/${SEED.session}/download`);
    assert.equal(response.status(), 200);
    for (const row of parseCsv((await response.text()).replace(/^﻿/, '')).rows) {
      assert.deepEqual(COLUMNS.slice(27).map(name => row[name]), [...SUFFIX, 'seed_prepared']);
    }
    const analysis = await json200(await http.get(`/admin/sessions/${SEED.session}/analysis`));
    assert.equal(analysis.provenance?.record_origin, 'seed_prepared', 'seed analysis provenance');
  }, ['E2']);
  await page.context().close();
}

async function normal(browser, server, step, pageErrors) {
  const {base, password} = server;
  const page = await open(browser, pageErrors);
  await step('N1', 'normal', async () => {
    assert.equal((await fetch(`${base}/health`)).status, 200);
    assert.equal((await page.goto(`${base}/login`)).status(), 200);
    await page.fill('input[name="username"]', 'admin');
    await page.fill('input[name="password"]', password);
    await Promise.all([page.waitForURL(/\/scenarios/), page.click('form button[type="submit"]')]);
    assert.equal(JSON.parse(await page.locator('#demo-config').textContent()).mode, 'normal');
    for (const path of ['/demo', '/demo/state']) assert.equal((await page.context().request.get(`${base}${path}`)).status(), 404, path);
    for (const {name} of await page.context().cookies()) assert(!name.startsWith('demo_'), `demo cookie ${name} in normal`);
  });
  await page.context().close();
}

export default async function check(page) {
  const pageErrors = [];
  const results = [];
  async function step(id, name, body, needs = [], reason) {
    const blocked = needs.find(need => results.find(result => result.id === need)?.status !== 'passed');
    let result;
    if (!body || blocked) result = {id, name, status: 'not run', detail: reason ?? `blocked by step ${blocked}`};
    else {
      const startedAt = Date.now();
      try {
        await body();
        result = {id, name, status: 'passed'};
      } catch (error) {
        result = {id, name, status: 'failed', detail: String(error.message ?? error).replace(/\s+/g, ' ').slice(0, 600)};
      }
      result.elapsedMs = Date.now() - startedAt;
    }
    results.push(result);
    console.log(`STEP ${id} ${result.status}${result.detail ? `: ${result.detail}` : ''}`);
  }

  await mkdir(OUT, {recursive: true});
  const tmp = await mkdtemp(join(tmpdir(), 'd3-04-'));
  const servers = await Promise.allSettled([demoServer(tmp), normalServer(tmp)]);
  try {
    const failed = servers.find(server => server.status === 'rejected');
    if (failed) throw failed.reason;
    const [demo, normalMode] = servers.map(server => server.value);
    console.log(`RECEIPT ${JSON.stringify(demo.receipt)}`);
    const browser = page.context().browser();
    await journey(browser, demo, step, pageErrors);
    await extras(browser, demo, step, pageErrors);
    await normal(browser, normalMode, step, pageErrors);
    await writeFile(join(OUT, 'result.json'), JSON.stringify({receipt: demo.receipt, results}, null, 2) + '\n');
  } finally {
    await Promise.allSettled(servers.filter(server => server.status === 'fulfilled').map(server => server.value.stop()));
    await rm(tmp, {recursive: true, force: true});
  }
  const failed = results.filter(result => result.status === 'failed');
  assert.equal(failed.length, 0, `D3-04 failed steps: ${failed.map(result => `${result.id} ${result.detail}`).join(' | ')}`);
  return {pageErrors};
}
