/* The document language stays fixed until an explicit, guarded reload. */
window.uiLanguage = (() => {
  const strings = JSON.parse(document.getElementById('ui-strings').textContent);
  const guards = new Set();
  // §24: an absent #demo-config or mode "normal" keeps every legacy name and header.
  let demo = null;
  try { demo = JSON.parse(document.getElementById('demo-config')?.textContent ?? 'null'); } catch {}
  if (demo?.mode !== 'demo') demo = null;
  const csrfCookie = demo?.csrf_cookie_name || 'csrftoken';
  const csrfHeader = demo?.csrf_header_name || 'x-csrf-token';
  const namespace = demo && {scope:demo.browser_scope_id, generation:demo.generation, epoch:demo.auth_epoch};
  // §10 order: CSRF is read per request and never stored.
  function csrfToken() {
    const prefix = `${csrfCookie}=`;
    const item = document.cookie.split(/;\s*/).find(part => part.startsWith(prefix));
    return item === undefined ? null : item.slice(prefix.length);
  }
  const DEMO_HEADERS = ['X-Demo-Scope', 'X-Demo-Generation', 'X-Demo-Auth-Epoch'];
  function demoHeaders() {
    if (namespace?.scope == null) return {};
    const values = [namespace.scope, namespace.generation, namespace.epoch];
    return Object.fromEntries(DEMO_HEADERS.map((name, index) => [name, String(values[index])]));
  }
  // §25: run/draft/mentor keys are bound to the visit namespace; old keys are never migrated.
  function storageKey(key) {
    return namespace?.scope == null ? key :
      `demo:${namespace.scope}:${namespace.generation}:${namespace.epoch}:${key}`;
  }
  // §25: pending work registers here; any later namespace, expiry or reset stops it with a hint.
  const staleHandlers = new Set();
  function registerStale(handler) {
    staleHandlers.add(handler);
    return () => staleHandlers.delete(handler);
  }
  // A capture token pins the namespace and an abort signal; any invalidation voids both.
  // ponytail: controllers are only released by invalidation; add a release call if a page captures per poll.
  let serial = 0;
  const controllers = new Set();
  function capture() {
    if (!demo) return {prefix:'', serial:0, signal:undefined};
    const controller = new AbortController();
    controllers.add(controller);
    return {prefix:storageKey(''), serial, signal:controller.signal};
  }
  function invalidate(key) {
    serial++;
    for (const controller of controllers) controller.abort();
    controllers.clear();
    notify(t(key));
    for (const handler of [...staleHandlers]) handler(t(key));
  }
  // Change 3: one state-check per authoritative answer; #199 alone emits the five state events.
  const STATE_CHECK = ['DEMO_SESSION_REQUIRED', 'DEMO_EXPIRED', 'DEMO_STALE_GENERATION', 'DEMO_STALE_ROLE'];
  let checking = false;
  // Authoritative #199 events only move the namespace forward within the same scope.
  for (const type of ['demo:state', 'demo:role-changed', 'demo:reset-completed']) {
    document.addEventListener(type, ({detail}) => {
      if (!namespace || typeof detail?.browser_scope_id !== 'string' ||
        !Number.isInteger(detail.generation) || !Number.isInteger(detail.auth_epoch)) return;
      if (namespace.scope != null && (detail.browser_scope_id !== namespace.scope ||
        detail.generation < namespace.generation ||
        detail.generation === namespace.generation && detail.auth_epoch < namespace.epoch)) return;
      const before = storageKey('');
      Object.assign(namespace, {scope:detail.browser_scope_id, generation:detail.generation, epoch:detail.auth_epoch});
      if (before && storageKey('') !== before) invalidate(type === 'demo:role-changed' ? 'demo.stale_role' : 'demo.stale_generation');
    });
  }
  for (const [type, key] of [['demo:expired', 'demo.expired'], ['demo:reset-started', 'demo.resetting']]) {
    document.addEventListener(type, ({detail}) => {
      if (namespace?.scope != null && detail?.browser_scope_id === namespace.scope) invalidate(key);
    });
  }
  for (const type of ['demo:state', 'demo:role-changed', 'demo:reset-started', 'demo:reset-completed', 'demo:expired']) {
    document.addEventListener(type, () => { checking = false; });
  }
  // §11: a demo envelope (API detail or SSE terminal frame) yields its hint; null keeps normal handling.
  function recovery(detail) {
    if (!demo || typeof detail?.recovery !== 'string') return null;
    const key = Object.hasOwn(strings, detail.recovery) ? detail.recovery : 'demo.refresh_state';
    if (!STATE_CHECK.includes(detail.code)) {
      notify(t(key));
      return t(key);
    }
    invalidate(key);
    if (!checking) {
      checking = true;
      document.dispatchEvent(new CustomEvent('demo:state-check', {detail:{reason:detail.code}}));
    }
    return t(key);
  }
  async function responseRecovery(response) {
    if (!demo || response.ok) return null;
    try { return recovery((await response.clone().json()).detail); } catch { return null; }
  }
  // §25: work keeps its capture token; any invalidation since, or a frame of another namespace, voids it.
  function current(token, frame) {
    if (!demo) return true;
    if (token.serial !== serial || storageKey('') !== token.prefix) return false;
    if (typeof frame?.browser_scope_id !== 'string' ||
      `demo:${frame.browser_scope_id}:${frame.generation}:${frame.auth_epoch}:` === token.prefix) return true;
    recovery({code:'DEMO_STALE_GENERATION', recovery:'demo.stale_generation'});
    return false;
  }
  // §25: a JSON run receipt must be current, not historical, and answer the request that was sent.
  function receipt(token, data, requestId) {
    if (!demo) return true;
    if (data?.historical === true) {
      recovery({code:'DEMO_STALE_GENERATION', recovery:'demo.stale_generation'});
      return false;
    }
    if (!current(token, data)) return false;
    if (data?.request_id === requestId) return true;
    notify(t('demo.refresh_state'));
    return false;
  }
  // §24.1: only the journey target, still in the namespace the caller captured.
  function observe(kind, sessionId, token, requestId = crypto.randomUUID()) {
    const journey = demo?.tour_facts?.journey_session_id;
    if (!Number.isInteger(journey) || String(sessionId) !== String(journey) || namespace.scope == null ||
      !current(token)) return;
    document.dispatchEvent(new CustomEvent('demo:tour-observed', {detail:{schema_version:1,
      browser_scope_id:namespace.scope, generation:namespace.generation, auth_epoch:namespace.epoch,
      journey_session_id:journey, kind, request_id:requestId}}));
  }
  // §25: after BFCache, saved work is only looked up once #199 has confirmed the state.
  function afterState(callback) {
    if (!demo) callback();
    else document.addEventListener('demo:state', () => callback(), {once:true});
  }
  function t(key, values = {}) {
    const text = Object.hasOwn(strings, key) ? strings[key] : '표시할 수 없는 안내입니다.';
    return text.replace(/\{([^{}]+)\}/g, (match, name) =>
      Object.hasOwn(values, name) ? String(values[name]) : match);
  }
  function headers(url, initial) {
    const result = new Headers(initial);
    if (new URL(url, location.href).origin === location.origin) {
      result.set('X-UI-Language', document.documentElement.lang);
      for (const [name, value] of Object.entries(demoHeaders())) result.set(name, value);
    } else {
      result.delete('X-UI-Language');
      if (demo) DEMO_HEADERS.forEach(name => result.delete(name));
    }
    return result;
  }
  function notify(text) {
    const notice = document.getElementById('language-notice');
    if (!notice) return;
    notice.hidden = false;
    notice.textContent = text;
  }
  function registerGuard(readState) {
    guards.add(readState);
    return () => guards.delete(readState);
  }
  function switchLanguage(language) {
    if (!['ko', 'en'].includes(language) || language === document.documentElement.lang) return;
    const states = [...guards].map(readState => readState());
    const reason = states.some(state => state.busy) ? 'guard_busy' :
      states.some(state => state.dirty) ? 'guard_input' : null;
    const announce = key => notify(t(`common.${key}`));
    if (reason) { announce(reason); return; }
    try {
      document.cookie = `ui_lang=${language}; Max-Age=31536000; Path=/; SameSite=Lax${location.protocol === 'https:' ? '; Secure' : ''}`;
      if (!document.cookie.split(';').some(value => value.trim() === `ui_lang=${language}`)) {
        announce('cookie_failed');
        return;
      }
    } catch {
      announce('cookie_failed');
      return;
    }
    location.reload();
  }
  // Capture runs before page handlers, which skip their own alert when this hint is set.
  document.addEventListener('htmx:responseError', event => {
    try { event.detail.demoRecovery = recovery(JSON.parse(event.detail.xhr.responseText).detail); } catch {}
  }, true);
  document.addEventListener('htmx:configRequest', event => {
    const url = new URL(event.detail.path, location.href);
    for (const name of Object.keys(event.detail.headers)) {
      const lower = name.toLowerCase();
      if (lower === 'x-ui-language' || demo && DEMO_HEADERS.some(header => header.toLowerCase() === lower)) {
        delete event.detail.headers[name];
      }
    }
    if (url.origin === location.origin) {
      event.detail.headers['X-UI-Language'] = document.documentElement.lang;
      Object.assign(event.detail.headers, demoHeaders());
    }
  });
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-ui-language]');
    if (button) switchLanguage(button.dataset.uiLanguage);
  });
  document.addEventListener('DOMContentLoaded', () => {
    const notices = document.querySelector('.nav-notices');
    new ResizeObserver(() => {
      document.body.style.setProperty('--nav-notice-height', `${notices.getBoundingClientRect().height}px`);
    }).observe(notices);
  });
  return {t, headers, registerGuard, csrfHeader, csrfToken, storageKey, registerStale, recovery, responseRecovery,
    capture, current, receipt, observe, afterState, notify,
    versions:demo && {content:demo.content_version, fixture:demo.fixture_version}};
})();
