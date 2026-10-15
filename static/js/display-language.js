/* The document language stays fixed until an explicit, guarded reload. */
window.uiLanguage = (() => {
  const strings = JSON.parse(document.getElementById('ui-strings').textContent);
  const guards = new Set();
  function t(key, values = {}) {
    const text = Object.hasOwn(strings, key) ? strings[key] : '표시할 수 없는 안내입니다.';
    return text.replace(/\{([^{}]+)\}/g, (match, name) =>
      Object.hasOwn(values, name) ? String(values[name]) : match);
  }
  function headers(url, initial) {
    const result = new Headers(initial);
    if (new URL(url, location.href).origin === location.origin) {
      result.set('X-UI-Language', document.documentElement.lang);
    } else {
      result.delete('X-UI-Language');
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
      if (name.toLowerCase() === 'x-ui-language') delete event.detail.headers[name];
    }
    if (url.origin === location.origin) {
      event.detail.headers['X-UI-Language'] = document.documentElement.lang;
    }
  });
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-ui-language]');
    if (button) switchLanguage(button.dataset.uiLanguage);
  });
  return {t, headers, registerGuard};
})();
