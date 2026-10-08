import {readSSE} from './student-sse.js';

export function mountMentorStream(ui) {
  const container = document.getElementById('messages-container');
  if (container.dataset.mentorStream) return;
  container.dataset.mentorStream = 'true';
  const turns = new Map();
  const storageKey = `chat-mentor-${ui.sessionId}`;
  let saved = {};
  try { saved = JSON.parse(sessionStorage.getItem(storageKey) || '{}'); } catch {}
  let leftPage = false;

  function persist() {
    const records = {};
    for (const [id, turn] of turns) records[id] = {
      requestId:turn.requestId, runId:turn.runId, state:turn.state,
      statusText:turn.status.textContent, retryable:turn.retryable,
      resend:turn.resend, result:turn.result
    };
    sessionStorage.setItem(storageKey, JSON.stringify(records));
  }

  function start(row, turnId, restored) {
    if (ui.isLocked() || turns.has(turnId)) return;
    const existing = Array.from(container.querySelectorAll('.message-tutor[data-turn-id]'))
      .find(el => el.dataset.turnId === turnId);
    if (existing) { row.after(existing); return; }
    const slot = document.createElement('div');
    slot.className = 'message message-mentor mentor-slot';
    slot.dataset.turnId = turnId;
    const wrapper = document.createElement('div');
    wrapper.className = 'message-content-wrapper';
    const sender = document.createElement('div');
    sender.className = 'message-sender';
    sender.textContent = '멘토';
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
    retry.textContent = '멘토 다시 요청';
    retry.hidden = true;
    const check = document.createElement('button');
    check.type = 'button';
    check.className = 'btn-secondary';
    check.textContent = '상태 다시 확인';
    check.hidden = true;
    wrapper.append(sender, bubble, status, retry, check);
    slot.append(wrapper);
    row.after(slot);
    const turn = {slot, status, bubble, retry, check, ...restored};
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
  }

  function apply(turn, data) {
    if (data.status === 'completed') {
      turn.result = data;
      finish(turn, 'completed', data.result_kind === 'no_intervention' ? '멘토 미개입' : '멘토 코칭 완료');
      if (data.message) {
        container.querySelectorAll('[data-message-id]').forEach(other => {
          if (other !== turn.slot && other.dataset.messageId === String(data.message.id)) other.remove();
        });
        turn.slot.dataset.messageId = data.message.id;
        turn.bubble.textContent = data.message.content;
        turn.bubble.hidden = false;
      }
    } else if (['failed', 'interrupted', 'cancelled'].includes(data.status)) {
      finish(turn, data.status, data.status === 'cancelled' ?
        '대화가 종료되어 멘토를 다시 요청할 수 없습니다.' : '멘토 코칭 생성에 실패했습니다.', data.retryable);
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
    turn.status.textContent = '멘토 저장 상태 확인 중';
    const requestId = turn.requestId;
    turn.controller = new AbortController();
    try {
      const url = turn.runId ? `/runs/${turn.runId}` :
        `/sessions/${ui.sessionId}/runs?request_id=${encodeURIComponent(requestId)}`;
      const response = await ui.fetch(url, {signal:turn.controller.signal});
      if (turn.requestId !== requestId || leftPage || ui.isLocked()) return;
      if (response.status === 401) ui.expire();
      else if (response.status === 403) finish(turn, 'forbidden', '이 멘토 실행을 조회할 권한이 없습니다.');
      else if (response.status === 404) {
        turn.resend = true;
        turn.retry.textContent = '같은 요청 다시 전송';
        finish(turn, 'unknown', '멘토 요청 수락 여부를 확인할 수 없습니다.', true);
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
    finish(turn, 'unknown', '멘토 저장 상태가 아직 불명확합니다. 상태 다시 확인을 눌러주세요.');
    turn.check.hidden = false;
  }

  async function send(turnId, turn) {
    if (ui.isLocked() || leftPage) return;
    if (!turn.resend) turn.requestId = crypto.randomUUID();
    turn.resend = false;
    turn.retry.textContent = '멘토 다시 요청';
    const requestId = turn.requestId;
    turn.runId = null;
    turn.running = true;
    turn.state = turn.slot.dataset.status = 'running';
    turn.retry.hidden = turn.check.hidden = true;
    turn.status.textContent = '멘토 처리 중';
    turn.controller = new AbortController();
    persist();
    try {
      const response = await ui.fetch(`/sessions/${ui.sessionId}/turns/${turnId}/mentor/stream`, {
        method:'POST', headers:ui.headers(), body:JSON.stringify({request_id:requestId}), signal:turn.controller.signal
      });
      if (turn.requestId !== requestId || !turn.running || ui.isLocked()) {
        await response.body?.cancel();
        return;
      }
      if (!response.ok) {
        const payload = await response.json();
        const data = payload.detail || payload;
        if (data.code === 'mentor_busy') {
          finish(turn, 'busy', '이전 코칭 처리 중 — 이후 다시 요청 가능', true);
        } else if (data.code === 'mentor_turn_obsolete') {
          finish(turn, 'obsolete', '더 최신 턴의 멘토 요청이 있어 이 턴은 더 이상 재요청할 수 없습니다.');
        } else if (data.code === 'session_ended') {
          finish(turn, 'cancelled', '대화가 종료되어 멘토를 다시 요청할 수 없습니다.');
          ui.end();
        } else if (response.status === 401) {
          ui.expire();
        } else if (response.status === 503 && data.code === 'configuration_unavailable') {
          finish(turn, 'failed', '관리자에게 AI 연결과 멘토 모델 검증을 요청해주세요.', true);
        } else if (response.status === 429 && data.code === 'call_limit_reached') {
          finish(turn, 'failed', 'AI 호출이 많습니다. 잠시 후 멘토를 다시 요청해주세요.', true);
        } else {
          finish(turn, 'failed', '멘토 요청을 처리하지 못했습니다.', ![400, 401, 403, 404, 422].includes(response.status));
        }
        return;
      }
      if (response.headers.get('Content-Type')?.includes('application/json')) {
        apply(turn, await response.json());
      } else await readSSE(response.body, (type, data) => {
        if (turn.requestId !== requestId || !turn.running || ui.isLocked()) return false;
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
    for (const turn of turns.values()) {
      const running = turn.running;
      clearTimeout(turn.timer);
      if (!leftPage && (running || !['completed', 'obsolete'].includes(turn.state))) finish(turn, state, message);
      turn.retry.hidden = turn.check.hidden = true;
      if (running) turn.controller?.abort();
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
  container.addEventListener('student:completed', e => start(e.detail.row, e.detail.turn_id));
  document.addEventListener('chat:locked', e => stop('cancelled', e.detail.authExpired ?
    '로그인이 만료되어 멘토를 다시 요청할 수 없습니다.' : '대화가 종료되어 멘토를 다시 요청할 수 없습니다.'));
  window.addEventListener('pagehide', () => {
    leftPage = true;
    stop('interrupted', '페이지 이동으로 멘토 코칭이 중단되었습니다.');
  });
}
