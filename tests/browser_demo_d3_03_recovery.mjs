import assert from 'node:assert/strict';

// §10/§24/§25 plumbing against an injected #demo-config; no real demo backend runs here.
const DEMO = {
  mode:'demo', browser_scope_id:'scope-a', generation:3, auth_epoch:2, state:'ready', role:'teacher',
  csrf_cookie_name:'demo_csrftoken', csrf_header_name:'x-csrf-token',
};

async function open(page, base, config, oldDraft) {
  await page.unrouteAll();
  if (config) {
    await page.route(url => url.pathname === '/chat', async route => {
      const response = await route.fetch();
      const body = (await response.text()).replace('<script src="/static/js/display-language.js">',
        `<script type="application/json" id="demo-config">${JSON.stringify(config)}</script>` +
        '<script src="/static/js/display-language.js">');
      await route.fulfill({response, body});
    });
  }
  await page.goto(`${base}/chat?stream`);
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  const id = await page.evaluate(() => window.currentSessionId);
  if (oldDraft) {
    await page.evaluate(([key, value]) => sessionStorage.setItem(key, value), [`chat-draft-${id}`, oldDraft]);
    await page.reload();
    await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  }
  return id;
}

function snapshot(page) {
  return page.evaluate(() => ({
    token:uiLanguage.csrfToken(),
    analysis:getAnalysisCsrfHeaders(),
    same:Object.fromEntries(uiLanguage.headers('/sessions/1', {'Content-Type':'application/json'})),
    cross:Object.fromEntries(uiLanguage.headers('https://example.com/x', {'X-Demo-Scope':'leak', 'X-UI-Language':'ko'})),
    key:uiLanguage.storageKey('chat-run-7'),
  }));
}

export default async function check(page) {
  const base = new URL(page.url()).origin;
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
  return {pageErrors:[]};
}
