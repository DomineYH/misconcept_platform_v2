import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
import {mkdir} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
import {control, namespace, open, ready, server, state} from './browser_demo_isolation.mjs';

// D4-02 (#204) I: recovery, language and accessibility on the real app (§25, §24, §9); the runner's fixture page is unused.
// One failing case does not hide the others: every case's failure is collected and rethrown at the end.
const SHOTS = new URL('../test-results/d4-02/', import.meta.url);
// D0-02 §4 recommended inputs (src/demo/seed.py INPUTS): the only ones with prepared student replies.
const INPUT = {A: '1/8이 1/4보다 크다고 생각한 이유를 말해 줄래?', common: '같은 크기의 종이띠 두 개를 각각 똑같이 4조각과 8조각으로 나누어 한 조각씩 겹쳐 보자. 어느 쪽 조각이 더 크고, 조각 수가 늘면 한 조각의 크기는 어떻게 될까?'};
const SUBMIT = '#teacher-form [type="submit"]';
const tick = ms => new Promise(resolve => setTimeout(resolve, ms));

async function until(fn, what, ms = 60000) {
  const end = Date.now() + ms;
  for (;;) {
    const value = await fn();
    if (value) return value;
    assert(Date.now() < end, `timeout: ${typeof what === 'function' ? await what() : what}`);
    await tick(100);
  }
}

const get = (page, path) => page.evaluate(async path => {
  const response = await fetch(path);
  return {status: response.status, body: await response.json().catch(() => null)};
}, path);
const stream = (page, id, ns) => page.evaluate(async ({id, ns}) => {
  const token = document.cookie.split('; ').find(item => item.startsWith('demo_csrftoken='))?.slice(15);
  const response = await fetch(`/sessions/${id}/turns/stream`, {method: 'POST', body: JSON.stringify({request_id: crypto.randomUUID(), content: 'stale write'}), headers: {
    'content-type': 'application/json', 'x-csrf-token': token, 'X-Demo-Scope': ns.browser_scope_id,
    'X-Demo-Generation': String(ns.generation), 'X-Demo-Auth-Epoch': String(ns.auth_epoch)
  }});
  return {status: response.status, body: await response.json().catch(() => null)};
}, {id, ns});
const chatReady = page => page.waitForFunction(() => document.querySelector('#teacher-form')?.dataset.studentStream);
const shot = (page, name) => page.screenshot({path: fileURLToPath(new URL(name, SHOTS))});

// Teacher (or admin) start through the UI; observed POST paths are kept for the zero-POST checks.
async function visit(ctx, {role = 'teacher', size, init, chat = true, click = locator => locator.click()} = {}) {
  const context = await open(ctx.browser);
  ctx.contexts.push(context);
  if (init) await context.addInitScript(init);
  const page = await context.newPage();
  if (size) await page.setViewportSize(size);
  page.on('pageerror', error => ctx.errors.push(error.message));
  page.on('dialog', dialog => dialog.accept());
  const posts = [];
  page.on('request', request => { if (request.method() === 'POST') posts.push(new URL(request.url()).pathname); });
  await page.goto(`${ctx.demo.base}/demo`);
  await click(page.locator(`[data-demo-action="start"][data-demo-role="${role}"]`));
  await page.waitForURL(/\/scenarios\?/);
  if (chat) await enter(page, click);
  return {context, page, posts, base: ctx.demo.base};
}

async function enter(page, click = locator => locator.click()) {
  await click(page.locator('.scenario-card a.btn-primary').first());
  await page.waitForURL(/\/scenarios\/\d+\?/);
  await chatReady(page);
}

async function done(page, turns) {
  await until(async () => page.evaluate(turns => document.querySelectorAll('.message-student[data-message-id]').length === turns &&
    !document.getElementById('teacher-input').disabled, turns), `${turns} saved student replies`);
}

async function database(context, base, current) {
  const response = await context.request.get(`${base}/metrics?${namespace(current)}`);
  assert.equal(response.status(), 200, `GET /metrics: ${await response.text()}`);
  return (await response.json()).database;
}

// Teacher cannot read /metrics: the same visitor reads the totals as admin, then returns to teacher.
async function totals({context, page, base}) {
  const switchTo = async role => {
    const current = await state(page);
    const result = await control(page, '/demo/role', {request_id: randomUUID(), role, expected_generation: current.generation, expected_auth_epoch: current.auth_epoch});
    assert.equal(result.status, 200, `POST /demo/role ${role}: ${JSON.stringify(result.body)}`);
  };
  await switchTo('admin');
  const result = await database(context, base, await state(page));
  await switchTo('teacher');
  await page.goto(`${base}${(await state(page)).next_url}`);
  return result;
}

