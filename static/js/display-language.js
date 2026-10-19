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
  // Authoritative #199 events only move the namespace forward within the same scope.
  for (const type of ['demo:state', 'demo:role-changed', 'demo:reset-completed']) {
    document.addEventListener(type, ({detail}) => {
      if (!namespace || typeof detail?.browser_scope_id !== 'string' ||
        !Number.isInteger(detail.generation) || !Number.isInteger(detail.auth_epoch)) return;
      if (namespace.scope != null && (detail.browser_scope_id !== namespace.scope ||
        detail.generation < namespace.generation ||
        detail.generation === namespace.generation && detail.auth_epoch < namespace.epoch)) return;
      Object.assign(namespace, {scope:detail.browser_scope_id, generation:detail.generation, epoch:detail.auth_epoch});
    });
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
  function registerGuard(readState) {
    guards.add(readState);
    return () => guards.delete(readState);
  }
  function switchLanguage(language) {
    if (!['ko', 'en'].includes(language) || language === document.documentElement.lang) return;
    const states = [...guards].map(readState => readState());
    const reason = states.some(state => state.busy) ? 'guard_busy' :
      states.some(state => state.dirty) ? 'guard_input' : null;
    const notice = document.getElementById('language-notice');
    function announce(key) {
      notice.hidden = false;
      notice.textContent = t(`common.${key}`);
    }
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
  return {t, headers, registerGuard, csrfHeader, csrfToken, storageKey};
})();
