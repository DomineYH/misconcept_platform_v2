import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {mkdir, readFile} from 'node:fs/promises';
import {setTimeout as delay} from 'node:timers/promises';

// #199 demo tour UI: static/js/demo-tour.js against the stubbed /demo/* endpoints.
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const JOURNEY = 7;
const KEY = 'demo:scope-a:1:tour-v1';
const EVENT_NAMES = ['demo:state', 'demo:role-changed', 'demo:reset-started', 'demo:reset-completed', 'demo:expired'];
const DETAIL_KEYS = ['auth_epoch', 'browser_scope_id', 'content_version', 'contract_version', 'fixture_version', 'generation', 'reason', 'role', 'state'];
const ADMIN_COLUMNS = ['session_id', 'scenario_id', 'scenario_title', 'teacher_id', 'teacher_username', 'teacher_nickname', 'session_started_at', 'session_ended_at', 'message_id', 'message_created_at', 'role', 'content', 'label', 'confidence', 'meta_json', 'feedback', 'classification_status', 'student_name', 'snapshot_origin', 'snapshot_created_at', 'source_scenario_version', 'config_hash_kind', 'analysis_schema_version', 'analysis_status', 'analysis_coverage_json', 'misconception_findings_json', 'message_analysis_disposition'];
const DEMO_COLUMNS = ['demo_response_source', 'demo_external_ai_called', 'demo_content_version', 'demo_fixture_version', 'demo_record_origin'];
const NEXT_ADMIN = '/fixtures/demo/page?role=admin&e=2';
// Same expression is evaluated in the page: "C X P P P P" from the li data-state values.
const MAP_JS = `[...document.querySelectorAll('.demo-tour-steps li')].map(li => ({complete: 'C', current: 'X', pending: 'P'}[li.dataset.state] ?? '?')).join(' ')`;

// ---- stub data -------------------------------------------------------------
const state = (o = {}) => ({
  contract_version: 'd0-03-v1-r3', content_version: 'd0-02-v1', fixture_version: 'demo-fixture-v1',
  state: 'ready', browser_scope_id: 'scope-a', generation: 1, auth_epoch: 1, role: 'teacher',
  expires_at: '2026-10-20T08:00:00Z', idle_ttl_seconds: 1800, absolute_ttl_seconds: 28800, request_id: null,
  csrf_cookie_name: 'demo_csrftoken', csrf_header_name: 'x-csrf-token', next_url: null, active: true,
  provenance: {mode: 'demo', response_source: 'prepared_example', external_ai_called: false, contract_version: 'd0-03-v1-r3', content_version: 'd0-02-v1', fixture_version: 'demo-fixture-v1', record_origin: 'visitor_generated'},
  last_error: null, tour_facts: facts(), ...o
});
const facts = (o = {}) => ({
  schema_version: 1, browser_scope_id: 'scope-a', generation: 1, auth_epoch: 1, journey_session_id: null,
  teacher_entered: true, scenario_created: false, dialogue_complete: false, analysis_ready: false,
  teacher_deleted: false, session_deleted: false, ...o
});
const analysisFacts = (o = {}) => facts({journey_session_id: JOURNEY, scenario_created: true, dialogue_complete: true, analysis_ready: true, ...o});
const deletedFacts = () => analysisFacts({teacher_deleted: true, session_deleted: true});
const ob = (kind, o = {}) => ({
  schema_version: 1, browser_scope_id: 'scope-a', generation: 1, auth_epoch: 1, journey_session_id: JOURNEY,
  kind, request_id: randomUUID(), ...o
});
const envelope = (code, recovery, retryable = false) => ({detail: {code, retryable, recovery, generation: null, auth_epoch: null}});
const receipt = (operation, request_id, result) => ({
  operation, request_id, result, historical: false,
  current: {state: result.state, generation: result.generation, auth_epoch: result.auth_epoch, role: result.role, browser_scope_id: result.browser_scope_id}
});

