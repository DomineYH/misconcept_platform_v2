import {node, button, field, roles, providers} from './ai-render.js';
import {identity, mountScenarioModel, commonModelReason} from './scenario-models.js';
import {readSSE} from './student-sse.js';
import {renderComparisonResult} from './comparison-results.js';
import {mountComparisonHistory} from './comparison-history.js';

const data = JSON.parse(document.getElementById('comparison-conversation-data').textContent);
const notice = document.getElementById('conversation-status');
const setup = document.getElementById('conversation-setup');
const dialogue = document.getElementById('conversation-dialogue');
const results = document.getElementById('conversation-rounds');
const controls = document.getElementById('conversation-controls');
const question = document.getElementById('conversation-question');
let state = data.snapshot, busy = false, selected = [], recovery = null, unavailable = false;
const phases = {open:'대화 진행', generating:'응답 생성 중', awaiting_adoption:'학생 응답 채택 대기', retry_required:'현재 패널 재시도 필요', analyzing:'사후 분석 중', ended:'비교 종료'};
const base = '/admin/comparison-conversations';
const path = () => `${base}/${state.conversation_id}`;
const running = () => state?.children.some(child => child.panels.some(panel => panel.status === 'running'));
async function request(url, method = 'GET', body = null, signal = undefined) {
  return fetch(url, {method, credentials:'same-origin', signal, headers:{'Content-Type':'application/json',
    'x-csrf-token':document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/)?.[1] || ''},
    ...(body == null ? {} : {body:JSON.stringify(body)})});
}
function remember() {
  const url = new URL(location.href);
  url.search = '';
  url.searchParams.set('conversation_id', state.conversation_id);
  history.replaceState(null, '', url);
}
async function refresh() {
  const response = await request(path());
  if (response.status === 404) { missing(); return false; }
  if (!response.ok) throw new Error('비교 기록을 조회할 수 없습니다.');
  state = await response.json();
  unavailable = false;
  render();
  notice.textContent = '저장된 비교 상태를 확인했습니다.';
  return true;
}
function missing() {
  unavailable = true;
  notice.textContent = '비교 대화를 찾거나 열 수 없습니다. 입력은 유지됩니다. 저장된 시나리오 선택 또는 상태 새로고침을 사용하세요.';
  render();
}
function errorMessage(response, detail) {
  const code = detail?.code;
  return ({version_conflict:'다른 요청이 먼저 처리되었습니다. 상태를 새로고침하세요.',
    comparison_busy:'실행 중인 비교가 있습니다.', adoption_or_auto_required:'완료 응답 채택 또는 자동 검사를 먼저 처리하세요.',
    configuration_unavailable:'모델·연결·역할 검증을 확인하세요.', new_comparison_required:'실행 계약이 변경되었습니다. 새 비교를 시작하세요.',
    context_limit:'모든 패널이 공통 입력을 처리할 수 있도록 입력 한도를 확인하세요.',
    stale_generation:'시도가 변경되었습니다. 상태를 새로고침하세요.', call_limit_reached:'동시 호출 한도에 도달했습니다.'})[code]
    || (response.status === 404 ? '비교 대화를 찾거나 열 수 없습니다.' : '요청을 처리하지 못했습니다. 입력을 보존했습니다.');
}
async function action(name, values = {}, role = null, retry = null) {
  if (unavailable || busy && name !== 'stop') return;
  const stopping = name === 'stop';
  let accepted = false, rejection = null;
  if (!stopping) busy = true;
  const requestId = crypto.randomUUID();
  const url = retry ? `${path()}/children/${retry.child}/panels/${retry.panel}/retry` : `${path()}/${name}`;
  render();
  try {
    const response = await request(url, 'POST', {request_id:requestId, expected_version:state.version, ...values});
    if (!response.ok) {
      if (response.status === 404) { missing(); return false; }
      const payload = await response.json();
      rejection = errorMessage(response, payload.detail);
      throw new Error(rejection);
    }
    if (response.headers.get('Content-Type')?.includes('text/event-stream')) {
      let round = retry ? state.children.find(child => child.comparison_run_id === retry.child) : null;
      await readSSE(response.body, (type, value) => {
        if (!round) {
          round = {comparison_run_id:value.comparison_run_id, target_role:role, panels:[]};
          state.children.push(round);
        }
        state.version = Math.max(state.version, value.version || 0);
        let panel = round.panels.find(panel => panel.panel_id === value.panel_id);
        if (type === 'run.accepted') {
          if (!panel) { panel = {panel_id:value.panel_id, attempts:[]}; round.panels.push(panel); }
          else if (panel.run_id !== value.run_id) panel.attempts.push({...structuredClone(panel), attempts:[]});
          Object.assign(panel, value, {status:'running', text:'', lastSeq:value.seq});
        } else if (panel?.run_id === value.run_id && value.seq > (panel.lastSeq ?? -1)) {
          panel.lastSeq = value.seq;
          if (type === 'output.delta') panel.text += value.text || '';
          else Object.assign(panel, value, {status:value.outcome_status || type.replace('run.','')});
        }
        render();
      });
    } else state = await response.json();
    if (!await refresh()) return false;
    if (name === 'turn') question.value = '';
    notice.textContent = '요청 결과를 저장했습니다.';
    accepted = true;
  } catch {
    notice.textContent = rejection || '요청 결과를 확인할 수 없습니다. 입력은 유지됩니다. 상태 새로고침으로 저장된 결과를 확인하세요.';
    if (state?.conversation_id && !stopping) await recover();
  } finally {
    if (!stopping) busy = false;
    render();
  }
  return accepted;
}
async function recover() {
  if (recovery) return;
  const controller = recovery = new AbortController();
  const deadline = Date.now() + 10000;
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    do {
      const response = await request(path(), 'GET', null, controller.signal);
      if (response.status === 404) { missing(); return; }
      if (!response.ok) break;
      state = await response.json();
      unavailable = false;
      render();
      if (!running()) return;
      await new Promise(resolve => setTimeout(resolve, 1000));
    } while (Date.now() < deadline);
  } catch { /* Stored state remains available for an explicit GET. */ }
  finally { clearTimeout(timeout); recovery = null; }
  notice.textContent = '복구 조회가 끝났습니다. 요청 결과는 확인되지 않았습니다. 상태 새로고침 또는 허용된 중지를 사용하세요.';
}
async function adopt(child, panel) {
  const accepted = await action('adopt', {child_id:child.comparison_run_id, panel_id:panel.panel_id, generation_id:panel.run_id});
  if (accepted && state.pending_auto) await action('mentor', {target_turn_id:state.pairs.at(-1).turn_id, trigger:'auto'}, 'mentor');
}
async function apply(panel) {
  try {
    const response = await request(`${path()}/panels/${panel.panel_id}/model`);
    if (!response.ok) throw new Error('현재 모델·연결·역할 검증 또는 원본 시나리오를 확인하세요.');
    const handoff = await response.json();
    location.href = `/admin/scenarios/${handoff.scenario_id}/edit?conversation=${state.conversation_id}&panel=${encodeURIComponent(panel.panel_id)}`;
  } catch (error) { notice.textContent = error.message; }
}
function render() {
  if (!state) return;
  const focused = document.activeElement;
  const cardId = focused.closest?.('[data-result-panel]')?.dataset.resultPanel;
  const controlText = controls.contains(focused) || cardId ? focused.textContent : null;
  setup.hidden = true;
  document.getElementById('conversation-actions').hidden = false;
  dialogue.replaceChildren(node('h2', state.source_snapshot.title), node('h3', '기준 대화 이력'),
    node('p', `전체 상태: ${phases[state.phase] || state.phase} · 버전 ${state.version} · 만료 ${state.expires_at}`, {class:'comparison-state'}));
  if (running()) dialogue.append(node('p', '다른 패널 실행 중 · 완료 응답도 채택할 수 없습니다.'));
  else if (state.pending_auto) dialogue.append(node('p', '채택된 턴의 자동 멘토 검사가 대기 중입니다. 검사 후 다음 질문 또는 종료를 진행하세요.'));
  else if (state.phase === 'awaiting_adoption') dialogue.append(node('p', state.allowed_actions?.adopt && !unavailable
    ? '완료 응답 중 서버가 허용한 응답 하나를 채택하면 다음 기준 대화 이력이 갱신됩니다.'
    : '현재 응답 채택이 허용되지 않습니다. 상태 새로고침으로 조건을 확인하세요.'));
  else if (state.phase === 'retry_required') dialogue.append(node('p', '채택할 완료 응답이 없습니다. 현재 대상 패널의 재시도 조건을 확인하세요.'));
  for (const pair of state.pairs) {
    const row = node('article', null, {'data-pair':pair.turn_id});
    row.append(node('p', `교사: ${pair.teacher}`, {class:'comparison-text'}), node('p', `학생: ${pair.student}`, {class:'comparison-text'}));
    dialogue.append(row);
  }
  const allowed = unavailable ? {} : state.allowed_actions || {};
  const target = state.pairs.at(-1)?.turn_id;
  controls.replaceChildren(button('질문 보내기', () => action('turn', {message:question.value}, 'student'), busy || !allowed.turn),
    button('멘토 도움', () => action('mentor', {target_turn_id:target, trigger:'manual'}, 'mentor'), busy || !allowed.mentor),
    button('대기 중 자동 검사', () => action('mentor', {target_turn_id:target, trigger:'auto'}, 'mentor'), unavailable || busy || !state.pending_auto || running()),
    button('비교 종료 및 분석', () => action('end', {}, 'analysis'), busy || !allowed.end),
    button('상태 새로고침', () => refresh().catch(error => { notice.textContent = error.message; })));
  question.disabled = busy || !allowed.turn;
  results.replaceChildren();
  for (const child of state.children) {
    const section = node('section', null, {'aria-label':`${roles[child.target_role]} 결과`});
    section.append(node('h2', `${roles[child.target_role]} 결과`));
    const grid = node('div', null, {class:'conversation-panels'});
    for (const panel of child.panels) {
      const card = node('article', null, {'aria-label':`패널 ${panel.panel_id}`, 'data-result-panel':`${child.comparison_run_id}/${panel.panel_id}`});
      grid.append(card);
      const pending = state.pending_student_child_id === child.comparison_run_id;
      const currentMentor = state.mentor_actions.some(entry => entry.child_id === child.comparison_run_id && entry.target_turn_id === target);
      const retryable = pending || currentMentor || state.final_analysis_child_id === child.comparison_run_id;
      renderComparisonResult(card, panel, child.target_role, {
        stop:() => action('stop', {child_id:child.comparison_run_id, generations:[{panel_id:panel.panel_id, generation_id:panel.run_id}]}),
        retry:() => action('retry', {generation_id:panel.run_id}, child.target_role, {child:child.comparison_run_id, panel:panel.panel_id}),
        adopt:() => adopt(child, panel), apply:() => apply(panel)
      }, unavailable || busy || running());
      const buttons = [...card.querySelectorAll(':scope > button')];
      for (const button of buttons) {
        if (button.textContent === '학생 응답 채택') button.disabled ||= !pending || !allowed.adopt;
        if (button.textContent === '패널 재시도') button.disabled ||= !retryable;
        if (button.textContent === '패널 중지') button.disabled ||= unavailable;
      }
    }
    section.append(grid);
    results.append(section);
  }
  for (const action of state.mentor_actions) {
    results.append(node('p', `멘토 ${action.position}턴 · ${action.trigger}: ${Object.entries(action.panel_decisions).map(([panel,decision]) => `패널 ${panel} ${decision}`).join(' · ')}`));
  }
  const activeChild = state.children.find(child => child.panels.some(panel => panel.status === 'running'));
  if (activeChild && !unavailable) controls.append(button('전체 중지', () => action('stop', {child_id:activeChild.comparison_run_id,
    generations:activeChild.panels.filter(panel => panel.status === 'running').map(panel => ({panel_id:panel.panel_id, generation_id:panel.run_id}))})));
  if (controlText) {
    const root = cardId ? [...results.querySelectorAll('[data-result-panel]')].find(card => card.dataset.resultPanel === cardId) : controls;
    [...(root?.querySelectorAll('button') || [])].find(button => button.textContent === controlText && !button.disabled)?.focus({preventScroll:true});
  }
}
function mountSetup() {
  if (!data.source) { setup.append(node('p', '저장된 시나리오를 먼저 선택하세요.')); return; }
  const activeRoles = ['student', 'analysis', ...(data.source.config.mentor.mode === 'off' ? [] : ['mentor'])];
  setup.append(node('h2', data.source.title));
  const count = field(setup, 'panel-count', '비교 모델 수', '1', {}, [['1','1'],['2','2'],['3','3']]);
  const panels = node('div', null, {class:'conversation-panels'});
  setup.append(panels);
  const start = button('비교 시작', async () => {
    if (busy || selected.some(panel => !panel.model)) return;
    busy = true; start.disabled = true;
    try {
      const response = await request(base, 'POST', {request_id:crypto.randomUUID(), scenario_id:data.source.id,
        expected_scenario_version:data.source.config_version,
        panels:selected.map(panel => ({panel_id:panel.id, model_config_id:panel.model.id,
          role_options:Object.fromEntries(activeRoles.map(role => [role,panel.controls[role].selection.options]))}))});
      if (!response.ok) { const payload = await response.json(); throw new Error(errorMessage(response, payload.detail)); }
      state = await response.json(); remember(); render();
    } catch (error) { notice.textContent = error.message; }
    finally { busy = false; start.disabled = false; render(); }
  }, true);
  function draw() {
    selected = []; panels.replaceChildren();
    for (let i = 1; i <= Number(count.value); i++) {
      const card = node('fieldset'); card.append(node('legend', `패널 ${i}`)); panels.append(card);
      const select = field(card, `panel-${i}`, `패널 ${i} 공통 모델`, '', {}, [['','모델 선택 필요']]);
      for (const model of data.model_choices) {
        const reason = commonModelReason(model, activeRoles);
        const option = node('option', `${providers[model.provider]} / ${model.display_name}${reason ? ` · ${reason}` : ''}`, {value:model.id});
        option.disabled = Boolean(reason); select.append(option);
      }
      const panel = {id:String(i), model:null, controls:{}}; selected.push(panel);
      for (const role of activeRoles) {
        const roleSelect = node('select', null, {id:`panel-${i}-${role}`, hidden:''});
        const options = node('div'), status = node('p'); card.append(roleSelect, status, options);
        panel.controls[role] = mountScenarioModel(data, role, `패널 ${i} ${roles[role]}`, {select:roleSelect, options, status}, null, () => {});
      }
      select.addEventListener('change', () => {
        panel.model = data.model_choices.find(model => String(model.id) === select.value);
        for (const role of activeRoles) panel.controls[role].set(panel.model ? identity(panel.model) : null);
        start.disabled = selected.some(panel => !panel.model);
      });
    }
    start.disabled = true;
  }
  count.addEventListener('change', draw); draw(); setup.append(start);
}
async function loadHistory(cursor = null) {
  const response = await request(`${base}${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`);
  if (!response.ok) throw new Error('비교 이력을 불러오지 못했습니다. 다시 확인하세요.');
  const history = await response.json();
  const root = document.getElementById('conversation-history');
  root.replaceChildren(node('h2', '전체 비교 이력'));
  if (!history.items.length) root.append(node('p', '보존 중인 비교 대화가 없습니다.'));
  for (const item of history.items) {
    const row = node('p');
    row.append(node('a', `${item.title} · ${item.created_at} · ${item.status}`, {href:`/admin/model-comparison?conversation_id=${item.conversation_id}`}),
      button('비교 기록 삭제', async () => {
        if (!confirm('전체 비교 기록을 삭제하시겠습니까? 비용 원장은 보존됩니다.')) return;
        const response = await request(`${base}/${item.conversation_id}`, 'DELETE').catch(() => null);
        if (!response) { notice.textContent = '비교 기록 삭제 결과를 확인할 수 없습니다. 현재 목록은 유지됩니다.'; return; }
        if (!response.ok) { notice.textContent = '비교 기록을 삭제하지 못했습니다. 상태를 새로 확인하세요.'; return; }
        if (state?.conversation_id === item.conversation_id) location.href = '/admin/model-comparison';
        else await loadHistory().catch(historyError);
      }));
    root.append(row);
  }
  if (history.next_cursor) root.append(button('다음 20개', () => loadHistory(history.next_cursor).catch(historyError)));
}
function historyError() { notice.textContent = '비교 이력을 불러오지 못했습니다. 다시 확인하세요.'; }
mountComparisonHistory(document.getElementById('legacy-comparison-history'), {request, apply:(_panel, comparison) => { location.href = `/admin/scenarios/new?from_comparison=${comparison.comparison_run_id}`; }, invalidate:() => {}, sourceId:() => null});
mountSetup();
if (state) { render(); if (running()) recover(); }
loadHistory().catch(historyError);
