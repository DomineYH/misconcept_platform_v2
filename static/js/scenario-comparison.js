import {node, button, field, roles, providers} from './ai-render.js';
import {mountComparisonHistory} from './comparison-history.js';
import {readSSE} from './student-sse.js';
import {active, renderComparisonResult} from './comparison-results.js';
import {identity, mountScenarioModel} from './scenario-models.js';

export function mountComparison(data, draft) {
  const open = document.getElementById('open-comparison');
  const root = document.getElementById('scenario-comparison');
  const heading = node('h2', '동일 맥락 모델 비교', {tabindex:'-1'});
  const source = node('p');
  const close = button('비교 닫기', () => {
    root.hidden = true;
    open.setAttribute('aria-expanded', 'false');
    open.focus();
  });
  const setup = node('fieldset');
  setup.append(node('legend', '공통 입력'));
  const target = field(setup, 'comparison-target', '비교 대상', 'student', {}, Object.entries(roles));
  const note = node('p');
  const history = node('div', null, {id:'comparison-history-input'});
  const pairs = [];
  let panels = [], nextPanel = 0, nextPair = 0, run = null, submitting = false;
  const notice = node('p', null, {role:'status', tabindex:'-1'});
  const errors = node('div', null, {role:'alert', tabindex:'-1'});
  const total = node('p', null, {id:'comparison-total'});
  const editorLocks = new Map();
  let reset, previousRole = 'student';
  const rolePanels = new Map();
  function textarea(parent, id, label) {
    const input = node('textarea', null, {id, rows:3, maxlength:50000});
    parent.append(node('label', label, {for:id}), input);
    return input;
  }
  function addPair(teacher = '', student = '') {
    const row = node('fieldset');
    const index = ++nextPair;
    row.append(node('legend', `이력 ${index}`));
    const t = textarea(row, `comparison-teacher-${index}`, `이력 ${index} 교사`);
    const s = textarea(row, `comparison-student-${index}`, `이력 ${index} 학생`);
    t.value = teacher;
    s.value = student;
    const pair = {row, teacher:t, student:s};
    pairs.push(pair);
    row.append(button(`이력 ${index} 삭제`, () => {
      pairs.splice(pairs.indexOf(pair), 1);
      row.remove();
      addHistory.focus();
    }));
    history.append(row);
    t.focus();
  }
  const addHistory = button('완료 쌍 추가', () => addPair());
  const questionLabel = node('label', '학생 현재 질문', {for:'comparison-question'});
  const question = node('textarea', null, {id:'comparison-question', rows:3, maxlength:50000});
  setup.append(note, history, addHistory, questionLabel, question);
  const panelNote = node('p', null, {role:'status'});
  const tabs = node('div', null, {class:'comparison-tabs', role:'tablist', 'aria-label':'비교 패널'});
  let selectedPanel = null;
  const grid = node('div', null, {class:'comparison-grid'});
  const addPanel = button('패널 추가', () => addModel(null));
  function available(model) {
    return model.enabled && model.connection_available && model.capabilities && !model.capabilities.metadata_conflict && model.verification_state[target.value]?.status === 'succeeded';
  }
  function syncPanels() {
    panelNote.textContent = panels.length === 0 ? '미설정 · AI 연결·모델에서 대상 역할을 검증하세요.' : panels.length === 1 ? '단독 테스트' : `${panels.length}패널 · 동일 맥락 비교`;
    addPanel.disabled = panels.length >= 3;
    grid.style.setProperty('--comparison-panels', Math.max(1, panels.length));
    renderTabs();
    sync();
  }
  function selectTab(id, focus = false) {
    selectedPanel = id;
    for (const panel of panels) {
      panel.card.dataset.mobileHidden = String(panel.id !== id);
      const tab = tabs.querySelector(`[data-tab="${panel.id}"]`);
      tab.setAttribute('aria-selected', String(panel.id === id));
      tab.tabIndex = panel.id === id ? 0 : -1;
      if (focus && panel.id === id) tab.focus();
    }
  }
  function renderTabs() {
    tabs.replaceChildren();
    for (const panel of panels) {
      const tab = button(`패널 ${panel.id}`, () => selectTab(panel.id));
      tab.setAttribute('role', 'tab');
      tab.setAttribute('aria-controls', panel.card.id);
      tab.dataset.tab = panel.id;
      tab.addEventListener('keydown', event => {
        const index = panels.indexOf(panel);
        const offsets = {ArrowRight:1, ArrowLeft:-1, Home:-index, End:panels.length-1-index};
        if (!(event.key in offsets)) return;
        event.preventDefault();
        selectTab(panels[(index + offsets[event.key] + panels.length) % panels.length].id, true);
      });
      tabs.append(tab);
    }
    if (panels.length) selectTab(panels.some(p => p.id === selectedPanel) ? selectedPanel : panels[0].id);
  }
  function addModel(saved) {
    if (panels.length >= 3) return;
    const id = ++nextPanel;
    const card = node('article', null, {'aria-label':`패널 ${id}`, 'data-comparison-panel':id, id:`comparison-panel-${id}`});
    const settings = node('fieldset');
    settings.append(node('legend', `패널 ${id} 모델·옵션`));
    const select = node('select', null, {id:`comparison-model-${id}`});
    const options = node('div');
    const status = node('p', null, {role:'status'});
    settings.append(node('label', `패널 ${id} 제공자 / 모델`, {for:select.id}), select, status, options);
    const output = node('div');
    output.append(node('p', '대기', {role:'status'}));
    const panel = {id:String(id), card, settings, output};
    panel.control = mountScenarioModel(data, target.value, `패널 ${id}`, {select, options, status}, saved, () => {});
    settings.append(button(`패널 ${id} 제거`, () => {
      panels = panels.filter(p => p !== panel);
      card.remove();
      syncPanels();
      addPanel.focus();
    }));
    card.append(settings, output);
    panels.push(panel);
    grid.append(card);
    syncPanels();
  }
  function defaults() {
    panels = [];
    nextPanel = 0;
    grid.replaceChildren();
    if (rolePanels.has(target.value)) {
      for (const selection of rolePanels.get(target.value)) addModel(selection);
      syncPanels();
      return;
    }
    const selected = draft().config[target.value].resolved_model_config;
    const candidates = data.model_choices.filter(available).sort((a,b) => a.display_name.localeCompare(b.display_name) || a.id - b.id);
    const preferred = candidates.find(m => m.id === selected?.model_config_id);
    const readyProviders = Object.keys(providers).filter(provider => candidates.some(m => m.provider === provider));
    if (preferred) readyProviders.sort((a,b) => (b === preferred.provider) - (a === preferred.provider));
    for (const provider of readyProviders.slice(0,3)) {
      const model = preferred?.provider === provider ? preferred : candidates.find(m => m.provider === provider);
      addModel(model === preferred ? selected : identity(model));
    }
    syncPanels();
  }
  function roleNote() {
    question.hidden = questionLabel.hidden = target.value === 'mentor';
    questionLabel.textContent = target.value === 'analysis' ? '마지막 교사 메시지 (분석, 선택)' : '학생 현재 질문';
    const mode = draft().config.mentor.mode;
    note.textContent = '기준 대화 이력 전체를 사용합니다. 수업의 최근 N턴 제한·자동 절삭·분할은 적용하지 않습니다. ' + (target.value === 'mentor' ? mode === 'off' ? '멘토 off · 편집 폼에서 켠 뒤 실행하세요.' : mode === 'auto' ? '개입 판단 시험: 수업의 시작 턴·간격·횟수 제한은 적용하지 않음' : '마지막 완료 쌍에 수동 코칭을 요청합니다.' : target.value === 'analysis' ? '대화 전체를 단일 호출로 분석합니다.' : '현재 질문에 대한 학생 응답을 비교합니다.');
  }
  target.addEventListener('change', () => {
    rolePanels.set(previousRole, panels.map(p => structuredClone(p.control.selection)));
    previousRole = target.value;
    run = null;
    total.textContent = '';
    defaults();
    roleNote();
  });
  const start = button('비교 실행', () => execute());
  const stop = button('전체 중지', () => stopRun());
  const refresh = button('상태 새로고침', () => refreshRun());
  root.append(heading, source, close, notice, errors, setup, panelNote, addPanel, start, stop, refresh, total, tabs, grid);
  function locked() { return submitting || run?.panels.some(p => active(p.status)); }
  function sync() {
    const running = locked();
    setup.disabled = running;
    if (reset) reset.disabled = running;
    start.disabled = running || panels.length === 0;
    addPanel.disabled = running || panels.length >= 3;
    stop.disabled = !run?.panels.some(p => active(p.status));
    refresh.disabled = !run || submitting;
    for (const panel of panels) panel.settings.disabled = running;
    for (const input of document.querySelectorAll('#scenario-form input, #scenario-form textarea, #scenario-form select, #scenario-form button')) {
      if (running) {
        if (!editorLocks.has(input)) editorLocks.set(input, input.disabled);
        input.disabled = true;
      } else if (editorLocks.has(input)) {
        input.disabled = editorLocks.get(input);
        editorLocks.delete(input);
      }
    }
    if (!run) return;
    let cost = 0, unknown = false;
    for (const state of run.panels) {
      for (const attempt of [...(state.attempts || []), state]) {
        if (attempt.usage?.cost == null) unknown = true;
        else cost += attempt.usage.cost;
      }
      const panel = panels.find(p => p.id === state.panel_id);
      if (!panel) continue;
      renderComparisonResult(panel.output, state, run.target_role, {
        stop:() => stopRun(state), retry:() => execute(state),
        adopt:() => adopt(state), apply:() => apply(state),
      }, running);
      panel.output.append(node('p', `공통 입력 hash: ${run.context_hash || 'unknown'}`));
    }
    total.textContent = `${unknown ? '부분 합계 (미확인 비용 포함)' : '비용 합계'}: ${cost}`;
  }
  function commonHistory() {
    return pairs.flatMap(pair => [{role:'teacher', content:pair.teacher.value}, {role:'student', content:pair.student.value}]);
  }
  function adopt(state) {
    if (locked() || run.target_role !== 'student' || state.status !== 'completed') return;
    pairs.splice(0);
    history.replaceChildren();
    nextPair = 0;
    for (let i = 0; i < run.canonical_context.length; i += 2) addPair(run.canonical_context[i].content, run.canonical_context[i+1].content);
    addPair(run.current_message, state.text);
    question.value = '';
    notice.textContent = '다음 비교의 기준 대화 이력을 준비했습니다. 원본 비교는 유지됩니다.';
  }
  function apply(state, comparison = run) {
    if (locked()) return;
    const model = data.model_choices.find(m => m.id === state.model_config.model_config_id);
    if (!model?.enabled || !model.connection_available || model.verification_state[comparison.target_role]?.status !== 'succeeded') {
      notice.textContent = '현재 역할 검증·연결을 확인한 뒤 적용하세요.';
      return;
    }
    const select = document.getElementById(`${comparison.target_role}.resolved_model_config`);
    select.dispatchEvent(new CustomEvent('model:apply', {detail:state.model_config}));
    document.querySelector(`[data-step="${{student:2,mentor:3,analysis:4}[comparison.target_role]}"]`).click();
    select.focus();
    notice.textContent = '대상 역할의 모델·옵션을 미저장 폼에 적용했습니다. 별도 저장 또는 게시가 필요합니다.';
  }
  async function request(url, method = 'GET', body) {
    return fetch(url, {method, credentials:'same-origin', headers:{'Content-Type':'application/json',
      'x-csrf-token':document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/)?.[1] || ''}, body:body == null ? undefined : JSON.stringify(body)});
  }
  function showError(response, result) {
    errors.replaceChildren();
    const messages = {404:'비교가 만료되었거나 삭제되었습니다. 보존기간은 생성 후 30일입니다.',
      409:'진행 중 비교 또는 변경된 시도가 있습니다. 상태를 새로 확인하세요.', 429:'전체 패널 슬롯을 확보하지 못했습니다. 잠시 후 다시 실행하세요.',
      503:'AI 연결·모델 설정을 확인하세요.', 422:'공통 입력·모델 옵션을 확인하세요.', 413:'입력이 너무 큽니다.', 401:'로그인이 필요합니다.', 403:'권한 또는 보안 토큰을 확인하세요.'};
    errors.append(node('p', messages[response.status] || '비교 요청을 처리하지 못했습니다. 입력을 보존했습니다.'));
    for (const error of result?.detail?.errors || []) {
      const panel = panels.find(p => p.id === error.panel_id);
      const input = error.field === 'current_message' ? question : error.field === 'history' ? addHistory : panel?.settings.querySelector('select');
      if (input) input.setAttribute('aria-invalid', 'true');
      errors.append(button(error.message || '입력을 확인하세요.', () => input?.focus()));
    }
    errors.focus();
  }
  function applySnapshot(snapshot) {
    run = snapshot;
    for (const state of run.panels) state.lastSeq = state.seq ?? -1;
    sync();
  }
  function event(type, value) {
    const state = run?.panels.find(p => p.panel_id === value.panel_id);
    if (!state || (run.comparison_run_id && value.comparison_run_id !== run.comparison_run_id)) return;
    if (type === 'run.accepted') {
      if (state.run_id || !active(state.status)) return;
      state.run_id = value.run_id;
      run.comparison_run_id = value.comparison_run_id;
      run.context_hash = value.context_hash ?? run.context_hash;
      for (const key of ['source_snapshot', 'canonical_context', 'current_message_id', 'created_at', 'expires_at']) {
        if (value[key] !== undefined) run[key] = value[key];
      }
      if (value.model_config) state.model_config = value.model_config;
    }
    if (value.run_id !== state.run_id || !active(state.status) || value.seq <= (state.lastSeq ?? -1)) return;
    state.lastSeq = value.seq;
    if (type === 'run.accepted') Object.assign(state, {status:'running'});
    else if (type === 'output.delta' && run.target_role === 'student') { state.text += value.text || ''; state.status = 'streaming'; }
    else if (type === 'output.completed') Object.assign(state, value, {status:value.outcome_status || 'completed'});
    else if (['run.failed','run.cancelled','run.interrupted'].includes(type)) Object.assign(state, value, {status:type.slice(4)});
    sync();
  }
  async function consume(response) {
    if (response.headers.get('Content-Type')?.includes('application/json')) applySnapshot(await response.json());
    else {
      const snapshot = run;
      await readSSE(response.body, (type, value) => {
        if (run !== snapshot) return false;
        event(type, value);
      });
    }
  }
  async function execute(retry = null) {
    if (locked()) return;
    const sourceDraft = draft();
    if (!retry && (target.value === 'student' && !question.value.trim() || target.value === 'mentor' && (pairs.length === 0 || sourceDraft.config.mentor.mode === 'off') || target.value === 'analysis' && pairs.length === 0 && !question.value.trim())) {
      errors.replaceChildren(node('p', target.value === 'mentor' ? '멘토를 켜고 완료 쌍을 추가하세요.' : '교사 메시지를 입력하세요.'));
      errors.focus();
      return;
    }
    const prior = run;
    const body = retry ? {request_id:crypto.randomUUID(), generation_id:retry.run_id} : {
      request_id:crypto.randomUUID(), target_role:target.value,
      source_snapshot:{...sourceDraft, scenario_id:data.id, source_version:data.id == null ? null : data.config_version, source_kind:'unsaved_draft'},
      canonical_context:commonHistory(), current_message:target.value === 'mentor' ? null : question.value,
      panels:panels.map(p => ({panel_id:p.id, model_config:structuredClone(p.control.selection)})),
      parent_run_id:run?.comparison_run_id || null,
    };
    submitting = true;
    errors.replaceChildren();
    root.querySelectorAll('[aria-invalid]').forEach(input => input.removeAttribute('aria-invalid'));
    notice.textContent = '전체 패널의 공통 입력을 확인 중입니다.';
    sync();
    try {
      const url = retry ? `/admin/comparisons/${run.comparison_run_id}/panels/${retry.panel_id}/retry` : '/admin/comparisons';
      const response = await request(url, 'POST', body);
      if (!response.ok) { showError(response, await response.json().catch(() => null)); return; }
      if (retry) {
        retry.attempts = [...(retry.attempts || []), structuredClone({...retry, attempts:[]})];
        Object.assign(retry, {run_id:null, status:'waiting', lastSeq:-1, text:''});
      } else run = {...body, comparison_run_id:null, panels:body.panels.map(p => ({...p, status:'waiting', text:'', lastSeq:-1}))};
      submitting = false;
      sync();
      await consume(response);
      notice.textContent = locked() ? '진행 중' : '비교가 종료되었습니다. 패널별 결과를 확인하세요.';
      if (locked()) notice.textContent = '진행 중 · 상태 새로고침 또는 중지를 사용할 수 있습니다.';
    } catch {
      if (!run?.comparison_run_id) run = prior;
      notice.textContent = '연결이 중단되었습니다. 상태 새로고침으로 저장된 결과를 확인하세요. 입력을 보존했습니다.';
    } finally { submitting = false; sync(); }
  }
  async function refreshRun() {
    if (!run?.comparison_run_id || submitting) return;
    try {
      const response = await request(`/admin/comparisons/${run.comparison_run_id}`);
      if (response.ok) applySnapshot(await response.json());
      else showError(response, null);
    } catch { notice.textContent = '상태를 확인하지 못했습니다. 다시 확인하세요.'; }
  }
  async function stopRun(state = null) {
    if (!run?.comparison_run_id) return;
    try {
      const url = `/admin/comparisons/${run.comparison_run_id}${state ? `/panels/${state.panel_id}` : ''}/stop`;
      const body = state ? {generation_id:state.run_id} : {generations:run.panels.filter(p => active(p.status)).map(p => ({panel_id:p.panel_id,generation_id:p.run_id}))};
      const response = await request(url, 'POST', body);
      if (response.ok) applySnapshot(await response.json());
      else showError(response, null);
    } catch { notice.textContent = '중지 상태를 확인하지 못했습니다. 상태를 새로 확인하세요.'; }
  }
  reset = button('비교 입력 초기화', () => {
    if (locked()) return;
    run = null;
    pairs.splice(0);
    history.replaceChildren();
    nextPair = 0;
    question.value = '';
    rolePanels.clear();
    target.value = previousRole = 'student';
    errors.replaceChildren();
    total.textContent = '';
    defaults();
    roleNote();
    notice.textContent = '편집 중 비교 입력을 초기화했습니다. 저장된 비교 기록은 유지됩니다.';
    target.focus();
  });
  root.append(reset);
  mountComparisonHistory(root, {request, apply, sourceId:() => data.id, invalidate:id => {
    if (run?.comparison_run_id !== id) return;
    run = null;
    for (const panel of panels) panel.output.replaceChildren();
    total.textContent = '';
    sync();
  }});
  sync();
  let initialized = false;
  open.addEventListener('click', () => {
    source.textContent = `${draft().title || '제목 없는 초안'} · 미저장 초안 · ${data.id == null ? '새 작성 · 원본 없음' : `원본 #${data.id} · 버전 ${data.config_version}`}`;
    if (!initialized) { defaults(); initialized = true; }
    roleNote();
    root.hidden = false;
    open.setAttribute('aria-expanded', 'true');
    heading.focus();
  });
  if (new URLSearchParams(window.location.search).get('compare') === '1') open.click();
}
