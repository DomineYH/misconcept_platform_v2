import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {randomUUID} from 'node:crypto';
import {tmpdir} from 'node:os';
import {chromium} from 'playwright';

// D4-02 (#204) B/F on the real app (tests/demo_d4_02_server.py); the runner's fixture page is unused.
// These need the G4 integrated SHA: demo student/mentor/analysis runs (#198) on the product binding.
const PENDING = [
  'B: A runs a dialogue and its analysis; B lists/totals/detail unchanged',
  "B: A's run ID requested from B -> 404 DEMO_RESOURCE_NOT_FOUND without body"
];
// Product gap, not G4: GET /admin/sessions/{id}/analysis is 503 DEMO_CONFIGURATION_INVALID in demo because
// load_plan reaches context_budget.provider_parameters, which has no "demo" adapter (KeyError, src/services/context_budget.py:13).
const PENDING_198 = ["B: B's admin analysis detail of the seed session unchanged by A's edits"];
// #197 manifest seed_refs (src/demo/fixtures/manifest.json).
const SEED = {admin: 1, teacher: 2, session: 1};
const SUFFIX = ['demo_response_source', 'demo_external_ai_called', 'demo_content_version', 'demo_fixture_version', 'demo_record_origin'];

async function server(args = []) {
  const child = spawn('uv', ['run', '--frozen', 'python', 'tests/demo_d4_02_server.py', ...args], {
    stdio: ['pipe', 'pipe', 'pipe'], env: process.env
  });
  let log = '';
  let receipt = null;
  const closed = new Promise(resolve => child.once('close', resolve));
  const port = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`helper not READY in 180s: ${log}`)), 180000);
    const read = chunk => {
      log += chunk;
      const seed = /^RECEIPT (.+)$/m.exec(log);
      if (seed) receipt = JSON.parse(seed[1]);
      const match = /^READY (\d+)$/m.exec(log);
      if (match) { clearTimeout(timer); resolve(Number(match[1])); }
    };
    child.stdout.on('data', read);
    child.stderr.on('data', read);
    child.once('error', reject);
    closed.then(code => reject(new Error(`helper exited ${code}: ${log}`)));
  });
  return {
    base: `http://127.0.0.1:${port}`,
    receipt,
    async stop() {
      child.stdin.end();
      const code = await Promise.race([closed, new Promise(resolve => setTimeout(resolve, 20000, 'timeout'))]);
      if (code === 'timeout') child.kill('SIGKILL');
      assert.equal(code, 0, `helper exit: ${log}`);
      assert(!/External (network|AI SDK) forbidden/.test(log), `egress attempted: ${log}`);
      return log;
    }
  };
}

function setCookies(headers) {
  return Object.fromEntries(headers.filter(header => header.name.toLowerCase() === 'set-cookie').map(header => {
    const [pair, ...attributes] = header.value.split(/;\s*/);
    return [pair.split('=')[0], Object.fromEntries(attributes.map(item => {
      const [key, value = ''] = item.split('=');
      return [key.toLowerCase(), value.toLowerCase()];
    }))];
  }));
}

async function expectCode(response, status, code) {
  assert.equal(response.status(), status, await response.text());
  assert.equal((await response.json()).detail.code, code);
  assert.equal(response.headers()['cache-control'], 'no-store');
  assert.deepEqual(setCookies(await response.headersArray()), {}, 'no Set-Cookie on refusal');
}

async function cookie(context, name) {
  return (await context.cookies()).find(item => item.name === name)?.value;
}

// The browser itself sends Origin and reads the token from document.cookie, as the UI does.
function control(page, path, body) {
  return page.evaluate(async ({path, body}) => {
    const token = document.cookie.split('; ').find(item => item.startsWith('demo_csrftoken='))?.slice(15);
    const response = await fetch(path, {method: 'POST', headers: {'content-type': 'application/json', 'x-csrf-token': token}, body: JSON.stringify(body)});
    return {status: response.status, body: await response.json()};
  }, {path, body});
}

function state(page) {
  return page.evaluate(async () => (await fetch('/demo/state')).json());
}