const domNamespace = page => page.evaluate(() => ({mode: document.documentElement.dataset.demoMode, ...document.documentElement.dataset}));
async function synced(page, label, first) {
  const dom = await domNamespace(page);
  const now = await state(page);
  assert.equal(dom.mode, 'demo', label);
  assert.deepEqual([dom.demoScope, dom.demoGeneration, dom.demoAuthEpoch], [now.browser_scope_id, String(now.generation), String(now.auth_epoch)], `${label}: DOM namespace vs GET /demo/state`);
  assert.equal(now.browser_scope_id, first.browser_scope_id, `${label}: no new scope`);
}

// §25: refresh, back/forward and BFCache re-sync from the state GET without any POST.
async function refresh(ctx) {
  const init = () => addEventListener('pageshow', event => {
    try { sessionStorage.setItem('t:pageshow', JSON.stringify([...JSON.parse(sessionStorage.getItem('t:pageshow') ?? '[]'), event.persisted])); } catch {}
  });
  const {page, posts} = await visit(ctx, {init});
  const first = await state(page);
  const draft = '아직 보내지 않은 초안';
  await page.fill('#teacher-input', draft);
  const before = posts.length;
  const check = async label => {
    await synced(page, label, first);
    assert.equal(posts.length, before, `${label}: navigation issued ${posts.slice(before)}`);
  };
  await page.reload();
  await chatReady(page);
  await check('reload');
  assert.equal(await page.inputValue('#teacher-input'), draft, 'reload keeps the unsent draft');
  await page.goBack();
  await page.waitForSelector('#demo-tour', {state: 'attached'});
  await check('back');
  await page.goForward();
  await chatReady(page);
  await check('forward');
  assert.equal(await page.inputValue('#teacher-input'), draft, 'forward keeps the unsent draft');
  // Recorded only: Playwright's Chromium may not restore from BFCache.
  console.log(`BFCACHE pageshow persisted=${await page.evaluate(() => sessionStorage.getItem('t:pageshow'))}`);
}

// §25: one start, one session, one turn per action; the second click never reaches the server.
async function doubleClicks(ctx) {
  const dbl = locator => locator.dblclick();
  const visitor = await visit(ctx, {chat: false, click: dbl});
  const {page, posts, base} = visitor;
  assert.equal(posts.filter(path => path === '/demo/start').length, 1, 'double click on start: POST /demo/start count');
  const baseline = await totals(visitor);
  await enter(page, dbl);
  const id = await page.evaluate(() => window.currentSessionId);
  await page.fill('#teacher-input', INPUT.A);
  await page.locator(SUBMIT).dblclick();
  await done(page, 1);
  await page.fill('#teacher-input', INPUT.common);
  const submit = page.locator(SUBMIT);
  await Promise.all([submit.click({force: true}), submit.click({force: true})]);
  await done(page, 2);
  assert.equal(posts.filter(path => path === `/sessions/${id}/turns/stream`).length, 2, 'two sends: POST turns/stream count');
  assert.equal(await page.locator('.message-teacher').count(), 2, 'two teacher turns in the DOM');
  await page.locator('#end-session-btn').dblclick();
  await until(() => page.evaluate(() => ['done', 'ready-to-analyze'].includes(document.getElementById('end-session-btn').dataset.state)), 'end settled');
  assert.equal(posts.filter(path => path === `/sessions/${id}/end`).length, 1, 'double click on end: POST /sessions/{id}/end count');
  const after = await totals(visitor);
  assert.equal(after.sessions.total, baseline.sessions.total + 1, '/metrics sessions.total: one session');
  assert(after.messages >= baseline.messages + 4, '/metrics messages: at least the two turns');
  // The ended session's history page: exactly the two turns that were sent.
  const ns = namespace(await state(page));
  const history = await page.evaluate(async url => {
    const response = await fetch(url);
    const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
    return {status: response.status, counts: ['teacher', 'student'].map(role => doc.querySelectorAll(`.message-${role}[data-message-id]`).length)};
  }, `/sessions/${id}?${ns}`);
  assert.equal(history.status, 200, `GET /sessions/${id} (ended) status`);
  assert.deepEqual(history.counts, [2, 2], `GET /sessions/${id}: teacher/student messages`);
}

