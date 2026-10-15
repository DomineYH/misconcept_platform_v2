/* Shared administrator/About dialogs; callers keep their own load and close paths. */
window.modalLifecycle = (() => {
  let active = null, returnFocus = null, closeDialog = null, previousOverflow = '';
  const controls = root => Array.from(root.querySelectorAll(
    'button:not(:disabled), a[href], input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), summary, [tabindex="0"]'
  )).filter(el => el.getClientRects().length && !el.closest('[hidden]'));
  function focus(root) {
    const required = root.querySelector('input[required]:not([type="hidden"]), textarea[required], select[required]');
    (required || controls(root)[0] || root).focus();
  }
  function open(root, close, caller = document.activeElement) {
    if (active === root) return;
    active = root;
    returnFocus = caller;
    closeDialog = close;
    previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    root.setAttribute('role', 'dialog');
    root.setAttribute('aria-modal', 'true');
    root.tabIndex = -1;
    if (!root.hasAttribute('aria-label') && !root.hasAttribute('aria-labelledby')) {
      const heading = root.querySelector('h2, h3');
      if (heading) root.setAttribute('aria-label', heading.textContent.trim());
    }
    root.querySelectorAll('.modal-error').forEach(el => el.remove());
    focus(root);
  }
  function close(root) {
    if (active !== root) return;
    active = null;
    closeDialog = null;
    document.body.style.overflow = previousOverflow;
    const target = returnFocus?.isConnected ? returnFocus : (document.querySelector('main h1') || document.querySelector('main h2') || document.querySelector('main'));
    if (target) {
      if (!target.matches('button, a, input, select, textarea, [tabindex]')) target.tabIndex = -1;
      target.focus();
    }
    returnFocus = null;
  }
  function error(root, message) {
    let notice = root.querySelector('.modal-error');
    if (!notice) {
      notice = document.createElement('p');
      notice.className = 'modal-error';
      notice.setAttribute('role', 'alert');
      notice.tabIndex = -1;
      (root.querySelector('.modal-body, .panel-body, form') || root).prepend(notice);
    }
    notice.textContent = Array.isArray(message)
      ? message.map(item => item.msg || uiLanguage.t('common.check_input')).join(' ')
      : String(message);
    notice.focus();
  }
  document.addEventListener('keydown', event => {
    if (!active) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopImmediatePropagation();
      closeDialog();
    } else if (event.key === 'Tab') {
      const items = controls(active), first = items[0], last = items[items.length - 1];
      if (!first) { event.preventDefault(); active.focus(); }
      else if (!active.contains(document.activeElement) ||
        (event.shiftKey && (document.activeElement === first || document.activeElement === active)) ||
        (!event.shiftKey && document.activeElement === last)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      }
    }
  }, true);
  document.addEventListener('focusin', event => {
    if (active && !active.contains(event.target)) focus(active);
  });
  return {open, close, error};
})();