// ---- CSV -------------------------------------------------------------------
const quote = value => /[",\r\n]/.test(value) ? `"${value.replaceAll('"', '""')}"` : value;
function buildCsv({journey = JOURNEY, origin = 'visitor_generated', dropLast = false} = {}) {
  const columns = [...ADMIN_COLUMNS, ...DEMO_COLUMNS].slice(0, dropLast ? -1 : undefined);
  const demo = {demo_response_source: 'prepared_example', demo_external_ai_called: 'false', demo_content_version: 'd0-02-v1', demo_fixture_version: 'demo-fixture-v1', demo_record_origin: origin};
  const rows = [
    {role: 'teacher', content: 'Hi, "there"\nsecond line'},
    {role: 'student', content: 'plain'},
    {role: 'summary', content: ''}
  ].map(row => columns.map(c => quote(String({session_id: journey, scenario_id: 1, ...demo, ...row}[c] ?? ''))).join(','));
  return {text: `﻿${columns.join(',')}\r\n${rows.join('\r\n')}\r\n`, rowCount: rows.length};
}

// ---- environment: one fresh context per scenario ---------------------------
const ROUTES = {
  state: url => url.pathname === '/demo/state',
  start: url => url.pathname === '/demo/start',
  role: url => url.pathname === '/demo/role',
  reset: url => url.pathname === '/demo/reset',
  download: url => /^\/admin\/sessions\/\d+\/download$/.test(url.pathname)
};
// Mirrors the server: selectors are validated only for an include_tour GET of a ready visit,
// and tour_facts are returned only there; a plain GET is bound to the bootstrap alone.
function serverState(req, env) {
  const cur = env.current;
  if (req.query.include_tour !== 'true' || cur.state !== 'ready') return {json: {...cur, tour_facts: null}};
  const [scope, g, e] = ['x-demo-scope', 'x-demo-generation', 'x-demo-auth-epoch'].map(k => req.headers[k]);
  if ([scope, g, e].some(v => v !== undefined)) {
    if (scope !== cur.browser_scope_id || g !== String(cur.generation)) return {status: 409, json: envelope('DEMO_STALE_GENERATION', 'demo.stale_generation')};
    if (e !== String(cur.auth_epoch)) return {status: 409, json: envelope('DEMO_STALE_ROLE', 'demo.stale_role')};
  }
  return {json: cur};
}
const defaults = {
  state: (req, env) => req.query.request_id
    ? {status: 404, json: envelope('DEMO_RESOURCE_NOT_FOUND', 'demo.not_found')}
    : serverState(req, env)
};

async function until(fn, message, timeout = 10000) {
  const end = Date.now() + timeout;
  for (;;) {
    const value = await fn();
    if (value) return value;
    assert(Date.now() < end, `timeout: ${message}`);
    await delay(25);
  }
}

async function newEnv(browser, base) {
  const ctx = await browser.newContext();
  const env = {
    ctx, base, requests: [], events: [], errors: [], downloads: [], handlers: {}, current: state(),
    reqs: (name, page) => env.requests.filter(r => r.name === name && (!page || r.page === page)),
    posts: name => env.reqs(name).filter(r => r.method === 'POST'),
    eventsOf: (page, ...names) => env.events.filter(e => e.page === page && (!names.length || names.includes(e.name))),
    async open(path, wait = true) {
      const page = await ctx.newPage();
      page.on('pageerror', error => env.errors.push(error.message));
      page.on('download', download => env.downloads.push(download));
      await page.goto(base + path);
      if (wait) await until(() => env.reqs('state', page).length, `first state GET for ${path}`);
      return page;
    }
  };
  await ctx.addCookies([{name: 'demo_csrftoken', value: 'csrf-v1', url: base}]);
  await ctx.exposeBinding('__demoEvent', ({page}, name, detail) => env.events.push({name, detail, page}));
  await ctx.addInitScript(names => {
    for (const name of names) document.addEventListener(name, event => window.__demoEvent(name, event.detail));
  }, EVENT_NAMES);
  for (const [name, match] of Object.entries(ROUTES)) {
    await ctx.route(match, async route => {
      const request = route.request();
      const url = new URL(request.url());
      let json;
      try { json = JSON.parse(request.postData() ?? ''); } catch {}
      const entry = {
        name, method: request.method(), path: url.pathname, query: Object.fromEntries(url.searchParams),
        headers: request.headers(), body: request.postData(), json, page: request.frame().page()
      };
      env.requests.push(entry);
      try {
        const reply = await (env.handlers[name] ?? defaults[name] ?? (() => ({status: 404, json: envelope('DEMO_RESOURCE_NOT_FOUND', 'demo.not_found')})))(entry, env);
        if (reply.abort) return await route.abort('failed');
        const {status = 200, json: payload, body = '', headers = {}} = reply;
        await route.fulfill({
          status, headers: {'cache-control': 'no-store', ...headers},
          contentType: payload === undefined ? undefined : 'application/json',
          body: payload === undefined ? body : JSON.stringify(payload)
        });
      } catch (error) {
        env.errors.push(`route ${name}: ${error.message}`);
        await route.fulfill({status: 500, body: 'handler failed'}).catch(() => {});
      }
    });
  }
  return env;
}

async function scenario(page, base, fn) {
  const env = await newEnv(page.context().browser(), base);
  try {
    await fn(env);
    assert.deepEqual(env.errors, [], 'no page or route errors');
  } finally {
    await env.ctx.close();
  }
}

const domMap = page => page.evaluate(MAP_JS);
async function expectMap(page, want) {
  await page.waitForFunction(`(${MAP_JS}) === ${JSON.stringify(want)}`, undefined, {timeout: 8000}).catch(() => {});
  assert.equal(await domMap(page), want);
}
const snap = page => page.evaluate(() => window.demoTour.snapshot());
const observe = (page, detail) => page.evaluate(d => document.dispatchEvent(new CustomEvent('demo:tour-observed', {detail: d})), detail);
const status = page => page.locator('#demo-tour-status').innerText();
const focused = (page, selector) => page.evaluate(s => document.activeElement?.matches(s) ?? false, selector);
const stored = (page, key = KEY) => page.evaluate(k => localStorage.getItem(k), key);
const isUuid = value => UUID.test(String(value));

// ---- 1 ---------------------------------------------------------------------
async function normalMode(page, base) {
  await scenario(page, base, async env => {
    const p = await env.open('/fixtures/demo/normal', false);
    assert.equal(await p.locator('#demo-tour').count(), 0, 'no tour panel');
    // Normal pages carry only {mode:'normal', public_url} (S1); the tour never loads.
    assert.deepEqual(JSON.parse(await p.locator('#demo-config').textContent()), {mode: 'normal', public_url: null});
    assert.equal(await p.locator('script[src*="demo-tour"]').count(), 0, 'no demo-tour.js');
    assert.equal(await p.locator('html').getAttribute('data-demo-mode'), null);
    assert.equal(await p.evaluate(() => typeof window.demoTour), 'undefined');
    assert.equal(env.reqs('state').length, 0, 'no /demo/state request in normal mode');
  });
}

// ---- 2 ---------------------------------------------------------------------
async function firstStateGet(page, base) {
  await scenario(page, base, async env => {
    const p = await env.open('/fixtures/demo/page');
    // §9.1: a bootstrap-bound current-state GET first (no selectors, no facts) ...
    const first = env.reqs('state', p)[0];
    assert.equal(first.query.include_tour, undefined);
    for (const h of ['x-demo-scope', 'x-demo-generation', 'x-demo-auth-epoch']) assert.equal(first.headers[h], undefined, h);
    // ... then the tour facts with the namespace it confirmed.
    const tour = await until(() => env.reqs('state', p).find(r => r.query.include_tour === 'true'), 'facts GET');
    assert.equal(tour.headers['x-demo-scope'], 'scope-a');
    assert.equal(tour.headers['x-demo-generation'], '1');
    assert.equal(tour.headers['x-demo-auth-epoch'], '1');
    await expectMap(p, 'C X P P P P');
    assert.equal(await p.locator('.demo-tour-steps li[aria-current="step"]').count(), 1);
    assert.equal(await p.locator('li[data-demo-step="scenario"]').getAttribute('aria-current'), 'step');
  });
}

// ---- 3 ---------------------------------------------------------------------
async function expectedMapTable(page, base) {
  const full = [ob('history_viewed'), ob('admin_record_viewed'), ob('csv_received')];
  const cases = [
    ['1 teacher, before any session', facts(), [], false, 'C X P P P P'],
    ['2 analysis ready, history not opened', analysisFacts(), [], false, 'C C C X P P'],
    ['3 history reopened', analysisFacts(), [ob('history_viewed')], false, 'C C C C X P'],
    ['4 admin switch, epoch only grows', analysisFacts({auth_epoch: 2}), [ob('history_viewed')], false, 'C C C C X P'],
    ['5 S1 one turn, S2 complete: S2 ignored', facts({journey_session_id: JOURNEY, scenario_created: true}), [ob('history_viewed', {journey_session_id: 8}), ob('csv_received', {journey_session_id: 8})], false, 'C C X P P P'],
    ['6 S2 created after S1 done', analysisFacts(), [ob('history_viewed'), ob('admin_record_viewed', {journey_session_id: 8})], false, 'C C C C X P'],
    ['7 admin viewed + csv', analysisFacts(), full, false, 'C C C C C X'],
    ['8 S1-only selected download = same csv_received', analysisFacts(), [ob('admin_record_viewed'), ob('csv_received')], false, 'C C C C C X'],
    ['9a mixed selection, no earlier success', analysisFacts(), [ob('history_viewed')], false, 'C C C C X P'],
    ['9b mixed selection, earlier success kept', analysisFacts(), full, false, 'C C C C C X'],
    ['10a csv abort, no earlier success', analysisFacts(), [ob('admin_record_viewed')], false, 'C C C C X P'],
    ['10b csv abort, earlier success kept', analysisFacts(), full, false, 'C C C C C X'],
    ['11 teacher/session deleted tombstone, before reset', deletedFacts(), full, false, 'C C C C C X'],
    ['12 deleted before csv: unseen stays pending', deletedFacts(), [], false, 'C C C X P P'],
    ['13 refresh after csv, later epoch', analysisFacts({auth_epoch: 3}), full, false, 'C C C C C X'],
    ['14a reset success: old scope shows all complete once', deletedFacts(), full, true, 'C C C C C C'],
    ['14b reset success: new admin generation (old observations ignored)', facts({generation: 2, teacher_entered: false}), full, false, 'X P P P P P'],
    ['14c no facts at all', null, [], false, 'X P P P P P']
  ];
  await scenario(page, base, async env => {
    const p = await env.open('/fixtures/demo/page');
    const got = await p.evaluate(rows => rows.map(([, f, o, done]) => window.demoTour.mapString(window.demoTour.computeSteps(f, o, done))), cases);
    cases.forEach(([name, , , , want], i) => assert.equal(got[i], want, `expected map row ${name}`));
    assert.equal(await p.evaluate(() => window.demoTour.storageKey('scope-a', 1)), KEY);
  });
}

// ---- 4 ---------------------------------------------------------------------
async function observationsAndStorage(page, base) {
  await scenario(page, base, async env => {
    env.current = state({tour_facts: analysisFacts()});
    const p = await env.open('/fixtures/demo/page');
    await expectMap(p, 'C C C X P P');
    const invalid = [
      ['wrong epoch', ob('history_viewed', {auth_epoch: 2})],
      ['wrong generation', ob('history_viewed', {generation: 2})],
      ['wrong journey', ob('history_viewed', {journey_session_id: 8})],
      ['bad kind', ob('page_viewed')],
      ['non-UUID request_id', ob('history_viewed', {request_id: 'not-a-uuid'})],
      ['uppercase request_id', ob('history_viewed', {request_id: randomUUID().toUpperCase()})],
      ['bad schema_version', ob('history_viewed', {schema_version: 2})]
    ];
    for (const [name, detail] of invalid) {
      await observe(p, detail);
      assert.equal(await domMap(p), 'C C C X P P', `${name}: map unchanged`);
      assert.deepEqual((await snap(p)).observations, [], `${name}: nothing stored in memory`);
    }
    const stray = await stored(p);
    assert(stray === null || JSON.parse(stray).observations.length === 0, 'invalid observations never persisted');

    const valid = ob('history_viewed');
    await observe(p, valid);
    await expectMap(p, 'C C C C X P');
    const raw = await stored(p);
    const record = JSON.parse(raw);
    assert.equal(record.schema_version, 1);
    assert.equal(record.journey_session_id, JOURNEY);
    assert.equal(record.observations.length, 1);
    assert.equal(record.observations[0].kind, 'history_viewed');
    assert.equal(record.observations[0].request_id, valid.request_id);
    assert(!/session_id,scenario_id|csrf-v1/.test(raw), 'no CSV text or CSRF value in storage');
    assert.equal(await p.evaluate(() => Object.keys(localStorage).filter(k => k.endsWith(':tour-v1')).join()), KEY, 'key has no auth_epoch');

    const sent = env.reqs('state', p).length;
    await p.reload();
    await until(() => env.reqs('state', p).length > sent, 'state GET after reload');
    await expectMap(p, 'C C C C X P');

    const second = await env.open('/fixtures/demo/page');
    await expectMap(second, 'C C C C X P');
    await observe(second, ob('admin_record_viewed'));
    await until(async () => (await snap(p)).observations.some(o => o.kind === 'admin_record_viewed'), 'first page merges admin_record_viewed via storage event');
    const merged = (await snap(p)).observations.map(o => o.kind).sort();
    assert.deepEqual(merged, ['admin_record_viewed', 'history_viewed']);
    assert.equal(await domMap(p), 'C C C C X P', 'merge does not change the map without csv');
  });
}

// ---- 5 ---------------------------------------------------------------------
async function roleSwitch(page, base) {
  await scenario(page, base, async env => {
    env.handlers.role = (req, e) => {
      e.current = state({role: 'admin', auth_epoch: 2, next_url: NEXT_ADMIN, request_id: req.json.request_id, tour_facts: facts({auth_epoch: 2})});
      return {json: e.current};
    };
    const p = await env.open('/fixtures/demo/page');
    await p.click('[data-demo-action="role"][data-demo-role="admin"]');
    await p.waitForURL(url => url.pathname + url.search === NEXT_ADMIN, {timeout: 10000});

    const [post] = env.posts('role');
    assert.equal(env.posts('role').length, 1);
    assert(isUuid(post.json.request_id), 'canonical lowercase UUID request_id');
    assert.equal(post.json.role, 'admin');
    assert.equal(post.json.expected_generation, 1);
    assert.equal(post.json.expected_auth_epoch, 1);
    assert.match(post.headers['content-type'], /application\/json/);
    assert.equal(post.headers['x-csrf-token'], 'csrf-v1');
    const afterPost = env.requests.slice(env.requests.indexOf(post) + 1).find(r => r.name === 'state');
    assert(afterPost, 'a state GET follows the POST');
    assert.equal(afterPost.query.request_id, undefined, 'plain state GET');

    const changed = (await until(() => env.eventsOf(p, 'demo:role-changed')[0], 'demo:role-changed')).detail;
    assert.deepEqual(Object.keys(changed).sort(), DETAIL_KEYS);
    assert.equal(changed.role, 'admin');
    assert.equal(changed.auth_epoch, 2);
    assert.equal(changed.state, 'ready');
    assert.equal(changed.reason, null);
    assert(!JSON.stringify(changed).includes('csrf-v1'), 'no CSRF value in event');
  });
}

// ---- 6 ---------------------------------------------------------------------
async function lostResponse(page, base) {
  await scenario(page, base, async env => {
    let calls = 0;
    env.handlers.role = (req, e) => {
      calls++;
      e.current = state({role: 'admin', auth_epoch: 2, request_id: req.json.request_id, tour_facts: facts({auth_epoch: 2})});
      return {abort: true};
    };
    env.handlers.state = (req, e) => req.query.request_id
      ? {json: receipt('role', req.query.request_id, e.current)}
      : serverState(req, e);
    const p = await env.open('/fixtures/demo/page');
    await p.click('[data-demo-action="role"][data-demo-role="admin"]');
    const [post] = await until(() => env.posts('role').length && env.posts('role'), 'role POST');
    const lookup = await until(() => env.requests.slice(env.requests.indexOf(post) + 1)[0], 'request after the lost POST');
    assert.equal(lookup.name, 'state');
    assert.equal(lookup.method, 'GET');
    assert.equal(lookup.query.request_id, post.json.request_id, 'lookup reuses the same request_id');
    await until(() => env.requests.slice(env.requests.indexOf(lookup) + 1).some(r => r.name === 'state' && !r.query.request_id), 'resync state GET after lookup');
    assert.equal(calls, 1, 'no second POST');
    assert.equal(env.posts('role').length, 1);
  });
}

// ---- 7 ---------------------------------------------------------------------
async function csrfInvalid(page, base) {
  await scenario(page, base, async env => {
    env.handlers.role = () => ({status: 403, json: envelope('DEMO_CSRF_INVALID', 'demo.csrf_refresh')});
    const p = await env.open('/fixtures/demo/page');
    await p.click('[data-demo-action="role"][data-demo-role="admin"]');
    const [post] = await until(() => env.posts('role').length && env.posts('role'), 'role POST');
    const rest = () => env.requests.slice(env.requests.indexOf(post) + 1);
    const lookup = await until(() => rest().find(r => r.name === 'state' && r.query.request_id), 'request_id lookup');
    assert.equal(lookup.query.request_id, post.json.request_id);
    const before = rest().filter(r => r.name === 'state' && !r.query.request_id && rest().indexOf(r) < rest().indexOf(lookup));
    assert(before.length >= 1, 'a plain state GET precedes the lookup');
    await delay(300);
    assert.equal(env.posts('role').length, 1, 'no second POST');
    assert(!(await status(p)).includes('csrf-v1'));
    assert.notEqual((await status(p)).trim(), '', 'recovery status shown');
    const pending = JSON.parse(await p.evaluate(() => sessionStorage.getItem('demo:control-v1')));
    assert.equal(pending.request_id, post.json.request_id, '404 lookup keeps the pending op with the same id');
    assert(!JSON.stringify(pending).includes('csrf-v1'), 'no CSRF value in pending op');
  });
}

// ---- 8 ---------------------------------------------------------------------
async function resetFlow(page, base) {
  await scenario(page, base, async env => {
    const polls = [];
    let resetId;
    env.current = state({role: 'admin', tour_facts: deletedFacts()});
    env.handlers.reset = (req, e) => {
      resetId = req.json.request_id;
      e.current = state({state: 'resetting', generation: 2, auth_epoch: 2, role: 'admin', request_id: resetId, tour_facts: null});
      return {status: 202, json: e.current};
    };
    env.handlers.state = (req, e) => {
      if (!req.query.request_id) return serverState(req, e);
      polls.push(e.events.some(x => x.name === 'demo:reset-completed'));
      if (polls.length === 1) return {status: 202, json: e.current};
      e.current = state({generation: 2, auth_epoch: 2, role: 'admin', next_url: '/fixtures/demo/page?g=2&e=2&role=admin',
        tour_facts: facts({generation: 2, auth_epoch: 2, teacher_entered: false})});
      return {json: receipt('reset', req.query.request_id, e.current)};
    };
    const p = await env.open('/fixtures/demo/page?role=admin');
    for (const kind of ['history_viewed', 'admin_record_viewed', 'csv_received']) await observe(p, ob(kind));
    await expectMap(p, 'C C C C C X');
    await p.evaluate(() => {
      window.__maps = [];
      window.__status = [];
      const ol = document.querySelector('.demo-tour-steps');
      const lis = [...ol.children];
      const letter = {complete: 'C', current: 'X', pending: 'P'};
      const cur = new Map(lis.map(li => [li, li.dataset.state]));
      const push = value => { if (window.__maps.at(-1) !== value) window.__maps.push(value); };
      push(lis.map(li => letter[cur.get(li)]).join(' '));
      // Replays every data-state write so a render that is overwritten in the same task is still seen.
      new MutationObserver(records => {
        const latest = new Map(lis.map(li => [li, li.dataset.state]));
        const after = [];
        for (let i = records.length - 1; i >= 0; i--) {
          after[i] = latest.get(records[i].target);
          latest.set(records[i].target, records[i].oldValue);
        }
        records.forEach((record, i) => {
          cur.set(record.target, after[i]);
          push(lis.map(li => letter[cur.get(li)] ?? '?').join(' '));
        });
      }).observe(ol, {subtree: true, attributes: true, attributeFilter: ['data-state'], attributeOldValue: true});
      new MutationObserver(records => {
        for (const r of records) window.__status.push(r.type === 'characterData' ? r.target.data : [...r.addedNodes].map(n => n.textContent).join(''));
      }).observe(document.querySelector('#demo-tour-status'), {childList: true, characterData: true, subtree: true});
    });

    // dismissed confirm -> no POST
    let question;
    p.once('dialog', dialog => { question = dialog.message(); return dialog.dismiss(); });
    await p.click('[data-demo-action="reset"]');
    await until(() => question !== undefined, 'confirm dialog');
    assert.equal(question, await p.getAttribute('[data-demo-action="reset"]', 'data-confirm'));
    assert.notEqual(question.trim(), '');
    await delay(300);
    assert.equal(env.posts('reset').length, 0, 'dismissed confirm sends nothing');

    // accepted confirm -> POST, 202, polls, ready g2
    p.once('dialog', dialog => dialog.accept());
    await p.click('[data-demo-action="reset"]');
    // While resetting (generation 2 reserved), the previous journey is kept, never completed.
    await until(() => env.eventsOf(p, 'demo:reset-started').length, 'demo:reset-started');
    assert.equal(await domMap(p), 'C C C C C X', 'resetting is not a reset success');
    assert.notEqual(await stored(p), null, 'old journey kept while the reset is pending');
    await until(() => env.eventsOf(p, 'demo:reset-completed').length, 'demo:reset-completed', 20000);
    assert.equal(env.posts('reset').length, 1);
    const post = env.posts('reset')[0];
    assert.deepEqual(Object.keys(post.json).sort(), ['confirm', 'expected_auth_epoch', 'expected_generation', 'request_id']);
    assert(isUuid(post.json.request_id));
    assert.equal(post.json.expected_generation, 1);
    assert.equal(post.json.expected_auth_epoch, 1);
    assert.equal(post.json.confirm, true);
    assert.equal(post.headers['x-csrf-token'], 'csrf-v1');

    const lookups = env.reqs('state').filter(r => r.query.request_id);
    assert(lookups.length >= 2, 'polled until non-202');
    assert(lookups.every(r => r.query.request_id === resetId), 'polls reuse the reset request_id');
    assert.deepEqual(polls.filter(Boolean), [], 'no reset-completed while the reset was still 202');
    const names = env.eventsOf(p, 'demo:reset-started', 'demo:reset-completed').map(e => e.name);
    assert.deepEqual(names, ['demo:reset-started', 'demo:reset-completed']);
    const started = env.eventsOf(p, 'demo:reset-started')[0].detail;
    const completed = env.eventsOf(p, 'demo:reset-completed')[0].detail;
    assert.equal(started.state, 'resetting');
    assert.equal(completed.state, 'ready');
    assert.equal(completed.generation, 2);

    // §24.1: the previous (now stale) panel shows the six completes once with the reset notice ...
    await expectMap(p, 'C C C C C C');
    const {maps, texts, label} = await p.evaluate(() => ({maps: window.__maps, texts: window.__status, label: document.querySelector('#demo-tour').dataset.labelResetDone}));
    assert.equal(maps.filter(m => m === 'C C C C C C').length, 1, `all six complete shown once (saw ${maps.join(' | ')})`);
    assert(label && texts.includes(label), 'reset-done status shown');
    assert.equal(await p.getAttribute('#demo-tour', 'data-stale'), 'true');
    assert.equal(await stored(p), null, 'old generation key removed');
    // ... and the new space starts over: restart opens the server-made next_url.
    await Promise.all([p.waitForURL('**/fixtures/demo/page?g=2&e=2&role=admin'), p.click('[data-demo-action="restart"]')]);
    await expectMap(p, 'X P P P P P');
    assert.deepEqual((await snap(p)).observations, [], 'new generation starts with no observations');
  });
}

// ---- 9 ---------------------------------------------------------------------
async function lowerGenerationIgnored(page, base) {
  await scenario(page, base, async env => {
    env.current = state({generation: 2, tour_facts: analysisFacts({generation: 2})});
    const p = await env.open('/fixtures/demo/page?g=2');
    await expectMap(p, 'C C C X P P');
    const events = env.events.length;
    const gets = env.reqs('state').length;
    env.current = state({generation: 1, tour_facts: facts()});
    await p.evaluate(() => window.demoTour.check('test'));
    assert.equal(env.reqs('state').length, gets + 1, 'one state GET');
    assert.equal(await domMap(p), 'C C C X P P', 'lower generation not rendered');
    assert.equal((await snap(p)).state.generation, 2);
    await p.evaluate(() => 0);
    assert.equal(env.events.length, events, 'no event for an ignored state');
  });
}

// ---- 10 --------------------------------------------------------------------
async function stateCheckEvent(page, base) {
  await scenario(page, base, async env => {
    const p = await env.open('/fixtures/demo/page');
    await expectMap(p, 'C X P P P P'); // the initial facts GET has landed
    const gets = env.reqs('state').length;
    env.current = state({role: 'admin', auth_epoch: 2, tour_facts: facts({auth_epoch: 2})});
    await p.evaluate(() => document.dispatchEvent(new CustomEvent('demo:state-check', {detail: {reason: 'DEMO_STALE_GENERATION'}})));
    await until(() => env.reqs('state').length > gets, 'state GET after demo:state-check');
    const event = (await until(() => env.eventsOf(p, 'demo:role-changed')[0], 'event built from the GET')).detail;
    assert.equal(event.role, 'admin');
    assert.equal(event.auth_epoch, 2);
    assert.equal(event.reason, null, 'the check reason is never applied as state');
    const after = env.reqs('state').slice(gets);
    assert.equal(after[0].query.include_tour, undefined, 'first a plain current-state GET');
    assert.equal(after[0].headers['x-demo-auth-epoch'], undefined, 'without the old selectors');
    const tour = await until(() => env.reqs('state').slice(gets).find(r => r.query.include_tour === 'true'), 'facts GET');
    assert.equal(tour.headers['x-demo-auth-epoch'], '2', 'facts use the confirmed namespace');
    await until(async () => (await snap(p)).facts?.auth_epoch === 2, 'facts of the confirmed namespace applied');
  });
}

// ---- 8b: a reset that fails is never shown as done; a new attempt gets a new ID -
async function resetFailure(page, base) {
  await scenario(page, base, async env => {
    const ids = [];
    env.current = state({role: 'admin', tour_facts: deletedFacts()});
    const failed = () => state({state: 'reset_failed', generation: 2, auth_epoch: 2, role: null, last_error: 'DEMO_RESET_FAILED', tour_facts: null});
    env.handlers.reset = (req, e) => {
      ids.push(req.json.request_id);
      if (ids.length === 1) {
        e.current = state({state: 'resetting', generation: 2, auth_epoch: 2, role: 'admin', request_id: req.json.request_id, tour_facts: null});
        return {status: 202, json: e.current};
      }
      e.current = failed();
      return {status: 503, json: envelope('DEMO_RESET_FAILED', 'demo.reset_failed', true)};
    };
    env.handlers.state = (req, e) => {
      if (!req.query.request_id) return serverState(req, e);
      e.current = failed();
      return {status: 503, json: envelope('DEMO_RESET_FAILED', 'demo.reset_failed', true)};
    };
    const p = await env.open('/fixtures/demo/page?role=admin');
    for (const kind of ['history_viewed', 'admin_record_viewed', 'csv_received']) await observe(p, ob(kind));
    await expectMap(p, 'C C C C C X');
    p.on('dialog', dialog => dialog.accept());

    // Polled reset ends in reset_failed: no success, no completion, the old journey stays.
    await p.click('[data-demo-action="reset"]');
    await until(async () => (await snap(p)).state.state === 'reset_failed', 'reset_failed applied', 15000);
    assert.equal(env.eventsOf(p, 'demo:reset-completed').length, 0);
    assert.equal(await domMap(p), 'C C C C C X', 'reset_failed never freezes completion');
    assert.notEqual(await stored(p), null);
    assert.equal(await p.evaluate(() => sessionStorage.getItem('demo:control-v1')), null, 'finished failure retires its ID');

    // A direct finished failure: state GET + same-ID receipt lookup, then the ID is retired.
    const before = env.requests.length;
    await p.click('[data-demo-action="reset"]');
    await until(() => env.posts('reset').length === 2, 'second reset POST');
    const second = env.posts('reset')[1].json;
    assert.notEqual(second.request_id, ids[0], 'a new attempt gets a new ID');
    assert(isUuid(second.request_id));
    assert.equal(second.expected_generation, 2, 'with the current generation');
    assert.equal(second.expected_auth_epoch, 2, 'and the current epoch');
    const later = () => env.requests.slice(before).filter(r => r.name === 'state');
    await until(() => later().some(r => !r.query.request_id) && later().some(r => r.query.request_id === second.request_id), 'resync and receipt lookup');
    await until(async () => (await p.evaluate(() => sessionStorage.getItem('demo:control-v1'))) === null, 'retired');
    assert.equal(env.eventsOf(p, 'demo:reset-completed').length, 0);
    assert.equal(await domMap(p), 'C C C C C X');
  });
}

// ---- 11 --------------------------------------------------------------------
async function tabNotification(page, base) {
  await scenario(page, base, async env => {
    const a = await env.open('/fixtures/demo/page');
    const b = await env.open('/fixtures/demo/page');
    for (const tab of [a, b]) await expectMap(tab, 'C X P P P P'); // initial facts GETs have landed
    const message = o => ({browser_scope_id: 'scope-a', generation: 2, auth_epoch: 1, state: 'ready', role: 'teacher', contract_version: 'd0-03-v1-r3', content_version: 'd0-02-v1', fixture_version: 'demo-fixture-v1', reason: null, ...o});
    const send = detail => a.evaluate(d => {
      const channel = new BroadcastChannel('misconcept-demo-v1');
      channel.postMessage(d);
      setTimeout(() => channel.close(), 200);
    }, detail);
    const gets = () => env.reqs('state', b).length;
    const before = gets();
    await send(message({browser_scope_id: 'scope-b', generation: 3}));
    await delay(300);
    assert.equal(gets(), before, 'other scope is ignored');
    await send(message());
    await until(() => gets() > before, 'page B re-reads state after the notification');
    assert.equal((await snap(b)).state.generation, 1, 'the notification detail itself is not applied');
    assert.equal(env.eventsOf(b).filter(e => e.detail.generation === 2).length, 0, 'no event from the notification detail');
  });
}

// ---- 12 --------------------------------------------------------------------
async function tourCsv(page, base) {
  await scenario(page, base, async env => {
    let mode = 'ok';
    env.handlers.download = req => {
      const echo = req.headers['x-demo-export-request-id'];
      if (mode === 'status') return {status: 500, json: envelope('DEMO_DB_LOCKED', 'demo.retry_later', true)};
      const csv = buildCsv({journey: mode === 'session' ? 8 : JOURNEY, origin: mode === 'origin' ? 'seed_prepared' : 'visitor_generated', dropLast: mode === 'columns'});
      // One raw 0xff byte inside a message cell: structure stays valid, UTF-8 does not.
      const at = csv.text.indexOf('plain');
      const body = mode === 'utf8'
        ? Buffer.concat([Buffer.from(csv.text.slice(0, at)), Buffer.from([0xff]), Buffer.from(csv.text.slice(at))])
        : csv.text;
      return {
        body,
        headers: {
          'content-type': 'text/csv; charset=utf-8',
          'content-disposition': 'attachment; filename=demo_d0-02-v1_admin_7.csv',
          'x-demo-export-request-id': mode === 'echo' ? randomUUID() : echo,
          'x-demo-export-row-count': String(mode === 'count' ? csv.rowCount + 1 : csv.rowCount)
        }
      };
    };
    env.current = state({role: 'admin', tour_facts: analysisFacts()});
    const p = await env.open('/fixtures/demo/page?role=admin');
    const arm = async () => {
      await p.evaluate(() => localStorage.clear());
      const gets = env.reqs('state', p).length;
      await p.reload();
      await until(() => env.reqs('state', p).length > gets, 'state GET after reload');
      await expectMap(p, 'C C C X P P');
      await observe(p, ob('admin_record_viewed'));
      await expectMap(p, 'C C C C X P');
      await p.locator('#demo-tour-csv').waitFor({state: 'visible'});
    };

    for (const failure of ['echo', 'count', 'columns', 'session', 'status', 'origin', 'utf8']) {
      mode = failure;
      await arm();
      const requests = env.reqs('download').length;
      await p.click('#demo-tour-csv');
      await until(() => env.reqs('download').length > requests, `${failure}: download request`);
      await until(async () => (await status(p)).trim() !== '', `${failure}: failure status`);
      await delay(150);
      assert.equal(env.downloads.length, 0, `${failure}: no download`);
      const snapshot = await snap(p);
      assert(!snapshot.observations.some(o => o.kind === 'csv_received'), `${failure}: no csv_received`);
      assert.equal(snapshot.map.analysis, 'complete');
      assert.equal(await domMap(p), 'C C C C X P', `${failure}: map unchanged`);
    }

    mode = 'ok';
    await arm();
    const [download] = await Promise.all([p.waitForEvent('download', {timeout: 10000}), p.click('#demo-tour-csv')]);
    assert.equal(download.suggestedFilename(), 'demo_d0-02-v1_admin_7.csv');
    const bytes = await readFile(await download.path());
    assert.deepEqual([...bytes.subarray(0, 3)], [0xef, 0xbb, 0xbf], 'download keeps the BOM');
    await expectMap(p, 'C C C C C X');
    const request = env.reqs('download').at(-1);
    assert.equal(request.path, `/admin/sessions/${JOURNEY}/download`);
    assert.equal(request.headers['x-demo-scope'], 'scope-a');
    assert.equal(request.headers['x-demo-generation'], '1');
    assert.equal(request.headers['x-demo-auth-epoch'], '1');
    assert(isUuid(request.headers['x-demo-export-request-id']));
    const observed = (await snap(p)).observations.find(o => o.kind === 'csv_received');
    assert.equal(observed.request_id, request.headers['x-demo-export-request-id']);
    assert.equal(observed.journey_session_id, JOURNEY);
    const raw = await stored(p);
    assert(raw.includes('csv_received') && !raw.includes('session_id,scenario_id'), 'storage keeps the observation, not the CSV');
  });
}

// ---- 13 --------------------------------------------------------------------
async function guideActions(page, base) {
  await scenario(page, base, async env => {
    const p = await env.open('/fixtures/demo/page');
    const panel = p.locator('#demo-tour');
    const resume = p.locator('.demo-tour-resume');
    await expectMap(p, 'C X P P P P');
    assert(await panel.isVisible());
    assert(await resume.isHidden(), 'resume hidden while the panel is open');

    await p.click('[data-demo-action="close"]');
    await panel.waitFor({state: 'hidden'});
    assert(await resume.isVisible());
    assert(await focused(p, '.demo-tour-resume'), 'focus moves to the resume button');
    await p.click('.demo-tour-resume');
    await panel.waitFor({state: 'visible'});
    assert(await focused(p, '#demo-tour-title'), 'focus moves to the tour title');

    await p.click('[data-demo-action="skip"]');
    await panel.waitFor({state: 'hidden'});
    assert.equal(JSON.parse(await stored(p)).skipped, true);
    const sent = env.reqs('state', p).length;
    await p.reload();
    await until(() => env.reqs('state', p).length > sent, 'state GET after reload');
    await expectMap(p, 'C X P P P P');
    assert(await panel.isHidden(), 'skip persists across reload');
    assert(await resume.isVisible());

    await p.click('.demo-tour-resume');
    await panel.waitFor({state: 'visible'});
    const gets = env.reqs('state', p).length;
    await p.click('[data-demo-action="restart"]');
    await until(() => env.reqs('state', p).length > gets, 'restart triggers a state GET');
    assert(await panel.isVisible());
    assert(await focused(p, '#demo-tour-title'));
    assert.equal(JSON.parse(await stored(p)).skipped, false);
  });
}

// ---- 14 --------------------------------------------------------------------
async function expiredEnvelope(page, base) {
  await scenario(page, base, async env => {
    env.handlers.state = () => ({status: 410, json: envelope('DEMO_EXPIRED', 'demo.expired')});
    const p = await env.open('/fixtures/demo/page');
    const detail = (await until(() => env.eventsOf(p, 'demo:expired')[0], 'demo:expired')).detail;
    assert.deepEqual(Object.keys(detail).sort(), DETAIL_KEYS);
    assert.equal(detail.state, 'expired');
    assert.equal(detail.reason, 'DEMO_EXPIRED');
    await until(async () => (await status(p)).trim() !== '', 'recovery status text');
    for (const selector of ['[data-demo-action="role"]', '[data-demo-action="reset"]', '#demo-tour-csv']) {
      assert.equal(await p.locator(`${selector}:visible`).count(), 0, `${selector} hidden after expiry`);
    }
  });
}

// ---- 15 --------------------------------------------------------------------
async function layout(page, base) {
  const out = new URL('../test-results/d3-01/', import.meta.url);
  await mkdir(out, {recursive: true});
  await scenario(page, base, async env => {
    env.current = state({role: 'admin', tour_facts: analysisFacts()});
    const p = await env.open('/fixtures/demo/page?role=admin');
    await observe(p, ob('history_viewed'));
    await p.locator('#demo-tour-csv').waitFor({state: 'visible'});
    assert.equal(await p.getAttribute('#demo-tour-status', 'aria-live'), 'polite');
    for (const [width, height] of [[390, 844], [1440, 900], [720, 450]]) {
      await p.setViewportSize({width, height});
      const report = await p.evaluate(() => {
        window.scrollTo(0, 0);
        const panel = document.querySelector('#demo-tour');
        const panelRect = panel.getBoundingClientRect();
        const mainTop = document.querySelector('#main-content').getBoundingClientRect().top;
        const buttons = [...panel.querySelectorAll('button')].filter(b => b.checkVisibility()).map(b => {
          b.scrollIntoView({block: 'center'});
          const r = b.getBoundingClientRect();
          const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          return {
            name: b.id || b.dataset.demoAction + (b.dataset.demoRole ? `:${b.dataset.demoRole}` : ''),
            inside: r.left >= 0 && r.right <= innerWidth && r.left >= panelRect.left - 0.5 && r.right <= panelRect.right + 0.5,
            textClipped: b.scrollWidth > b.clientWidth + 1 || b.scrollHeight > b.clientHeight + 1,
            covered: !(top === b || b.contains(top)),
            size: r.width * r.height
          };
        });
        window.scrollTo(0, 0);
        return {panelBottom: panel.getBoundingClientRect().bottom, mainTop, buttons, scrollWidth: document.documentElement.scrollWidth, innerWidth};
      });
      const at = `${width}x${height}`;
      assert(report.scrollWidth <= report.innerWidth, `${at}: no horizontal page scroll`);
      assert(report.panelBottom <= report.mainTop + 0.5, `${at}: panel (${report.panelBottom}) does not overlap main (${report.mainTop})`);
      assert.deepEqual(report.buttons.map(b => b.name).sort(), ['close', 'demo-tour-csv', 'reset', 'restart', 'role:teacher', 'skip'], `${at}: visible panel buttons`);
      for (const b of report.buttons) {
        assert(b.inside, `${at}: ${b.name} inside panel and viewport width`);
        assert(!b.textClipped, `${at}: ${b.name} text not clipped`);
        assert(!b.covered, `${at}: ${b.name} not covered`);
        assert(b.size > 0, `${at}: ${b.name} has size`);
      }
      await p.evaluate(() => { window.scrollTo(0, 0); document.activeElement?.blur(); });
      let reached = false;
      for (let i = 0; i < 40 && !reached; i++) {
        await p.keyboard.press('Tab');
        reached = await focused(p, '#demo-tour [data-demo-action="close"]');
      }
      assert(reached, `${at}: Tab reaches the close button`);
      await p.evaluate(() => window.scrollTo(0, 0));
      await p.screenshot({path: new URL(`layout-${at}.png`, out).pathname});
    }
  });
}

// ---- 16 --------------------------------------------------------------------
async function entryPage(page, base) {
  await scenario(page, base, async env => {
    let started = false;
    env.handlers.state = (req, e) => started
      ? serverState(req, e)
      : {status: 401, json: envelope('DEMO_SESSION_REQUIRED', 'demo.start_required')};
    env.handlers.start = (req, e) => {
      if (env.posts('start').length === 1) return {status: 503, headers: {'retry-after': '30'}, json: envelope('DEMO_CAPACITY', 'demo.capacity', true)};
      started = true;
      e.current = state({role: 'teacher', next_url: '/fixtures/demo/page', request_id: req.json.request_id});
      return {status: 201, json: e.current};
    };
    const p = await env.open('/fixtures/demo/entry');
    assert.equal(await p.locator('#demo-tour').count(), 0, 'no tour panel on the entry page');
    assert.equal(await p.locator('[data-demo-step]').count(), 0, 'no tour steps on the entry page');
    assert.deepEqual(await p.locator('[data-demo-action="start"]').evaluateAll(b => b.map(x => x.dataset.demoRole)), ['teacher', 'admin']);
    assert.equal(await p.getAttribute('#demo-tour-status', 'aria-live'), 'polite');
    await until(async () => (await status(p)).trim() !== '', 'recovery status after the 401 state GET');

    for (const [width, height] of [[390, 844], [1440, 900], [720, 450]]) {
      await p.setViewportSize({width, height});
      const report = await p.evaluate(() => {
        window.scrollTo(0, 0);
        const buttons = [...document.querySelectorAll('[data-demo-action="start"]')].map(b => {
          b.scrollIntoView({block: 'center'});
          const r = b.getBoundingClientRect();
          return {
            name: b.dataset.demoRole, visible: b.checkVisibility() && r.width > 0 && r.height > 0,
            inside: r.left >= 0 && r.right <= innerWidth,
            textClipped: b.scrollWidth > b.clientWidth + 1 || b.scrollHeight > b.clientHeight + 1
          };
        });
        window.scrollTo(0, 0);
        return {buttons, scrollWidth: document.documentElement.scrollWidth, innerWidth};
      });
      const at = `${width}x${height}`;
      assert(report.scrollWidth <= report.innerWidth, `${at}: no horizontal page scroll`);
      for (const b of report.buttons) {
        assert(b.visible && b.inside, `${at}: ${b.name} start button visible inside the viewport`);
        assert(!b.textClipped, `${at}: ${b.name} start button text not clipped`);
      }
    }

    // 503 + Retry-After is a finished failure (§9.1): resync, then the ID is retired.
    await p.click('[data-demo-action="start"][data-demo-role="teacher"]');
    await until(() => env.posts('start').length, 'first start POST');
    await until(async () => (await p.evaluate(() => sessionStorage.getItem('demo:control-v1'))) === null, 'failed ID retired');
    await until(async () => (await status(p)).trim().endsWith('(30s)'), 'capacity status with the Retry-After seconds');
    assert.equal(new URL(p.url()).pathname, '/fixtures/demo/entry', 'no navigation after a 503');
    const resync = env.requests.slice(env.requests.indexOf(env.posts('start')[0]) + 1).filter(r => r.name === 'state');
    assert(resync.some(r => !r.query.request_id), 'a current-state GET follows the failure');
    await p.click('[data-demo-action="start"][data-demo-role="teacher"]');
    await p.waitForURL(url => url.pathname === '/fixtures/demo/page', {timeout: 10000});
    const [first, second] = env.posts('start');
    assert.equal(env.posts('start').length, 2);
    assert(isUuid(first.json.request_id), 'canonical lowercase UUID request_id');
    assert(isUuid(second.json.request_id));
    assert.notEqual(second.json.request_id, first.json.request_id, 'a new attempt gets a new request_id');
    for (const post of [first, second]) {
      assert.equal(post.json.role, 'teacher');
      assert.equal(post.headers['x-csrf-token'], 'csrf-v1');
    }
    const afterPost = env.requests.slice(env.requests.indexOf(second) + 1).find(r => r.name === 'state');
    assert(afterPost, 'a state GET follows the successful POST');
    assert.equal(afterPost.query.request_id, undefined, 'plain state GET');
    await p.locator('#demo-tour').waitFor({state: 'attached'});
  });
}

// ---- 17 --------------------------------------------------------------------
async function demoErrorPage(page, base) {
  const catalog = JSON.parse(await readFile(new URL('../src/i18n/catalog.json', import.meta.url), 'utf8')).demo;
  await scenario(page, base, async env => {
    for (const [code, recovery] of [[410, 'demo.expired'], [409, 'demo.stale_role']]) {
      const p = await env.ctx.newPage();
      p.on('pageerror', error => env.errors.push(error.message));
      const response = await p.goto(`${base}/fixtures/demo/error?status=${code}&recovery=${recovery}`);
      assert.equal(response.status(), code);
      const texts = Object.values(catalog[recovery.split('.')[1]]);
      const title = (await p.locator('h2#error-title').innerText()).trim();
      assert(texts.includes(title), `${code}: h2 shows the catalog text for ${recovery} (got "${title}")`);
      assert((await p.title()).includes(title), `${code}: <title> carries the recovery text`);
      const entry = p.locator('.empty-state a[href="/demo"]');
      assert(await entry.isVisible(), `${code}: link back to /demo visible`);
      assert.equal(await p.locator('.empty-state a[href="/scenarios"]').count(), 1, `${code}: scenarios link kept`);
      await p.evaluate(() => document.activeElement?.blur());
      let reached = false;
      for (let i = 0; i < 40 && !reached; i++) {
        await p.keyboard.press('Tab');
        reached = await focused(p, '.empty-state a[href="/demo"]');
      }
      assert(reached, `${code}: Tab reaches the /demo link`);
      await p.close();
    }
  });
}

export default async function check(page) {
  const base = new URL(page.url()).origin;
  // Spec items 1–17 in order; a failure names its scenario.
  for (const fn of [normalMode, firstStateGet, expectedMapTable, observationsAndStorage, roleSwitch, lostResponse,
    csrfInvalid, resetFlow, resetFailure, lowerGenerationIgnored, stateCheckEvent, tabNotification, tourCsv, guideActions,
    expiredEnvelope, layout, entryPage, demoErrorPage]) {
    try {
      await fn(page, base);
    } catch (error) {
      error.message = `${fn.name}: ${error.message}`;
      throw error;
    }
  }
  return {pageErrors: []};
}