// §24: close/skip/resume change guidance only.
async function tour(ctx) {
  const {page, posts} = await visit(ctx, {chat: false});
  const current = await state(page);
  const key = `demo:${current.browser_scope_id}:${current.generation}:tour-v1`;
  const flags = () => page.evaluate(key => { const record = JSON.parse(localStorage.getItem(key) ?? '{}'); return [record.closed === true, record.skipped === true]; }, key);
  const facts = () => page.evaluate(async () => (await (await fetch('/demo/state?include_tour=true')).json()).tour_facts);
  const map = () => page.evaluate(() => window.demoTour.snapshot().map);
  const panel = page.locator('#demo-tour');
  const factsLoaded = () => page.waitForFunction(() => window.demoTour?.snapshot().facts != null);
  await panel.waitFor({state: 'visible'});
  await factsLoaded();
  const baseline = {facts: await facts(), map: await map()};
  const before = posts.length;
  const expectGuidance = async (label, hidden, expected) => {
    await (hidden ? panel.waitFor({state: 'hidden'}) : panel.waitFor({state: 'visible'}));
    await factsLoaded();
    assert.deepEqual(await flags(), expected, `${label}: localStorage ${key}`);
    assert.deepEqual({facts: await facts(), map: await map()}, baseline, `${label}: TourFacts and map unchanged`);
    assert.equal(posts.length, before, `${label}: POST ${posts.slice(before)}`);
  };
  for (const [action, expected] of [['close', [true, false]], ['skip', [false, true]]]) {
    await page.locator(`#demo-tour [data-demo-action="${action}"]`).click();
    await expectGuidance(action, true, expected);
    assert(await page.locator('.demo-tour-resume').isVisible(), `${action}: resume offered`);
    if (action === 'close') {
      await page.reload();
      await expectGuidance('close after reload', true, expected);
    }
    await page.locator('.demo-tour-resume').click();
    await expectGuidance(`resume after ${action}`, false, [false, false]);
  }
}

// §25: a role switch or reset in another tab marks this tab stale; its writes with the old namespace are refused.
async function twoTabs(ctx) {
  const {context, page: tab1, base} = await visit(ctx);
  const old = await state(tab1);
  const id = await tab1.evaluate(() => window.currentSessionId);
  const tab2 = await context.newPage();
  tab2.on('pageerror', error => ctx.errors.push(error.message));
  tab2.on('dialog', dialog => dialog.accept());
  await tab2.goto(`${base}${old.next_url}`);
  const text = key => tab1.evaluate(key => window.uiLanguage.t(key), key);
  const stale = async (key, label) => {
    const message = await text(key);
    await until(async () => (await tab1.locator('#language-notice').textContent()) === message, `${label}: tab 1 #language-notice ${key}`);
    assert.equal(await tab1.locator('#demo-tour-status').textContent(), message, `${label}: tour status`);
    assert.equal(await tab1.locator('#demo-tour').getAttribute('data-stale'), 'true', `${label}: panel data-stale`);
    assert(await tab1.locator('#teacher-input').isDisabled(), `${label}: chat input locked`);
  };
  const refused = async (code, label) => {
    const before = await database(context, base, await state(tab2));
    const result = await stream(tab1, id, old);
    assert.equal(result.status, 409, `${label}: POST /sessions/${id}/turns/stream with old namespace ${JSON.stringify(result.body)}`);
    assert.equal(result.body.detail.code, code, label);
    assert.deepEqual(await database(context, base, await state(tab2)), before, `${label}: old-namespace write changed /metrics totals`);
  };
  await tab2.locator('#demo-tour [data-demo-action="role"][data-demo-role="admin"]').click();
  await tab2.waitForURL(/\/admin\/scenarios\?/);
  await stale('demo.stale_role', 'role switch');
  await refused('DEMO_STALE_ROLE', 'role switch');
  await shot(tab1, 'recovery-stale-role.png');
  // ready() reads the state, so wait until the reset POST was answered.
  const [reset] = await Promise.all([tab2.waitForResponse(response => new URL(response.url()).pathname === '/demo/reset'),
    tab2.locator('#demo-tour [data-demo-action="reset"]').click()]);
  assert([200, 202].includes(reset.status()), `POST /demo/reset ${reset.status()}`);
  await ready(tab2, old.generation + 1);
  await stale('demo.stale_generation', 'reset');
  await refused('DEMO_STALE_GENERATION', 'reset');
  await shot(tab1, 'recovery-stale-generation.png');
}

