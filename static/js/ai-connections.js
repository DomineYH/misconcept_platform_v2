import {render, node, button, field, providers, roles} from './ai-render.js';

const status = document.getElementById('ai-status');
const error = document.getElementById('ai-error');
const editor = document.getElementById('ai-editor');
let state, busy = false, returnFocus;

function showError(code) {
  const messages = {401:'로그인이 필요합니다.', 403:'권한 또는 보안 토큰을 확인하세요.',
    409:'다른 변경 또는 진행 중 작업과 충돌했습니다. 설정을 다시 불러오세요.',
    422:'입력값 또는 현재 비밀번호를 확인하세요.', 429:'작업 한도에 도달했습니다. 잠시 후 다시 시도하세요.'};
  error.textContent = messages[code] || '작업을 완료할 수 없습니다. 연결 설정과 서비스 상태를 확인하세요.';
  error.hidden = false;
  error.focus();
}

async function request(path, body) {
  const headers = {};
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    headers['x-csrf-token'] = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/)?.[1] || '';
  }
  const response = await fetch(`/admin/ai/${path}`, {method:body === undefined ? 'GET' : 'POST',
    credentials:'same-origin', headers, ...(body === undefined ? {} : {body:JSON.stringify(body)})});
  // Never display raw server detail, validation input, or exception text.
  if (!response.ok) throw response.status;
  return response.status === 204 ? null : response.json();
}

async function load() {
  try {
    const next = await request('state');
    state = next;
    render(state, actions);
    error.hidden = true;
    status.textContent = '설정을 불러왔습니다. 화면 진입은 제공자를 호출하지 않습니다.';
    return true;
  } catch (code) {
    showError(code);
    status.textContent = 'AI 연결 설정을 불러올 수 없습니다. 관리자 연결 기능이 준비되었는지 확인하세요.';
    return false;
  }
}

async function submit(path, body, message = '작업을 완료했습니다.') {
  if (busy) return;
  busy = true;
  error.hidden = true;
  status.textContent = '작업 중입니다.';
  const submitButton = editor.querySelector('button[type=submit]');
  if (submitButton) submitButton.disabled = true;
  try {
    const result = await request(path, body);
    const refreshed = await load();
    status.textContent = refreshed ? message : `${message} 최신 상태를 다시 불러오세요.`;
    return result;
  } catch (code) {
    showError(code);
    status.textContent = '작업에 실패했습니다.';
  } finally {
    editor.querySelectorAll('input[type=password]').forEach(input => { input.value = ''; });
    if (submitButton) submitButton.disabled = false;
    busy = false;
  }
}

function closeEditor() {
  editor.replaceChildren();
  editor.hidden = true;
  (returnFocus?.isConnected ? returnFocus : document.getElementById('ai-refresh')).focus();
}

function form(title, onSubmit) {
  returnFocus = document.activeElement;
  editor.replaceChildren(node('h2', title));
  editor.hidden = false;
  const form = node('form');
  form.addEventListener('submit', event => { event.preventDefault(); onSubmit(form); });
  editor.append(form);
  return form;
}

function finish(form, label) {
  form.append(node('button', label, {type:'submit'}), button('닫기', closeEditor));
  (form.querySelector('input, select') || form.querySelector('button')).focus();
  editor.scrollIntoView({block:'nearest'});
}

