import {readSSE} from './student-sse.js';

// A8 mounts this on the product page; A7 mounts it only in the browser fixture.
export function mountMentorStream(ui) {
  const container = document.getElementById('messages-container');
  if (container.dataset.mentorStream) return;
  container.dataset.mentorStream = 'true';
  const turns = new Map();

  function start(row, turnId) {
    if (ui.isLocked() || turns.has(turnId)) return;
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
    status.textContent = '멘토 처리 중';
    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';
    bubble.style.whiteSpace = 'pre-wrap';
    bubble.hidden = true;
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'btn-secondary';
    retry.textContent = '멘토 다시 요청';
    retry.hidden = true;
    wrapper.append(sender, bubble, status, retry);
    slot.append(wrapper);
    row.after(slot);
    const turn = {slot, status, bubble, retry};
    turns.set(turnId, turn);
    retry.addEventListener('click', () => {
      if (!turn.running && !ui.isLocked()) void send(turnId, turn);
    });
    void send(turnId, turn);
  }

  function finish(turn, state, message, retryable = false) {
    turn.running = false;
    turn.slot.dataset.status = state;
    turn.status.textContent = message;
    turn.retry.hidden = !retryable || ui.isLocked();
  }

  async function send(turnId, turn) {
    turn.requestId = crypto.randomUUID();
    const requestId = turn.requestId;
    turn.runId = null;
    turn.running = true;
    turn.retry.hidden = true;
    turn.slot.dataset.status = 'running';
    turn.status.textContent = '멘토 처리 중';
    turn.controller = new AbortController();
    try {
      const response = await ui.fetch(`/sessions/${ui.sessionId}/turns/${turnId}/mentor/stream`, {
        method:'POST', headers:ui.headers(), body:JSON.stringify({request_id:turn.requestId}), signal:turn.controller.signal
      });
      if (turn.requestId !== requestId || !turn.running || ui.isLocked()) {
        await response.body?.cancel();
        return;
      }
      if (!response.ok) {
        const data = await response.json();
        if (data.code === 'mentor_busy') {
          finish(turn, 'busy', '이전 코칭 처리 중 — 이후 다시 요청 가능', true);
        } else if (data.code === 'mentor_turn_obsolete') {
          finish(turn, 'obsolete', '더 최신 턴의 멘토 요청이 있어 이 턴은 더 이상 재요청할 수 없습니다.');
        } else if (data.code === 'session_ended') {
          finish(turn, 'cancelled', '대화가 종료되어 멘토를 다시 요청할 수 없습니다.');
          ui.end();
        } else if (response.status === 401) {
          ui.expire();
        } else {
          finish(turn, 'failed', '멘토 요청을 처리하지 못했습니다.', ![401, 403, 404].includes(response.status));
        }
        return;
      }
      await readSSE(response.body, (type, data) => {
        if (turn.requestId !== requestId || !turn.running || ui.isLocked()) return false;
        if (data.turn_id !== turnId) return true;
        if (type === 'run.accepted' && data.request_id === turn.requestId && data.operation === 'mentor') {
          turn.runId = data.run_id;
        } else if (data.run_id === turn.runId) {
          if (type === 'output.completed') {
            finish(turn, 'completed', data.result_kind === 'no_intervention' ? '멘토 미개입' : '멘토 코칭 완료');
            if (data.message) {
              turn.slot.dataset.messageId = data.message.id;
              turn.bubble.textContent = data.message.content;
              turn.bubble.hidden = false;
            }
          } else if (['run.failed', 'run.interrupted', 'run.cancelled'].includes(type)) {
            finish(turn, data.status, type === 'run.cancelled' ? '대화가 종료되어 멘토를 다시 요청할 수 없습니다.' : data.message, data.retryable);
            if (type === 'run.cancelled') ui.end();
          }
        }
        return turn.running;
      });
      // ponytail: A8 must recover the stored run after EOF before offering a paid retry.
      if (turn.requestId === requestId && turn.running) finish(turn, 'unknown', '멘토 실행 상태를 확인할 수 없습니다.');
    } catch (error) {
      if (error.code === 'AUTH_EXPIRED') ui.expire();
      if (turn.requestId === requestId && turn.running) finish(turn, 'unknown', '멘토 실행 상태를 확인할 수 없습니다.');
    }
  }

  function stop(state, message) {
    for (const turn of turns.values()) {
      const running = turn.running;
      if (running || !['completed', 'obsolete'].includes(turn.slot.dataset.status)) finish(turn, state, message);
      turn.retry.hidden = true;
      if (running) turn.controller.abort();
    }
  }

  container.addEventListener('student:completed', e => start(e.detail.row, e.detail.turn_id));
  document.addEventListener('chat:locked', e => stop('cancelled', e.detail.authExpired ?
    '로그인이 만료되어 멘토를 다시 요청할 수 없습니다.' : '대화가 종료되어 멘토를 다시 요청할 수 없습니다.'));
  window.addEventListener('pagehide', () => stop('interrupted', '페이지 이동으로 멘토 코칭이 중단되었습니다.'));
}