// §25/§24: the draft survives the language toggle and the toggle never translates Korean content.
async function language(ctx) {
  const {page, base} = await visit(ctx);
  const content = () => page.evaluate(() => ['.scenario-header h1', '.scenario-prompt', '.message-student .message-bubble', '.message-teacher .message-bubble']
    .map(selector => document.querySelector(selector)?.textContent.replace(/\s+/g, ' ').trim()));
  const map = () => page.evaluate(() => [...document.querySelectorAll('#demo-tour [data-demo-step]')].map(li => `${li.dataset.demoStep}=${li.dataset.state}`).join(' '));
  await page.fill('#teacher-input', INPUT.A);
  await page.locator(SUBMIT).click();
  await done(page, 1);
  // The first turn's auto coaching keeps the language guard busy until it settles.
  await page.waitForFunction(() => {
    const slots = [...document.querySelectorAll('.mentor-slot')];
    return slots.length > 0 && slots.every(slot => !['running', 'unknown'].includes(slot.dataset.status));
  });
  await page.waitForFunction(() => window.demoTour.snapshot().facts !== null);
  const before = {content: await content(), map: await map(), ns: await domNamespace(page)};
  assert(before.content.every(item => /[가-힣]/.test(item)), `Korean content: ${before.content}`);
  const toggle = page.locator('[data-ui-language]').first();
  // With an unsent draft the guarded toggle refuses and keeps everything (display-language.js switchLanguage).
  const draft = '언어 전환 전 초안';
  await page.fill('#teacher-input', draft);
  await page.evaluate(() => { window.marker = 1; });
  await toggle.click();
  const guard = await page.evaluate(() => window.uiLanguage.t('common.guard_input'));
  await until(async () => (await page.locator('#language-notice').textContent()) === guard,
    async () => `guard_input notice, saw ${JSON.stringify(await page.locator('#language-notice').textContent())}`);
  assert.equal(await page.inputValue('#teacher-input'), draft, 'draft kept while the toggle is refused');
  assert.equal(await page.evaluate(() => window.marker), 1, 'no reload while the draft is unsent');
  // Without a draft the toggle reloads in the other display language.
  await page.fill('#teacher-input', '');
  await toggle.click();
  await page.waitForFunction(() => document.documentElement.lang === 'en');
  await chatReady(page);
  await page.waitForFunction(() => window.demoTour.snapshot().facts !== null);
  assert.deepEqual(await content(), before.content, 'Korean content is not translated');
  await until(async () => (await map()) === before.map, `tour step map kept (${before.map})`);
  assert.deepEqual(await domNamespace(page), before.ns, 'namespace attributes kept');
  assert.equal(await page.locator('[data-ui-language]').first().getAttribute('data-ui-language'), 'ko');
}

// Visible, inside the viewport after scrolling, and the element itself is hit at its centre.
async function reachable(page, selector, label) {
  const target = page.locator(selector);
  await target.scrollIntoViewIfNeeded();
  const seen = await target.evaluate(element => {
    const box = element.getBoundingClientRect();
    const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
    return {inside: box.width > 0 && box.height > 0 && box.x >= 0 && box.y >= 0 && box.right <= innerWidth && box.bottom <= innerHeight,
      uncovered: Boolean(hit && element.contains(hit)), box: [box.x, box.y, box.right, box.bottom], view: [innerWidth, innerHeight]};
  });
  assert(seen.inside && seen.uncovered, `${label} ${selector}: ${JSON.stringify(seen)}`);
}

async function controls(page, label, tab) {
  await reachable(page, '#teacher-input', label);
  await reachable(page, SUBMIT, label);
  // On a narrow screen the end button sits in the situation tab.
  if (!(await page.locator('#end-session-btn').isVisible())) await page.locator(tab).click();
  await reachable(page, '#end-session-btn', label);
}

async function tabTo(page, selector) {
  for (const key of ['Tab', 'Shift+Tab']) {
    for (let step = 0; step < 80; step += 1) {
      if (await page.evaluate(selector => document.activeElement?.matches(selector), selector)) return;
      await page.keyboard.press(key);
    }
  }
  assert.fail(`keyboard cannot reach ${selector}`);
}

