export const providers = {openai:'OpenAI', anthropic:'Claude', google:'Gemini'};
export const roles = {student:'학생봇', mentor:'멘토', analysis:'사후 분석'};
const states = {unconfigured:'미설정', ready:'사용 가능', decryption_failed:'복호화 실패',
  unverified:'미검증', verifying:'검증 중', succeeded:'성공', failed:'실패', stale:'재검증 필요'};

export function node(tag, text, attrs = {}) {
  const el = document.createElement(tag);
  if (text != null) el.textContent = text;
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  return el;
}

export function button(text, action, disabled = false) {
  const el = node('button', text, {type:'button'});
  el.disabled = disabled;
  el.addEventListener('click', action);
  return el;
}

export function field(form, name, label, value = '', attrs = {}, choices = null) {
  const group = node('div', null, {class:'form-group'});
  const id = `ai-${name}`;
  const input = node(choices ? 'select' : 'input', null, {id, name, ...attrs});
  if (choices) for (const [key, text] of choices) input.append(node('option', text, {value:key}));
  input.value = value ?? '';
  group.append(node('label', label, {for:id}), input);
  form.append(group);
  return input;
}

export function render(state, actions) {
  const root = document.getElementById('ai-content');
  root.replaceChildren();
  if (!state.master_key_available) root.append(node('p', '마스터 키 설정 필요: 인프라의 원래 마스터 키와 버전을 복원하세요. 키 저장과 AI 호출은 사용할 수 없습니다.'));
  const connections = node('section', null, {'aria-label':'제공자 연결'});
  connections.append(node('h2', '제공자 연결'));
  const cards = node('div', null, {class:'ai-grid'});
  for (const p of state.providers) {
    const card = node('article', null, {'data-provider':p.provider});
    card.append(node('h3', providers[p.provider]),
      node('p', `키 ${p.key_registered ? '등록됨' : '미등록'} ${p.masked_hint || ''} · ${p.enabled ? '활성' : '비활성'} · ${states[p.status] || '사용 불가'}`),
      node('p', `최근 비생성 확인: ${p.verified_at || '없음'}`));
    if (p.status === 'decryption_failed') card.append(node('p', '원래 마스터 키·버전을 복원하세요. 복구할 수 없다면 현재 비밀번호로 키를 삭제한 후 재등록하세요.'));
    if (p.error_code) card.append(node('p', '최근 연결 확인 실패. 연결 설정과 접근 권한을 확인하세요.'));
    const row = node('div', null, {class:'ai-actions'});
    row.append(button(p.key_registered ? '키 교체' : '키 저장', () => actions.connection(p, 'key'), !state.master_key_available || p.status === 'decryption_failed'),
      button(p.enabled ? '비활성화' : '재활성화', () => actions.connection(p, 'enabled'), !p.key_registered || (!p.enabled && (!state.master_key_available || p.status === 'decryption_failed'))),
      button('키 삭제', () => actions.connection(p, 'delete'), !p.key_registered),
      button('비생성 확인·목록 갱신', () => actions.catalog(p), !p.enabled || p.status !== 'ready' || !state.master_key_available || p.catalog.available === false));
    card.append(row, node('p', p.catalog.available === false ? '모델 목록은 아직 제공되지 않습니다.' : `모델 목록: ${p.catalog.stale ? '오래된 목록' : '최근 목록'} · 마지막 갱신 ${p.catalog.fetched_at || '없음'}`));
    if (p.catalog.stale) card.append(node('p', '참고 목록입니다. 접근 가능 여부나 역할 검증 성공을 보장하지 않습니다.'));
    const list = node('ul');
    for (const model of p.catalog.models) {
      const li = node('li');
      li.append(button(model.model_id, () => actions.register(p, model.model_id), state.models_available === false));
      list.append(li);
    }
    card.append(list, button('모델 ID 직접 입력', () => actions.register(p, ''), state.models_available === false));
    cards.append(card);
  }
  connections.append(cards);
  root.append(connections);
  const models = node('section', null, {'aria-label':'모델 설정'});
  models.append(node('h2', '모델 설정'));
  if (state.models_available === false) models.append(node('p', '모델 등록·역할 시험은 아직 제공되지 않습니다.'));
  if (state.probes_available === false) models.append(node('p', '역할 시험은 아직 제공되지 않습니다.'));
  for (const m of state.models) {
    const p = state.providers.find(p => p.provider === m.provider);
    const card = node('article', null, {'data-model':m.id});
    card.append(node('h3', `${m.display_name} · ${providers[m.provider]} / ${m.model_id}`),
      node('p', `${m.enabled ? '활성' : '비활성'} · ${m.capabilities ? '기능 정의 있음' : '기능 정의 필요'}`),
      button('표시명·활성·옵션 수정', () => actions.model(m)));
    if (m.capabilities?.metadata_conflict) card.append(node('p', '기능 정의·제공자 목록 불일치: 역할 시험과 실행을 사용할 수 없습니다.'));
    for (const [role, label] of Object.entries(roles)) {
      const verification = m.verification_state[role];
      const row = node('div', null, {class:'ai-actions'});
      row.append(node('p', `${label}: ${states[verification.status] || '미검증'}${verification.verified_at ? ` · ${verification.verified_at}` : ''}`));
      if (verification.error_code) row.append(node('p', '역할 응답 형식 시험에 실패했습니다. 설정을 확인한 후 다시 시험하세요.'));
      row.append(button(`${label} 시험`, () => actions.probe(m, role), state.probes_available === false || !m.capabilities || m.capabilities.metadata_conflict || !state.master_key_available || !p.enabled || p.status !== 'ready' || verification.status === 'verifying'));
      if (verification.status === 'verifying') row.append(button(`${label} 진행 조회`, () => actions.poll(verification.probe_request_id)), button(`${label} 시험 취소`, () => actions.cancel(verification.probe_request_id)));
      card.append(row);
    }
    models.append(card);
  }
  root.append(models);
  const settings = node('section', null, {'aria-label':'작성 기본값·호출 설정'});
  settings.append(node('h2', '작성 기본값·호출 설정'), node('p', '작성 기본값은 새 시나리오 작성에만 사용합니다. 기존 시나리오와 세션은 변경하지 않습니다.'));
  if (!state.settings) {
    settings.append(node('p', '작성 기본값·호출 설정은 아직 제공되지 않습니다.'));
    root.append(settings);
    return;
  }
  for (const [role, label] of Object.entries(roles)) {
    const d = state.settings.defaults[role];
    const m = state.models.find(m => m.id === d?.model_config_id);
    settings.append(node('p', `${label}: ${d ? `${m?.display_name || d.model_config_id} · ${d.available ? '사용 가능' : '기본 모델 사용 불가'}` : '기본값 없음'}`));
  }
  settings.append(button('작성 기본값·호출 설정 수정', () => actions.settings(state)));
  root.append(settings);
}