async function ready(page, generation) {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    const current = await state(page);
    if (current.state !== 'resetting') {
      assert.equal(current.state, 'ready');
      assert.equal(current.generation, generation);
      return current;
    }
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw new Error('reset did not finish');
}

const namespace = current => `ds=${current.browser_scope_id}&dg=${current.generation}&da=${current.auth_epoch}`;

// Real server under shared-CI load: the first requests can exceed the 30 s default.
async function open(browser) {
  const context = await browser.newContext();
  context.setDefaultTimeout(90000);
  return context;
}

async function demoChecks(browser, {base}, pageErrors) {
  const origin = base;
  const contexts = {A: await open(browser), B: await open(browser)};
  const pages = {};
  for (const [name, context] of Object.entries(contexts)) {
    pages[name] = await context.newPage();
    pages[name].on('pageerror', error => pageErrors.push(error.message));
    // §10: GET /demo issues only the two demo cookies with the exact attributes (Secure=false: loopback development).
    const entry = await pages[name].goto(`${base}/demo`);
    assert.equal(entry.status(), 200);
    assert.deepEqual(setCookies(await entry.headersArray()), {
      demo_session_id: {httponly: '', 'max-age': '28800', path: '/', samesite: 'lax'},
      demo_csrftoken: {path: '/', samesite: 'lax'}
    });
    const jar = (await context.cookies()).map(({name, domain, path, httpOnly, sameSite, expires}) =>
      ({name, domain, path, httpOnly, sameSite, session: expires === -1})).sort((a, b) => a.name.localeCompare(b.name));
    assert.deepEqual(jar, [
      {name: 'demo_csrftoken', domain: '127.0.0.1', path: '/', httpOnly: false, sameSite: 'Lax', session: true},
      {name: 'demo_session_id', domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax', session: false}
    ]);
  }
  const before = {session: await cookie(contexts.A, 'demo_session_id'), csrf: await cookie(contexts.A, 'demo_csrftoken')};

  // §9: two explicit starts from the same seed give two separate visitor spaces.
  const started = {};
  for (const [name, role] of [['A', 'teacher'], ['B', 'admin']]) {
    const response = await control(pages[name], '/demo/start', {request_id: randomUUID(), role});
    assert.equal(response.status, 201, JSON.stringify(response.body));
    started[name] = await state(pages[name]);
    assert.equal(started[name].state, 'ready');
    assert.equal(started[name].role, role);
    assert.equal(started[name].contract_version, 'd0-03-v1-r3');
    assert.equal(started[name].csrf_cookie_name, 'demo_csrftoken');
    assert.equal(started[name].csrf_header_name, 'x-csrf-token');
    for (const key of ['visitor_id', 'user_id', 'path', 'db_path']) assert(!(key in started[name]), key);
  }
  assert.notEqual(started.A.browser_scope_id, started.B.browser_scope_id);
  assert.notEqual(await cookie(contexts.A, 'demo_csrftoken'), before.csrf, 'start publish rotates the CSRF token');
  const totals = await contexts.B.request.get(`${base}/metrics?${namespace(started.B)}`);
  assert.equal(totals.status(), 200, await totals.text());
  const totalsBefore = (await totals.json()).database;

  // §9.1/§10 CSRF and Origin matrix on POST /demo/role (same role = no-op, so the controls change nothing).
  const role = () => ({request_id: randomUUID(), role: 'teacher', expected_generation: started.A.generation, expected_auth_epoch: started.A.auth_epoch});
  const form = () => Object.fromEntries(Object.entries(role()).map(([key, value]) => [key, String(value)]));
  const token = await cookie(contexts.A, 'demo_csrftoken');
  const post = (headers, body) => contexts.A.request.post(`${base}/demo/role`, {headers, ...body, maxRedirects: 0});
  const accepted = [
    ['JSON with Origin and token', await post({origin, 'x-csrf-token': token}, {data: role()})],
    ['form with same-origin Referer only', await post({referer: `${base}/demo`, 'x-csrf-token': token}, {form: form()})]
  ];
  for (const [name, response] of accepted) assert.equal(response.status(), 200, `${name}: ${await response.text()}`);
  const refused = [
    ['missing token', await post({origin}, {data: role()})],
    ['wrong token', await post({origin, 'x-csrf-token': `x${token}`}, {data: role()})],
    ['cross Origin (NORMAL_ORIGIN host)', await post({origin: base.replace('127.0.0.1', 'localhost'), 'x-csrf-token': token}, {data: role()})],
    ['JSON without Origin', await post({'x-csrf-token': token}, {data: role()})],
    ['form without Origin and Referer', await post({'x-csrf-token': token}, {form: form()})],
    ['form with cross Referer', await post({referer: 'http://localhost/demo', 'x-csrf-token': token}, {form: form()})]
  ];
  const stale = await open(browser);
  await stale.addCookies([
    {name: 'demo_session_id', value: before.session, url: base},
    {name: 'demo_csrftoken', value: before.csrf, url: base}
  ]);
  refused.push(['token from before the start rotation', await stale.request.post(`${base}/demo/role`, {headers: {origin, 'x-csrf-token': before.csrf}, data: role()})]);
  for (const [name, response] of refused) {
    try { await expectCode(response, 403, 'DEMO_CSRF_INVALID'); } catch (error) { throw new Error(`${name}: ${error.message}`); }
  }
  const unchanged = await state(pages.A);
  assert.deepEqual([unchanged.role, unchanged.generation, unchanged.auth_epoch], ['teacher', started.A.generation, started.A.auth_epoch]);

  // §9.1: a forged or missing signature is 401 before anything else.
  const forged = await open(browser);
  await forged.addCookies([{name: 'demo_session_id', value: `${before.session.slice(0, -2)}xx`, url: base}]);
  await expectCode(await forged.request.get(`${base}/demo/state`), 401, 'DEMO_SESSION_REQUIRED');
  await expectCode(await forged.request.post(`${base}/demo/start`, {headers: {origin, 'x-csrf-token': token}, data: {request_id: randomUUID(), role: 'admin'}}), 401, 'DEMO_SESSION_REQUIRED');
  const anonymous = await open(browser);
  await expectCode(await anonymous.request.post(`${base}/demo/start`, {headers: {origin}, data: {request_id: randomUUID(), role: 'admin'}}), 401, 'DEMO_SESSION_REQUIRED');

  // §3/§9: no client-chosen path, visitor or user selectors.
  const bToken = await cookie(contexts.B, 'demo_csrftoken');
  for (const extra of [{path: '/tmp/demo.db'}, {visitor_id: 'v1'}, {user_id: 1}]) {
    await expectCode(await contexts.B.request.post(`${base}/demo/start`, {headers: {origin, 'x-csrf-token': bToken}, data: {request_id: randomUUID(), role: 'admin', ...extra}}), 422, 'DEMO_INVALID_REQUEST');
  }
  await expectCode(await contexts.B.request.get(`${base}/metrics?${namespace(started.B)}&visitor_id=v1`), 422, 'DEMO_INVALID_REQUEST');
  await expectCode(await contexts.B.request.get(`${base}/metrics?${namespace(started.B)}`, {headers: {'x-demo-user-id': '1'}}), 422, 'DEMO_INVALID_REQUEST');

  // §10: ds/dg/da must agree with the X-Demo-* headers and the signed mapping.
  const metrics = headers => contexts.B.request.get(`${base}/metrics?${namespace(started.B)}`, {headers});
  await expectCode(await metrics({'x-demo-generation': String(started.B.generation + 1)}), 409, 'DEMO_STALE_GENERATION');
  await expectCode(await metrics({'x-demo-scope': started.A.browser_scope_id}), 409, 'DEMO_STALE_GENERATION');
  await expectCode(await metrics({'x-demo-auth-epoch': String(started.B.auth_epoch + 1)}), 409, 'DEMO_STALE_ROLE');
  await expectCode(await contexts.B.request.get(`${base}/metrics?${namespace(started.A)}`), 409, 'DEMO_STALE_GENERATION');

  // A changes role and resets; B's space does not move.
  const switched = await control(pages.A, '/demo/role', {...role(), role: 'admin'});
  assert.equal(switched.status, 200, JSON.stringify(switched.body));
  const admin = await state(pages.A);
  assert.equal(admin.auth_epoch, started.A.auth_epoch + 1);
  const reset = await control(pages.A, '/demo/reset', {request_id: randomUUID(), expected_generation: admin.generation, expected_auth_epoch: admin.auth_epoch, confirm: true});
  assert([200, 202].includes(reset.status), JSON.stringify(reset.body));
  const fresh = await ready(pages.A, admin.generation + 1);
  await expectCode(await contexts.A.request.get(`${base}/metrics?${namespace(admin)}`), 409, 'DEMO_STALE_GENERATION');
  assert.equal((await contexts.A.request.get(`${base}/metrics?${namespace(fresh)}`)).status(), 200);
  const other = await state(pages.B);
  assert.deepEqual(
    [other.browser_scope_id, other.generation, other.auth_epoch, other.role],
    [started.B.browser_scope_id, started.B.generation, started.B.auth_epoch, 'admin']
  );
  assert.deepEqual((await (await metrics({})).json()).database, totalsBefore);

  // §28/§3 on the #197 seed: A's admin edits and new IDs stay in A; B's lists, totals, detail, settings and CSV do not move.
  const bGet = path => contexts.B.request.get(`${base}${path}${path.includes('?') ? '&' : '?'}${namespace(started.B)}`);
  const view = async () => {
    const csv = await bGet(`/admin/sessions/${SEED.session}/download`);
    assert.equal(csv.status(), 200, await csv.text());
    const seen = {};
    for (const path of ['/metrics', '/admin/ai/state', '/admin/users', '/admin/groups']) {
      const response = await bGet(path);
      assert.equal(response.status(), 200, `${path}: ${await response.text()}`);
      // demo-config's sliding idle expiry moves with every request (§8); /metrics also has uptime.
      seen[path] = path === '/metrics' ? (await response.json()).database : (await response.text()).replace(/"expires_at": "[^"]*"/g, '');
    }
    return {seen, csv: await csv.text()};
  };
  const bBefore = await view();
  // §23: the seed's completed session exports 27 admin + 5 suffix columns, marked as prepared.
  const header = bBefore.csv.replace(/^\uFEFF/, '').split(/\r?\n/)[0].split(',');
  assert.equal(header.length, 32, header.join(','));
  assert.deepEqual(header.slice(27), SUFFIX);
  assert(bBefore.csv.includes(',prepared_example,false,d0-02-v1,demo-fixture-v1,seed_prepared'), 'seed row provenance');
  const aPost = async (path, data) => contexts.A.request.post(`${base}${path}?${namespace(fresh)}`, {
    headers: {origin, 'x-csrf-token': await cookie(contexts.A, 'demo_csrftoken')}, data: data ?? {}
  });
  let response = await aPost(`/admin/users/${SEED.teacher}/update`, {nickname: 'A-renamed'});
  assert.equal(response.status(), 200, await response.text());
  response = await aPost('/admin/users', {username: 'a_only', password: 'password123', nickname: 'A-only'});
  assert.equal(response.status(), 201, await response.text());
  const created = (await response.json()).id;
  response = await aPost(`/admin/users/${SEED.teacher}/delete`);
  assert.equal(response.status(), 200, await response.text());
  const aCsv = await contexts.A.request.get(`${base}/admin/sessions/${SEED.session}/download?${namespace(fresh)}`);
  assert.equal(aCsv.status(), 200, await aCsv.text());
  assert.notEqual(await aCsv.text(), bBefore.csv, "A's CSV reflects A's edits");
  assert.deepEqual(await view(), bBefore, "B unchanged by A's edits");
  // A's new user ID does not exist for B: 404 envelope only, nothing of A's data.
  const missing = await contexts.B.request.post(`${base}/admin/users/${created}/update?${namespace(started.B)}`, {
    headers: {origin, 'x-csrf-token': await cookie(contexts.B, 'demo_csrftoken')}, data: {nickname: 'B-edit'}
  });
  const missingText = await missing.text();
  await expectCode(missing, 404, 'DEMO_RESOURCE_NOT_FOUND');
  assert(!/A-only|a_only|A-renamed/.test(missingText), missingText);
  assert.deepEqual(await view(), bBefore, 'B unchanged after the refused request');

  // §9/§22: switching to the deleted seed teacher is refused; the administrator cannot delete itself.
  const aNow = await state(pages.A);
  const deleted = await control(pages.A, '/demo/role', {request_id: randomUUID(), role: 'teacher', expected_generation: aNow.generation, expected_auth_epoch: aNow.auth_epoch});
  assert.equal(deleted.status, 409, JSON.stringify(deleted.body));
  assert.equal(deleted.body.detail.code, 'DEMO_ROLE_DELETED');
  const usersBefore = (await (await contexts.A.request.get(`${base}/metrics?${namespace(aNow)}`)).json()).database;
  response = await aPost(`/admin/users/${SEED.admin}/delete`);
  assert.equal(response.status(), 400, await response.text());
  assert.deepEqual((await (await contexts.A.request.get(`${base}/metrics?${namespace(aNow)}`)).json()).database, usersBefore);
  assert.deepEqual([(await state(pages.A)).role, (await state(pages.A)).auth_epoch], ['admin', aNow.auth_epoch]);

  // §9/§10: the normal login policy is no demo entry point; no normal cookies appear.
  await expectCode(await contexts.A.request.post(`${base}/login`, {form: {username: 'fictional-admin', password: 'x'.repeat(12)}, maxRedirects: 0}), 403, 'DEMO_ACTION_FORBIDDEN');
  for (const context of [...Object.values(contexts), stale, forged, anonymous]) {
    for (const {name} of await context.cookies()) assert(name.startsWith('demo_'), `normal cookie ${name} in demo`);
  }
  for (const context of [...Object.values(contexts), stale, forged, anonymous]) await context.close();
  return [before.session, before.csrf, token, bToken];
}

async function normalChecks(browser, {base}) {
  // §9: normal mode registers no demo router; the normal CSRF token rules out a 403 masking the 404.
  const context = await open(browser);
  for (const path of ['/demo', '/demo/state']) {
    const response = await context.request.get(`${base}${path}`);
    assert.equal(response.status(), 404, path);
  }
  const csrf = await cookie(context, 'csrftoken');
  assert(csrf, 'normal CSRF cookie');
  for (const path of ['/demo/start', '/demo/role', '/demo/reset']) {
    const response = await context.request.post(`${base}${path}`, {headers: {origin: base, 'x-csrf-token': csrf}, data: {request_id: randomUUID(), role: 'admin'}});
    assert.equal(response.status(), 404, `${path}: ${await response.text()}`);
  }
  for (const {name} of await context.cookies()) assert(!name.startsWith('demo_'), `demo cookie ${name} in normal`);
  await context.close();
}

export default async function check() {
  for (const name of PENDING) console.log(`PENDING ${name}: needs G4 / #202`);
  for (const name of PENDING_198) console.log(`PENDING ${name}: product defect -> #198 (context_budget demo branch)`);
  const pageErrors = [];
  const [demo, normal] = await Promise.all([server(), server(['--normal'])]);
  const browser = await chromium.launch({headless: true});
  let values = [];
  // The seed build receipt for the evidence (§17); the helper prints it before READY.
  assert(demo.receipt?.seed_sha256 && demo.receipt.manifest_sha256, 'seed receipt');
  console.log(`RECEIPT ${JSON.stringify(demo.receipt)}`);
  try {
    values = await demoChecks(browser, demo, pageErrors);
    await normalChecks(browser, normal);
  } finally {
    await browser.close();
    const stopped = await Promise.allSettled([demo.stop(), normal.stop()]);
    for (const result of stopped) if (result.status === 'rejected') throw result.reason;
    const logs = stopped.map(result => result.value).join('\n');
    // §4: logs carry codes and counts only, never cookie values or the temporary root.
    for (const value of [...values, tmpdir()]) assert(!value || !logs.includes(value), 'secret or real path in server log');
  }
  return {pageErrors};
}