const actions = {
  connection(p, operation) {
    if (busy) return;
    const f = form(`${providers[p.provider]} 연결 변경`, f => {
      const payload = {expected_version:p.connection_version, current_password:f.elements.current_password.value};
      if (operation === 'key') payload.api_key = f.elements.api_key.value;
      if (operation === 'enabled') payload.enabled = !p.enabled;
      f.querySelectorAll('input[type=password]').forEach(input => { input.value = ''; });
      submit(`providers/${p.provider}/${operation}`, payload);
    });
    f.append(node('p', '키 교체·재활성화 후에는 역할 재검증이 필요합니다. 교체 전에 시작한 호출은 기존 키로 완료할 수 있습니다. 비활성화·삭제는 새 호출을 즉시 차단하고 이전 키로 시작한 호출에도 중단을 요청합니다. 제공자 처리 및 이미 발생한 비용은 취소되지 않을 수 있습니다. 모델 설정과 과거 기록은 보존합니다.'));
    const impact = node('ul');
    for (const item of p.impact) impact.append(node('li', item));
    f.append(node('p', '영향 범위'), impact);
    if (operation === 'key') field(f, 'api_key', '새 API 키', '', {type:'password', required:'', autocomplete:'off'});
    field(f, 'current_password', '현재 비밀번호', '', {type:'password', required:'', autocomplete:'off'});
    finish(f, '변경 제출');
  },
  catalog(p) { submit(`providers/${p.provider}/catalog`, {expected_version:p.connection_version}, '비생성 확인·목록 갱신을 완료했습니다.'); },
  register(p, modelId) {
    if (busy) return;
    const f = form(`${providers[p.provider]} 모델 등록`, f => {
      submit('models', {provider:p.provider, model_id:f.elements.model_id.value.trim(),
        display_name:f.elements.display_name.value.trim()}, '모델 등록을 완료했습니다. 역할 시험 전에는 실행할 수 없습니다.');
    });
    f.append(node('p', '새 모델은 비활성·미검증으로 등록합니다. 목록에 있어도 역할 시험은 별도로 필요합니다. 직접 입력 모델은 기능 정의가 필요할 수 있습니다.'));
    field(f, 'model_id', '모델 ID', modelId, {type:'text', required:''});
    field(f, 'display_name', '표시명', modelId, {type:'text', required:''});
    finish(f, '모델 등록');
  },
  model(m) {
    if (busy) return;
    const f = form(`${m.model_id} 모델 설정`, f => {
      const options = {};
      for (const definition of m.capabilities?.fields || []) {
        const value = f.elements[definition.name].value;
        if (value === '') continue;
        const path = definition.name.split('.');
        let target = options;
        for (const part of path.slice(0, -1)) target = target[part] ||= {};
        target[path.at(-1)] = ['integer', 'number'].includes(definition.type) ? Number(value) : value;
      }
      submit(`models/${m.id}/update`, {expected_version:m.config_version,
        display_name:f.elements.display_name.value, enabled:f.elements.enabled.value === 'true',
        default_options:options});
    });
    field(f, 'display_name', '표시명', m.display_name, {type:'text', required:''});
    const enabled = field(f, 'enabled', '모델 활성', String(m.enabled), {}, [['false','비활성'],['true','활성']]);
    enabled.disabled = !m.enabled && (!m.capabilities || m.capabilities.metadata_conflict || !Object.values(m.verification_state).some(v => v.status === 'succeeded'));
    f.append(node('p', '모델 ID는 등록 후 변경할 수 없습니다. 모델 비활성화는 신규 호출만 막습니다. 지원 옵션 변경은 서버에서 조합과 범위를 검증합니다.'));
    if (!m.capabilities) f.append(node('p', '기능 정의 필요: 옵션 편집과 역할 시험을 사용할 수 없습니다.'));
    for (const definition of m.capabilities?.fields || []) {
      const value = definition.name.split('.').reduce((obj, key) => obj?.[key], m.default_options);
      const attrs = {type:['integer','number'].includes(definition.type) ? 'number' : 'text'};
      if (definition.min != null) attrs.min = definition.min;
      if (definition.max != null) attrs.max = definition.max;
      if (definition.type === 'integer') attrs.step = '1';
      if (definition.type === 'number') attrs.step = 'any';
      field(f, definition.name, definition.label, value, attrs,
        definition.choices ? [['','지정하지 않음'], ...definition.choices.map(v => [v,v])] : null);
    }
    finish(f, '모델 설정 저장');
  },
  probe(m, role) {
    if (busy) return;
    const requestId = crypto.randomUUID();
    const f = form(`${roles[role]} 역할 시험`, async f => {
      const result = await submit(`models/${m.id}/probes`, {role, request_id:requestId,
        expected_version:m.config_version}, '역할 시험을 시작했습니다. 진행 조회 또는 명시적 취소를 사용할 수 있습니다.');
      if (result) f.querySelector('button[type=submit]').disabled = true;
    });
    const contracts = {student:'일반 텍스트와 스트리밍', mentor:'수동 코칭과 자동 미개입을 각각 단일 구조화 응답으로 확인', analysis:'분류 JSON과 종합 결과 JSON'};
    f.append(node('p', `${roles[role]} · ${contracts[role]} · 최대 2회 생성 호출 · 각 호출 최대 출력 ${m.probe_budgets[role]} 토큰 · 자동 재시도 0회`),
      node('p', '유료 비용이 발생할 수 있습니다. 고정 합성 입력을 사용하며 시험 성공은 교육적 품질 보증이 아닙니다. 첫 단계 실패 시 다음 호출은 하지 않습니다.'),
      node('p', '관리자당 시험은 한 묶음만 진행할 수 있습니다. 한도 부족 시 대기하지 않으므로 잠시 후 다시 시도하세요. 페이지를 닫아도 시험은 재시작하거나 중단되지 않습니다. 진행 조회와 시험 취소를 사용하세요.'));
    finish(f, '시험 시작');
  },
  async poll(requestId) {
    if (busy) return;
    try {
      const result = await request(`probes/${encodeURIComponent(requestId)}`);
      await load();
      const labels = {verifying:'검증 중', succeeded:'성공', failed:'실패', stale:'재검증 필요'};
      status.textContent = `시험 상태: ${labels[result.status] || '중단됨'}`;
    } catch (code) { showError(code); }
  },
  cancel(requestId) {
    submit(`probes/${encodeURIComponent(requestId)}/cancel`, {}, '중단을 요청했습니다. 제공자 처리 및 이미 발생한 비용은 취소되지 않을 수 있습니다.');
  },
  settings(state) {
    if (busy) return;
    const settings = state.settings;
    const f = form('작성 기본값·호출 설정', f => {
      const defaults = {}, limits = {}, timeouts = {};
      for (const role of Object.keys(roles)) defaults[role] = f.elements[`default_${role}`].value === '' ? null : Number(f.elements[`default_${role}`].value);
      for (const key of Object.keys(settings.limits)) limits[key] = Number(f.elements[`limit_${key}`].value);
      for (const key of Object.keys(settings.timeouts)) timeouts[key] = Number(f.elements[`timeout_${key}`].value);
      if (limits.admin >= limits.total || timeouts.student_first_output > timeouts.student_total || timeouts.mentor_first_output > timeouts.mentor_total) {
        error.hidden = false;
        error.textContent = '관리자 한도는 전체 한도보다 작아야 하고 첫 본문 제한은 전체 시간 제한 이하여야 합니다.';
        error.focus();
        return;
      }
      submit('settings/update', {expected_version:settings.settings_version, defaults, limits, timeouts});
    });
    f.append(node('p', '새 시나리오 작성용 기본값입니다. 기존 시나리오·세션은 변경하지 않습니다. 사용 불가 기본값을 자동으로 대체하지 않습니다. 한도·시간 변경은 이후 시작 호출에 적용합니다.'));
    for (const [role, label] of Object.entries(roles)) {
      const current = settings.defaults[role];
      const choices = [['', '기본값 없음']];
      for (const m of state.models) {
        if (m.id === current?.model_config_id && !current.available) continue;
        const p = state.providers.find(p => p.provider === m.provider);
        if (p.enabled && p.status === 'ready' && state.master_key_available && m.enabled && m.capabilities && !m.capabilities.metadata_conflict && m.verification_state[role].status === 'succeeded') choices.push([m.id, `${providers[m.provider]} / ${m.display_name}`]);
      }
      const unavailable = current && !choices.some(([id]) => id === current.model_config_id);
      if (unavailable) choices.push([current.model_config_id, `기본 모델 사용 불가 (${current.model_config_id})`]);
      const select = field(f, `default_${role}`, `${label} 작성 기본 모델`, current?.model_config_id, {}, choices);
      if (unavailable) select.lastElementChild.disabled = true;
    }
    const limitLabels = {total:'전체 호출 한도', openai:'OpenAI 호출 한도', anthropic:'Claude 호출 한도', google:'Gemini 호출 한도', admin:'관리자 작업 한도'};
    const limitGroup = node('fieldset');
    limitGroup.append(node('legend', '동시 호출 한도'));
    for (const [key, label] of Object.entries(limitLabels)) field(limitGroup, `limit_${key}`, label, settings.limits[key], {type:'number', required:'', min:key === 'admin' ? 1 : 2, ...(key === 'admin' ? {max:3} : {}), step:1});
    f.append(limitGroup);
    const timeoutLabels = {connect:'연결 제한(초)', student_first_output:'학생봇 첫 본문 제한(초)', student_total:'학생봇 전체 제한(초)', mentor_first_output:'멘토 첫 본문 제한(초)', mentor_total:'멘토 전체 제한(초)', analysis_total:'사후 분석 전체 제한(초)', model_list_total:'목록 전체 제한(초)'};
    const timeoutGroup = node('fieldset');
    timeoutGroup.append(node('legend', '시간 제한'));
    for (const [key, label] of Object.entries(timeoutLabels)) field(timeoutGroup, `timeout_${key}`, label, settings.timeouts[key], {type:'number', required:'', min:1, step:1});
    f.append(timeoutGroup);
    finish(f, '설정 저장');
  }
};
document.getElementById('ai-refresh').addEventListener('click', () => { if (!busy) load(); });
editor.addEventListener('keydown', event => { if (event.key === 'Escape') closeEditor(); });
window.addEventListener('pagehide', () => editor.querySelectorAll('input[type=password]').forEach(input => { input.value = ''; }));
load();