// §24: 390x844; 200% zoom is emulated as a 640x422 viewport (1280x844 CSS px at 200%); keyboard-only completes start + send.
async function layout(ctx) {
  for (const [label, size] of [['390x844', {width: 390, height: 844}], ['200% zoom (640x422)', {width: 640, height: 422}]]) {
    const {page} = await visit(ctx, {size});
    await controls(page, label, '#mobile-tab-scenario');
    await shot(page, `recovery-layout-${size.width}.png`);
  }
  const context = await open(ctx.browser);
  ctx.contexts.push(context);
  const page = await context.newPage();
  page.on('pageerror', error => ctx.errors.push(error.message));
  await page.goto(`${ctx.demo.base}/demo`);
  await tabTo(page, '[data-demo-action="start"][data-demo-role="teacher"]');
  await page.keyboard.press('Enter');
  await page.waitForURL(/\/scenarios\?/);
  await tabTo(page, '.scenario-card a.btn-primary');
  await page.keyboard.press('Enter');
  await chatReady(page);
  await tabTo(page, '#teacher-input');
  await reachable(page, ':focus', 'keyboard input');
  await page.keyboard.type(INPUT.A);
  await tabTo(page, SUBMIT);
  await reachable(page, ':focus', 'keyboard send');
  await page.keyboard.press('Space');
  await done(page, 1);
  await tabTo(page, '#end-session-btn');
  await reachable(page, ':focus', 'keyboard end');
}

// §25, §9: after the idle TTL only an explicit start re-enters. Uses the helper test clock (brief substitution 2).
async function expiry(ctx) {
  const {page, context, base} = await visit(ctx, {chat: false});
  const old = await state(page);
  const oldUrl = `${base}/scenarios?${namespace(old)}`;
  assert.equal((await context.request.get(oldUrl)).status(), 200, 'GET old namespace URL while live');
  await ctx.demo.advance(old.idle_ttl_seconds + 1);
  const expired = await until(async () => { const result = await get(page, '/demo/state'); return result.status === 200 ? null : result; }, 'GET /demo/state after the idle TTL', 90000);
  assert.deepEqual([expired.status, expired.body?.detail?.code], [410, 'DEMO_EXPIRED'], 'GET /demo/state after expiry');
  const refused = await context.request.get(oldUrl);
  assert([401, 410].includes(refused.status()), `GET ${oldUrl} after expiry: ${refused.status()}`);
  const entry = await page.goto(`${base}/demo`);
  assert.equal(entry.status(), 200, 'GET /demo after expiry still renders the entry page');
  const again = await get(page, '/demo/state');
  assert([401, 410].includes(again.status) && !again.body?.browser_scope_id, `GET navigation created a scope: ${JSON.stringify(again)}`);
  await page.waitForFunction(() => document.getElementById('demo-tour-status').textContent.trim() !== '');
  await page.locator('[data-demo-action="start"][data-demo-role="teacher"]').click();
  await page.waitForURL(/\/scenarios\?/);
  const fresh = await state(page);
  assert.equal(fresh.state, 'ready');
  assert.notEqual(fresh.browser_scope_id, old.browser_scope_id, 'explicit start gives a new browser_scope_id');
  assert.notEqual((await context.request.get(oldUrl)).status(), 200, 'old namespace URL after the new start');
}

const CASES = [['I1 refresh/back/forward/BFCache', refresh], ['I2 double clicks', doubleClicks], ['I3 tour close/skip/resume', tour],
  ['I4 two tabs', twoTabs], ['I5 ko/en toggle', language], ['I6 390/zoom/keyboard layout', layout], ['I7 expiry re-entry', expiry]];

export default async function check() {
  await mkdir(SHOTS, {recursive: true});
  const errors = [];
  const defects = [];
  const demo = await server();
  let browser;
  try {
    console.log(`RECEIPT ${JSON.stringify(demo.receipt)}`);
    browser = await chromium.launch({headless: true});
    for (const [name, run] of CASES) {
      const ctx = {browser, demo, errors, contexts: []};
      try {
        await run(ctx);
        console.log(`PASS ${name}`);
      } catch (error) {
        defects.push(`${name}: ${error.message}`);
      } finally {
        await Promise.allSettled(ctx.contexts.map(context => context.close()));
      }
    }
  } finally {
    await browser?.close();
    await demo.stop();
  }
  assert.deepEqual(defects, [], 'recovery defects');
  return {pageErrors: errors};
}
