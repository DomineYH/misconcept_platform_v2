import {readSSE} from './student-sse.js';

function failureText(code) {
  if (['timeout_connect', 'timeout_first_output', 'timeout_total'].includes(code)) {
    return uiLanguage.t('teacher.mentor_timeout');
  }
  return {
    invalid_output:uiLanguage.t('teacher.mentor_invalid'),
    refused:uiLanguage.t('teacher.mentor_refused'),
    context_limit:uiLanguage.t('teacher.context_limit')
  }[code] || uiLanguage.t('teacher.mentor_failed');
}

export function mountMentorStream(ui) {
  const container = document.getElementById('messages-container');
  if (container.dataset.mentorStream) return;
  container.dataset.mentorStream = 'true';
  const turns = new Map();
  uiLanguage.registerGuard(() => ({
    busy: Array.from(turns.values()).some(turn => turn.running || turn.lookupBusy || turn.state === 'unknown')
  }));
  const help = document.getElementById('request-mentor');
  const helpStatus = document.getElementById('mentor-help-status');
  const storageKey = uiLanguage.storageKey(`chat-mentor-${ui.sessionId}`);
  const captured = uiLanguage.storageKey('');
  let saved = {};
  try { saved = JSON.parse(sessionStorage.getItem(storageKey) || '{}'); } catch {}
  let leftPage = false;

  function persist() {
    const records = {};
    for (const [id, turn] of turns) records[id] = {
      trigger:turn.trigger, requestId:turn.requestId, runId:turn.runId, state:turn.state,
      statusText:turn.status.textContent, retryable:turn.retryable,
      resend:turn.resend, result:turn.result
    };
    sessionStorage.setItem(storageKey, JSON.stringify(records));
  }

  function start(row, turnId, restored, trigger = 'auto') {
    if (ui.isLocked() || turns.has(turnId)) return;
    const existing = Array.from(container.querySelectorAll('.message-tutor[data-turn-id]'))
      .find(el => el.dataset.turnId === turnId);
    if (existing) { row.after(existing); return; }
    const slot = document.createElement('div');
    slot.className = 'message message-mentor mentor-slot';
    slot.dataset.turnId = turnId;
    slot.setAttribute('role', 'group');
    slot.setAttribute('aria-label', uiLanguage.t('teacher.mentor_target_turn', {name:ui.mentorName || uiLanguage.t('teacher.mentor'), turn:row.dataset.turnIndex || turnId}));
    const wrapper = document.createElement('div');
    wrapper.className = 'message-content-wrapper';
    const sender = document.createElement('div');
    sender.className = 'message-sender';
    sender.textContent = ui.mentorName || uiLanguage.t('teacher.mentor');
    const status = document.createElement('div');
    status.className = 'message-meta mentor-run-status';
    status.setAttribute('role', 'status');
    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';
    bubble.style.whiteSpace = 'pre-wrap';
    bubble.hidden = true;
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'btn-secondary';
    retry.textContent = uiLanguage.t('teacher.mentor_retry');
    retry.hidden = true;
    const check = document.createElement('button');
    check.type = 'button';
    check.className = 'btn-secondary';
    check.textContent = uiLanguage.t('teacher.check_status');
    check.hidden = true;
    const note = document.createElement('p');
    note.className = 'message-meta';
    note.textContent = uiLanguage.t('teacher.mentor_no_coaching');
    note.hidden = true;
    wrapper.append(sender, bubble, status, note, retry, check);
    slot.append(wrapper);
    row.after(slot);
    const turn = {slot, status, bubble, note, retry, check, trigger, ...restored};
    turns.set(turnId, turn);
    retry.addEventListener('click', () => {
      if (!turn.running && !ui.isLocked()) void send(turnId, turn);
    });
    check.addEventListener('click', () => void recover(turnId, turn));
    if (restored?.result) apply(turn, restored.result);
    else if (restored?.requestId) void recover(turnId, turn);
    else void send(turnId, turn);
  }

  function finish(turn, state, message, retryable = false) {
    clearTimeout(turn.timer);
    turn.running = false;
    turn.state = turn.slot.dataset.status = state;
    turn.status.textContent = message;
    turn.retryable = retryable;
    turn.retry.hidden = !retryable || ui.isLocked();
    turn.check.hidden = true;
    persist();
    refreshHelp();
  }

  function apply(turn, data) {
    if (data.status === 'completed') {
      turn.result = data;
      finish(turn, 'completed', data.result_kind === 'no_intervention' ? uiLanguage.t('teacher.mentor_no_intervention') : uiLanguage.t('teacher.mentor_completed'));
      turn.note.hidden = data.result_kind !== 'no_intervention';
      if (data.message) {
        container.querySelectorAll('[data-message-id]').forEach(other => {
          if (other !== turn.slot && other.dataset.messageId === String(data.message.id)) other.remove();
        });
        turn.slot.dataset.messageId = data.message.id;
        turn.bubble.textContent = data.message.content;
        turn.bubble.hidden = false;
      }
    } else if (['failed', 'interrupted', 'cancelled'].includes(data.status)) {
      const hint = uiLanguage.recovery(data);
      if (hint && ui.isLocked()) return;
      finish(turn, data.status, hint ?? (data.status === 'cancelled' ?
        uiLanguage.t('teacher.mentor_session_ended') : failureText(data.code || data.error_code)), data.retryable);
      if (data.status === 'cancelled') ui.end();
    } else {
      turn.runId = data.run_id;
      turn.running = true;
      turn.state = turn.slot.dataset.status = 'running';
      persist();
    }
  }

  async function recover(turnId, turn, reset = true) {
    if (turn.lookupBusy || ui.isLocked() || leftPage) return;
    if (reset) {
      clearTimeout(turn.timer);
      turn.recoveryStarted = Date.now();
      turn.recoveryStep = 0;
    }
    turn.lookupBusy = true;
    turn.running = true;
    turn.retry.hidden = true;
    turn.check.hidden = true;
    turn.status.textContent = uiLanguage.t('teacher.mentor_checking');
    const requestId = turn.requestId;
    turn.controller = new AbortController();
    try {
      const url = turn.runId ? `/runs/${turn.runId}` :
        `/sessions/${ui.sessionId}/runs?request_id=${encodeURIComponent(requestId)}`;
      const response = await ui.fetch(url, {signal:turn.controller.signal});
      if (turn.requestId !== requestId || leftPage || ui.isLocked()) return;
      if (response.status === 401) ui.expire();
      else if (response.status === 403) finish(turn, 'forbidden', uiLanguage.t('teacher.mentor_forbidden'));
      else if (response.status === 404) {
        turn.resend = true;
        turn.retry.textContent = uiLanguage.t('teacher.resend_request');
        finish(turn, 'unknown', uiLanguage.t('teacher.mentor_unknown_request'), true);
        turn.check.hidden = false;
      } else if (response.ok) {
        apply(turn, await response.json());
        if (turn.running) {
          const elapsed = [1000, 3000, 7000, 15000, 30000][turn.recoveryStep++];
          if (elapsed !== undefined && Date.now() < turn.recoveryStarted + 30000) {
            turn.timer = setTimeout(() => void recover(turnId, turn, false),
              Math.max(0, turn.recoveryStarted + elapsed - Date.now()));
          } else unknown(turn);
        }
      } else unknown(turn);
    } catch (error) {
      if (leftPage || ui.isLocked()) return;
      if (error.code === 'AUTH_EXPIRED') ui.expire();
      else unknown(turn);
    } finally { turn.lookupBusy = false; }
  }

  function unknown(turn) {
    finish(turn, 'unknown', uiLanguage.t('teacher.mentor_unknown_saved'));
    turn.check.hidden = false;
  }

  async function send(turnId, turn) {
    if (ui.isLocked() || leftPage) return;
    if (!turn.resend) {
      turn.requestId = crypto.randomUUID();
      if (turn.state) turn.trigger = 'manual';
    }
    turn.resend = false;
    turn.retry.textContent = uiLanguage.t('teacher.mentor_retry');
    const requestId = turn.requestId;
    turn.runId = null;
    turn.running = true;
    turn.state = turn.slot.dataset.status = 'running';
    turn.retry.hidden = turn.check.hidden = true;
    turn.note.hidden = true;
    turn.status.textContent = uiLanguage.t('teacher.mentor_processing');
    turn.controller = new AbortController();
    persist();
    refreshHelp();
    try {
      const response = await ui.fetch(`/sessions/${ui.sessionId}/turns/${turnId}/mentor/stream`, {
        method:'POST', headers:ui.headers(), body:JSON.stringify({request_id:requestId, trigger:turn.trigger}), signal:turn.controller.signal
      });
      if (turn.requestId !== requestId || !turn.running || ui.isLocked()) {
        await response.body?.cancel();
        return;
      }
      if (!response.ok) {
        const payload = await response.json();
        const data = payload.detail || payload;
        const hint = uiLanguage.recovery(data);
        if (hint) {
          finish(turn, 'failed', hint, data.retryable === true);
        } else if (data.code === 'mentor_busy') {
          finish(turn, 'busy', uiLanguage.t('teacher.mentor_busy'), true);
        } else if (data.code === 'mentor_limit') {
          finish(turn, 'limit', uiLanguage.t('teacher.mentor_limit'));
        } else if (['mentor_start_turn', 'mentor_interval'].includes(data.code)) {
          finish(turn, 'ready', uiLanguage.t('teacher.mentor_interval'));
        } else if (data.code === 'mentor_disabled' || data.code === 'mentor_auto_disabled') {
          finish(turn, 'unavailable', uiLanguage.t('teacher.mentor_unavailable'));
        } else if (data.code === 'mentor_turn_obsolete') {
          finish(turn, 'obsolete', uiLanguage.t('teacher.mentor_obsolete'));
        } else if (data.code === 'session_ended') {
          finish(turn, 'cancelled', uiLanguage.t('teacher.mentor_session_ended'));
          ui.end();
        } else if (response.status === 401) {
          ui.expire();
        } else if (response.status === 503 && data.code === 'configuration_unavailable') {
          finish(turn, 'failed', uiLanguage.t('teacher.mentor_configuration'), true);
        } else if (response.status === 429 && data.code === 'call_limit_reached') {
          finish(turn, 'failed', uiLanguage.t('teacher.mentor_capacity'), true);
        } else if (response.status === 422 && data.code === 'context_limit') {
          finish(turn, 'failed', failureText(data.code));
        } else {
          finish(turn, 'failed', uiLanguage.t('teacher.mentor_request_failed'), ![400, 401, 403, 404, 422].includes(response.status));
        }
        return;
      }
      if (response.headers.get('Content-Type')?.includes('application/json')) {
        apply(turn, await response.json());
      } else await readSSE(response.body, (type, data) => {
        if (turn.requestId !== requestId || !turn.running || ui.isLocked()) return false;
        if (!uiLanguage.current(captured, data)) return false;
        if (data.turn_id !== turnId) return true;
        if (type === 'run.accepted' && data.request_id === requestId && data.operation === 'mentor') {
          turn.runId = data.run_id;
          persist();
        } else if (data.run_id === turn.runId &&
          ['output.completed', 'run.failed', 'run.interrupted', 'run.cancelled'].includes(type)) {
          apply(turn, data);
        }
        return turn.running;
      });
      if (turn.requestId === requestId && turn.running) await recover(turnId, turn);
    } catch (error) {
      if (leftPage || ui.isLocked()) return;
      if (error.code === 'AUTH_EXPIRED') ui.expire();
      else if (turn.requestId === requestId && turn.running) await recover(turnId, turn);
    }
  }

  function stop(state, message) {
    if (help) help.disabled = true;
    for (const turn of turns.values()) {
      const running = turn.running;
      clearTimeout(turn.timer);
      if (!leftPage && (running || !['completed', 'obsolete'].includes(turn.state))) finish(turn, state, message);
      turn.retry.hidden = turn.check.hidden = true;
      if (running) turn.controller?.abort();
    }
  }

  function latestRow() {
    return Array.from(container.querySelectorAll('.message-student[data-turn-id][data-message-id]')).at(-1);
  }

  function refreshHelp() {
    if (!help) return;
    const row = latestRow();
    const turn = row && turns.get(row.dataset.turnId);
    const coached = row && Array.from(container.querySelectorAll('.message-tutor[data-turn-id], .mentor-slot[data-message-id]'))
      .some(item => item.dataset.turnId === row.dataset.turnId);
    const busy = Array.from(turns.values()).some(item => item.running);
    help.disabled = ui.isLocked() || !row || busy || coached ||
      (turn && (turn.result?.result_kind === 'message' || ['limit', 'unavailable', 'obsolete'].includes(turn.state)));
    helpStatus.textContent = turn?.status.textContent || (coached ? uiLanguage.t('teacher.mentor_completed') : uiLanguage.t('teacher.mentor_help_available'));
    for (const [id, old] of turns) {
      if (id !== row?.dataset.turnId && !old.running) old.retry.hidden = true;
    }
  }

  for (const coach of container.querySelectorAll('.message-tutor[data-turn-id]')) {
    const row = Array.from(container.querySelectorAll('.message-student[data-turn-id]'))
      .find(el => el.dataset.turnId === coach.dataset.turnId);
    if (row) row.after(coach);
  }
  if (ui.mentorEnabled === false) return;
  for (const row of container.querySelectorAll('.message-student[data-turn-id]')) {
    if (saved[row.dataset.turnId]) start(row, row.dataset.turnId, saved[row.dataset.turnId]);
  }
  help?.addEventListener('click', () => {
    const row = latestRow();
    if (!row || help.disabled) return;
    const turn = turns.get(row.dataset.turnId);
    if (turn) { turn.resend = false; void send(row.dataset.turnId, turn); }
    else start(row, row.dataset.turnId, null, 'manual');
  });
  container.addEventListener('student:completed', e => {
    if (ui.mentorMode === 'auto') start(e.detail.row, e.detail.turn_id);
    refreshHelp();
  });
  refreshHelp();
  document.addEventListener('chat:locked', e => stop('cancelled', e.detail.authExpired ?
    uiLanguage.t('teacher.mentor_auth_expired') : uiLanguage.t('teacher.mentor_session_ended')));
  uiLanguage.registerStale(message => stop('cancelled', message));
  window.addEventListener('pagehide', () => {
    leftPage = true;
    stop('interrupted', uiLanguage.t('teacher.mentor_page_left'));
  });
}
