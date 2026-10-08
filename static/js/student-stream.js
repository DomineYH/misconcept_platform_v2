import {readSSE} from './student-sse.js';

// Fetch owns the composer transport; initialization is safe to repeat.
export function mountStudentStream(ui) {
  const form = document.getElementById('teacher-form');
  if (form.dataset.studentStream) return;
  form.dataset.studentStream = 'true';
  const input = document.getElementById('teacher-input');
  const submit = form.querySelector('[type="submit"]');
  const container = document.getElementById('messages-container');
  const storageKey = `chat-run-${ui.sessionId}`;
  const draftKey = `chat-draft-${ui.sessionId}`;
  let pending, teacher, student, status, actions, retry;
  let lookupBusy = false;
  let forbidden = false;
  let recoveryTimer, recoveryStartedAt, recoveryStep;
  let leftPage = false, lifetime = new AbortController();
  ui.stopPolling();
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
    retry.textContent = '학생 응답 다시 받기';
    retry.addEventListener('click', () => void send(true));
    const end = document.createElement('button');
    end.type = 'button';
    end.className = 'btn-secondary';
    end.textContent = '대화 종료';
    end.addEventListener('click', () => document.getElementById('end-session-btn').click());
    const check = document.createElement('button');
    check.type = 'button';
    check.className = 'btn-secondary';
    check.textContent = '상태 다시 확인';
    check.addEventListener('click', () => void recover());
    actions.append(retry, end, check);
    student.querySelector('.message-content-wrapper').append(actions);
    status.textContent = '전송 중';
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
      status.textContent = '학생 생성 중 · 잠정 응답';
      persist();
    } else if (type === 'output.delta') {
      if (data.run_id !== pending.run_id) return;
      student.querySelector('.message-bubble').textContent += data.text;
    } else if (type === 'output.completed') {
      if (data.run_id !== pending.run_id) return;
      reconcile(student, data.message.id);
      student.querySelector('.message-bubble').textContent = data.message.content;
      status.textContent = '저장 완료';
      clearTimeout(recoveryTimer);
      pending = null;
      sessionStorage.setItem(draftKey, input.value);
      sessionStorage.removeItem(storageKey);
      sync();
      if (!ui.isLocked()) input.focus();
      container.dispatchEvent(new CustomEvent('student:completed', {detail:{row:student, ...data}}));
    } else if (['run.failed', 'run.interrupted', 'run.cancelled'].includes(type)) {
      if (data.run_id !== pending.run_id) return;
      Object.assign(pending, data);
      clearTimeout(recoveryTimer);
      pending.partial_text = student.querySelector('.message-bubble').textContent;
      status.textContent = `${data.message} 응답 미완료·대화 기록에 미포함`;
      actions.hidden = false;
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
        failed:'학생 응답 생성에 실패했습니다.', interrupted:'학생 응답이 중단되었습니다.',
        cancelled:'대화가 종료되었습니다.'
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
    status.textContent = '이 실행을 조회하거나 재시도할 권한이 없습니다. 응답 미완료·대화 기록에 미포함';
    actions.hidden = true;
    if (!input.value) input.value = pending.content;
    persist();
    sync();
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
    status.textContent = '상태 확인 중 · 응답 미완료·대화 기록에 미포함';
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
        status.textContent = '요청 수락 여부를 확인할 수 없습니다. 같은 요청을 명시적으로 다시 전송할 수 있습니다.';
        retry.textContent = '같은 요청 다시 전송';
        retry.disabled = false;
        if (!input.value) input.value = pending.content;
        persist();
      }
      else if (response.ok) {
        applySnapshot(await response.json());
        if (pending?.status === 'running') {
          status.textContent = '학생 생성 중 · 저장 상태 확인 중';
          const elapsed = [1000, 3000, 7000, 15000, 30000][recoveryStep++];
          if (elapsed !== undefined && Date.now() < recoveryStartedAt + 30000) {
            recoveryTimer = setTimeout(() => void recover(false), Math.max(0, recoveryStartedAt + elapsed - Date.now()));
          } else {
            status.textContent = '저장 상태가 아직 불명확합니다. 상태 다시 확인을 눌러주세요.';
          }
        }
      }
      else status.textContent = '저장 상태를 확인하지 못했습니다. 상태 다시 확인을 눌러주세요.';
    } catch (error) {
      if (leftPage) return;
      if (error.code === 'AUTH_EXPIRED') expire();
      else status.textContent = '저장 상태를 확인하지 못했습니다. 상태 다시 확인을 눌러주세요.';
    } finally {
      lookupBusy = false;
      sync();
    }
  }

  async function send(isRetry = false) {
    if (ui.isLocked() || leftPage || forbidden) return;
    if (isRetry) {
      if (!pending?.retryable || !['failed', 'interrupted', 'unknown'].includes(pending.status)) return;
      const resend = pending.status === 'unknown';
      pending = {...pending, request_id:resend ? pending.request_id : crypto.randomUUID(),
        request_turn_id:resend ? pending.request_turn_id : pending.turn_id,
        status:'sending', run_id:null, partial_text:''};
      retry.textContent = '학생 응답 다시 받기';
      teacher.dataset.requestId = pending.request_id;
      student.querySelector('.message-bubble').textContent = '';
      status.textContent = '전송 중';
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
      if (response.status === 403) {
        deny();
        return;
      }
      if (response.status === 400 && (await response.clone().json()).detail === 'Session already ended') {
        event('run.cancelled', {run_id:pending.run_id, status:'cancelled', code:'session_ended',
          message:'대화가 종료되었습니다.', retryable:false});
        return;
      }
      if (response.status === 503 || response.status === 429) {
        const detail = (await response.clone().json()).detail;
        if (['configuration_unavailable', 'call_limit_reached'].includes(detail?.code)) {
          pending.status = 'unknown';
          pending.retryable = true;
          status.textContent = detail.code === 'configuration_unavailable' ?
            '관리자에게 AI 연결과 학생 모델 검증을 요청해주세요. 요청은 아직 수락되지 않았습니다.' :
            '호출 한도에 도달했습니다. 잠시 후 같은 요청을 다시 전송해주세요. 요청은 아직 수락되지 않았습니다.';
          retry.textContent = '같은 요청 다시 전송';
          retry.disabled = false;
          actions.hidden = false;
          if (!input.value) input.value = pending.content;
          persist();
          return;
        }
      }
      if (response.ok && response.headers.get('Content-Type')?.includes('application/json')) {
        applySnapshot(await response.json());
        if (pending?.status === 'running') await recover();
      } else {
        await readSSE(response.body, (type, data) => {
          if (pending === attempt && !leftPage) event(type, data);
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
    if (pending) void recover();
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
