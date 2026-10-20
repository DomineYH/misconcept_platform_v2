import {readSSE} from './student-sse.js';

// Fetch owns the composer transport; initialization is safe to repeat.
export function mountStudentStream(ui) {
  const form = document.getElementById('teacher-form');
  if (form.dataset.studentStream) return;
  form.dataset.studentStream = 'true';
  const input = document.getElementById('teacher-input');
  const submit = form.querySelector('[type="submit"]');
  const container = document.getElementById('messages-container');
  const storageKey = uiLanguage.storageKey(`chat-run-${ui.sessionId}`);
  const draftKey = uiLanguage.storageKey(`chat-draft-${ui.sessionId}`);
  const token = uiLanguage.capture();
  let pending, teacher, student, status, actions, retry;
  let lookupBusy = false;
  let sessionEnded = false;
  document.addEventListener('chat:ended', () => { sessionEnded = true; });
  uiLanguage.registerGuard(() => ({
    dirty: Boolean(input.value),
    busy: lookupBusy || (!sessionEnded && Boolean(pending) && ['sending', 'running', 'unknown'].includes(pending.status))
  }));
  let forbidden = false;
  let recoveryTimer, recoveryStartedAt, recoveryStep;
  let leftPage = false, lifetime = new AbortController();
  ui.stopPolling();
  uiLanguage.registerStale(message => {
    lifetime.abort();
    clearTimeout(recoveryTimer);
    if (!pending || !status) return;
    status.textContent = message;
    actions.hidden = true;
  });
  // Remove the legacy transport on this mounted form, including future HTMX processing.
  form.removeAttribute('hx-post');
  container.removeAttribute('hx-get');

  function persist() {
    pending.draft = input.value;
    sessionStorage.setItem(draftKey, input.value);
    sessionStorage.setItem(storageKey, JSON.stringify(pending));
  }

  function sync() {
    input.disabled = submit.disabled = ui.isLocked() || Boolean(pending);
  }

  function message(role, content) {
    const row = document.createElement('div');
    row.className = `message message-${role}`;
    const avatar = document.createElement('div');
    avatar.className = `avatar ${role}`;
    avatar.textContent = role === 'teacher' ? 'T' : ui.studentName;
    const wrapper = document.createElement('div');
    wrapper.className = 'message-content-wrapper';
    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';
    bubble.style.whiteSpace = 'pre-wrap';
    bubble.textContent = content;
    wrapper.append(bubble);
    row.append(avatar, wrapper);
    container.append(row);
    return row;
  }

  function reconcile(row, id) {
    container.querySelectorAll('[data-message-id]').forEach(other => {
      if (other !== row && other.dataset.messageId === String(id)) other.remove();
    });
    row.dataset.messageId = id;
  }

  function renderTurn() {
    teacher = message('teacher', pending.content);
    teacher.dataset.requestId = pending.request_id;
    student = message('student', '');
    status = document.createElement('div');
    status.className = 'message-meta student-run-status';
    status.setAttribute('role', 'status');
    student.querySelector('.message-content-wrapper').append(status);
    actions = document.createElement('div');
    actions.hidden = true;
    retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'btn-secondary';
    retry.textContent = uiLanguage.t('teacher.student_retry');
    retry.addEventListener('click', () => void send(true));
    const end = document.createElement('button');
    end.type = 'button';
    end.className = 'btn-secondary';
    end.textContent = uiLanguage.t('teacher.end_chat');
    end.addEventListener('click', () => document.getElementById('end-session-btn').click());
    const check = document.createElement('button');
    check.type = 'button';
    check.className = 'btn-secondary';
    check.textContent = uiLanguage.t('teacher.check_status');
    check.addEventListener('click', () => void recover());
    actions.append(retry, end, check);
    student.querySelector('.message-content-wrapper').append(actions);
    status.textContent = uiLanguage.t('teacher.sending');
    container.scrollTop = container.scrollHeight;
  }

  function event(type, data) {
    if (!pending || !['sending', 'running'].includes(pending.status)) return;
    if (type === 'run.accepted') {
      if (data.request_id !== pending.request_id) return;
      Object.assign(pending, data);
      reconcile(teacher, data.teacher_message_id);
      teacher.dataset.turnId = student.dataset.turnId = data.turn_id;
      teacher.dataset.turnIndex = student.dataset.turnIndex = data.turn_index;
      status.textContent = uiLanguage.t('teacher.student_provisional');
      persist();
    } else if (type === 'output.delta') {
      if (data.run_id !== pending.run_id) return;
      student.querySelector('.message-bubble').textContent += data.text;
    } else if (type === 'output.completed') {
      if (data.run_id !== pending.run_id) return;
      reconcile(student, data.message.id);
      student.querySelector('.message-bubble').textContent = data.message.content;
      status.textContent = uiLanguage.t('teacher.saved');
      clearTimeout(recoveryTimer);
      pending = null;
      sessionStorage.setItem(draftKey, input.value);
      sessionStorage.removeItem(storageKey);
      sync();
      if (!ui.isLocked()) input.focus();
      container.dispatchEvent(new CustomEvent('student:completed', {detail:{row:student, ...data}}));
    } else if (['run.failed', 'run.interrupted', 'run.cancelled'].includes(type)) {
      if (data.run_id !== pending.run_id) return;
      const hint = uiLanguage.recovery(data);
      if (hint && ui.isLocked()) return;
      Object.assign(pending, data);
      clearTimeout(recoveryTimer);
      pending.partial_text = student.querySelector('.message-bubble').textContent;
      status.textContent = uiLanguage.t('teacher.student_incomplete', {reason:hint ?? (
        data.status === 'cancelled' ? uiLanguage.t('teacher.conversation_ended') :
        (data.code || data.error_code) === 'configuration_unavailable' ? uiLanguage.t('teacher.student_configuration_error') :
        (data.code || data.error_code) === 'storage_error' ? uiLanguage.t('teacher.student_storage_failed') :
        data.status === 'interrupted' ? uiLanguage.t('teacher.student_interrupted') :
        uiLanguage.t('teacher.student_failed'))});
      actions.hidden = false;
      retry.textContent = uiLanguage.t('teacher.student_retry');
      retry.disabled = !data.retryable;
      if (!input.value) input.value = pending.content;
      persist();
      if (type === 'run.cancelled') ui.end();
      sync();
    }
    container.scrollTop = container.scrollHeight;
  }

  function applySnapshot(data) {
    pending.status = 'sending';
    pending.request_id = data.request_id;
    event('run.accepted', {...data, status:'running'});
    if (data.status === 'completed') {
      event('output.completed', data);
    } else if (['failed', 'interrupted', 'cancelled'].includes(data.status)) {
      student.querySelector('.message-bubble').textContent = data.partial_text || '';
      event(`run.${data.status}`, {...data, message:{
        failed:uiLanguage.t('teacher.student_failed'), interrupted:uiLanguage.t('teacher.student_interrupted'),
        cancelled:uiLanguage.t('teacher.conversation_ended')
      }[data.status]});
    }
  }

  function expire() {
    clearTimeout(recoveryTimer);
    if (pending) {
      if (!input.value) input.value = pending.content;
      persist();
    }
    ui.expire();
    sync();
  }

  function deny() {
    forbidden = true;
    clearTimeout(recoveryTimer);
    pending.status = 'forbidden';
    pending.retryable = false;
    status.textContent = uiLanguage.t('teacher.student_forbidden');
    actions.hidden = true;
    if (!input.value) input.value = pending.content;
    persist();
    sync();
  }

  // A JSON receipt that fails the demo check must not be applied; stop the work and tell the user.
  function rejectReceipt() {
    clearTimeout(recoveryTimer);
    status.textContent = uiLanguage.t('demo.refresh_state');
    retry.disabled = true;
  }

  async function recover(reset = true) {
    if (!pending || ui.isLocked() || lookupBusy || leftPage || forbidden) return;
    if (reset) {
      clearTimeout(recoveryTimer);
      recoveryStartedAt = Date.now();
      recoveryStep = 0;
    }
    lookupBusy = true;
    const attempt = pending;
    status.textContent = uiLanguage.t('teacher.student_checking');
    actions.hidden = false;
    retry.disabled = true;
    try {
      const url = pending.run_id ? `/runs/${pending.run_id}` :
        `/sessions/${ui.sessionId}/runs?request_id=${encodeURIComponent(pending.request_id)}`;
      const response = await ui.fetch(url, {signal:lifetime.signal});
      if (pending !== attempt || leftPage) return;
      if (response.status === 401) expire();
      else if (response.status === 403) deny();
      else if (response.status === 404) {
        pending.status = 'unknown';
        pending.retryable = true;
        status.textContent = uiLanguage.t('teacher.student_unknown_request');
        retry.textContent = uiLanguage.t('teacher.resend_request');
        retry.disabled = false;
        if (!input.value) input.value = pending.content;
        persist();
      }
      else if (response.ok) {
        const data = await response.json();
        if (pending !== attempt || leftPage) return;
        if (!uiLanguage.receipt(token, data, attempt.request_id)) { rejectReceipt(); return; }
        applySnapshot(data);
        if (pending?.status === 'running') {
          status.textContent = uiLanguage.t('teacher.student_checking_saved');
          const elapsed = [1000, 3000, 7000, 15000, 30000][recoveryStep++];
          if (elapsed !== undefined && Date.now() < recoveryStartedAt + 30000) {
            recoveryTimer = setTimeout(() => void recover(false), Math.max(0, recoveryStartedAt + elapsed - Date.now()));
          } else {
            status.textContent = uiLanguage.t('teacher.student_unknown_saved');
          }
        }
      }
      else status.textContent = uiLanguage.t('teacher.student_lookup_failed');
    } catch (error) {
      if (leftPage) return;
      if (error.code === 'AUTH_EXPIRED') expire();
      else status.textContent = uiLanguage.t('teacher.student_lookup_failed');
    } finally {
      lookupBusy = false;
      sync();
    }
  }

  async function send(isRetry = false) {
    if (ui.isLocked() || leftPage || forbidden) return;
    const previous = pending;
    if (isRetry) {
      if (!pending?.retryable || !['failed', 'interrupted', 'unknown'].includes(pending.status)) return;
      const resend = pending.status === 'unknown';
      pending = {...pending, request_id:resend ? pending.request_id : crypto.randomUUID(),
        request_turn_id:resend ? pending.request_turn_id : pending.turn_id,
        status:'sending', run_id:null, partial_text:''};
      retry.textContent = uiLanguage.t('teacher.student_retry');
      teacher.dataset.requestId = pending.request_id;
      student.querySelector('.message-bubble').textContent = '';
      status.textContent = uiLanguage.t('teacher.sending');
      actions.hidden = true;
    } else {
      if (pending || !input.value.trim()) return;
      pending = {request_id:crypto.randomUUID(), content:input.value, status:'sending'};
      renderTurn();
      input.value = '';
      input.style.height = '';
    }
    persist();
    sync();
    const attempt = pending;
    const body = {request_id:pending.request_id, content:pending.content};
    if (pending.request_turn_id) body.turn_id = pending.request_turn_id;
    try {
      const response = await ui.fetch(`/sessions/${ui.sessionId}/turns/stream`, {
        method:'POST', headers:ui.headers(), body:JSON.stringify(body), signal:lifetime.signal
      });
      if (leftPage || pending !== attempt) {
        await response.body?.cancel();
        return;
      }
      if (response.status === 401) {
        expire();
        return;
      }
      const detail = response.ok ? null : await response.clone().json().then(body => body.detail, () => null);
      const hint = uiLanguage.recovery(detail);
      if (hint) {
        // §11: the request was refused; only an explicit resend reuses its request_id.
        pending.status = 'unknown';
        pending.retryable = detail.retryable === true;
        status.textContent = hint;
        retry.textContent = uiLanguage.t('teacher.resend_request');
        retry.disabled = !pending.retryable;
        actions.hidden = false;
        if (!input.value) input.value = pending.content;
        persist();
        return;
      }
      if (response.status === 403) {
        deny();
        return;
      }
      if (response.status === 422 && (await response.clone().json()).detail?.code === 'context_limit') {
        status.textContent = uiLanguage.t('teacher.student_context_limit');
        if (!input.value) input.value = pending.content;
        if (body.turn_id) {
          pending = previous;
          status.textContent += uiLanguage.t('teacher.student_previous_incomplete');
          teacher.dataset.requestId = pending.request_id;
          student.querySelector('.message-bubble').textContent = pending.partial_text || '';
          actions.hidden = false;
          retry.disabled = !pending.retryable;
          persist();
        } else {
          teacher.remove();
          container.append(status);
          student.remove();
          pending = null;
          sessionStorage.removeItem(storageKey);
          sessionStorage.setItem(draftKey, input.value);
        }
        sync();
        if (!input.disabled) input.focus();
        return;
      }
      if (response.status === 400 && (await response.clone().json()).detail?.code === 'session_ended') {
        event('run.cancelled', {run_id:pending.run_id, status:'cancelled', code:'session_ended',
          message:uiLanguage.t('teacher.conversation_ended'), retryable:false});
        return;
      }
      if (response.status === 503 || response.status === 429) {
        const detail = (await response.clone().json()).detail;
        if (['configuration_unavailable', 'call_limit_reached'].includes(detail?.code)) {
          pending.status = 'unknown';
          pending.retryable = true;
          status.textContent = detail.code === 'configuration_unavailable' ?
            uiLanguage.t('teacher.student_configuration') :
            uiLanguage.t('teacher.student_capacity');
          retry.textContent = uiLanguage.t('teacher.resend_request');
          retry.disabled = false;
          actions.hidden = false;
          if (!input.value) input.value = pending.content;
          persist();
          return;
        }
      }
      if (response.ok && response.headers.get('Content-Type')?.includes('application/json')) {
        const data = await response.json();
        if (pending !== attempt || leftPage) return;
        if (!uiLanguage.receipt(token, data, attempt.request_id)) { rejectReceipt(); return; }
        applySnapshot(data);
        if (pending?.status === 'running') await recover();
      } else {
        await readSSE(response.body, (type, data) => {
          if (pending !== attempt || leftPage || !uiLanguage.current(token, data)) return false;
          event(type, data);
          return !leftPage && pending === attempt && ['sending', 'running'].includes(pending.status);
        });
        if (pending === attempt && ['sending', 'running'].includes(pending.status)) await recover();
      }
    } catch (error) {
      if (leftPage) return;
      if (error.code === 'AUTH_EXPIRED') expire();
      else if (pending === attempt && ['sending', 'running'].includes(pending.status)) await recover();
    }
  }

  form.addEventListener('submit', e => {
    e.preventDefault();
    e.stopImmediatePropagation();
    void send();
  }, true);
  input.addEventListener('keydown', e => {
    if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    form.requestSubmit();
  }, true);
  input.addEventListener('input', () => sessionStorage.setItem(draftKey, input.value));
  window.addEventListener('pagehide', () => {
    leftPage = true;
    lifetime.abort();
    clearTimeout(recoveryTimer);
    sessionStorage.setItem(draftKey, input.value);
    if (pending) persist();
  });
  window.addEventListener('pageshow', e => {
    if (!e.persisted) return;
    leftPage = false;
    lifetime = new AbortController();
    if (pending) uiLanguage.afterState(() => void recover());
  });
  if (!ui.isLocked()) input.value = sessionStorage.getItem(draftKey) ?? input.value;
  const saved = sessionStorage.getItem(storageKey);
  if (saved && !ui.isLocked()) {
    pending = JSON.parse(saved);
    input.value = pending.draft ?? pending.content;
    renderTurn();
    student.querySelector('.message-bubble').textContent = pending.partial_text || '';
    if (pending.teacher_message_id) reconcile(teacher, pending.teacher_message_id);
    void recover();
  }
  sync();
}
