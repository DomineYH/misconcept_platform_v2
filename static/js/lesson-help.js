const messages = {
  ready:'완료된 학생 턴에 도움을 요청할 수 있습니다.',
  running:'멘토 처리 중 — 학생 대화는 계속할 수 있습니다.',
  failed:'멘토 코칭 생성에 실패했습니다.',
  rate_limited:'동시 호출 한도에 도달했습니다. 잠시 후 다시 요청하세요.',
  limit:'최근 완료 턴의 개입 상한에 도달했습니다.',
  unavailable:'현재 이 턴에 도움을 요청할 수 없습니다.',
  completed:'멘토 코칭 완료', no_intervention:'멘토 미개입'
};

// The lesson controller supplies admission and requests; this component only displays safe help state.
export function mountLessonHelp(root, initial, onRequest) {
  const help = root.querySelector('#request-mentor');
  if (!help) return;
  const retry = root.querySelector('#retry-mentor');
  const status = root.querySelector('#mentor-help-status');
  const result = root.querySelector('#mentor-result');
  let current;
  function render(data) {
    current = data.result_kind === 'no_intervention' ? 'no_intervention' : data.status;
    status.textContent = messages[current] || messages.unavailable;
    help.disabled = !['ready', 'no_intervention'].includes(current);
    retry.hidden = !['failed', 'rate_limited'].includes(current);
    result.textContent = data.message?.content || '';
    result.hidden = !result.textContent;
  }
  async function request(trigger = 'manual') {
    if (!['ready', 'no_intervention', 'failed', 'rate_limited'].includes(current)) return;
    render({status:'running'});
    try { render(await onRequest(trigger)); }
    catch { render({status:'failed'}); }
  }
  help.addEventListener('click', () => request());
  retry.addEventListener('click', () => request());
  render(initial);
  return {request, render};
}
