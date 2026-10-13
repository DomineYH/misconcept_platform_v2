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
let state = data.snapshot, busy = false, selected = [], recovery = null;
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
  if (!response.ok) throw new Error('비교 기록을 조회할 수 없습니다.');
  state = await response.json();
  render();
}
function errorMessage(response, detail) {
  const code = detail?.code;
  return ({version_conflict:'다른 요청이 먼저 처리되었습니다. 상태를 새로고침하세요.',
    comparison_busy:'실행 중인 비교가 있습니다.', adoption_or_auto_required:'완료 응답 채택 또는 자동 검사를 먼저 처리하세요.',
    configuration_unavailable:'모델·연결·역할 검증을 확인하세요.', new_comparison_required:'실행 계약이 변경되었습니다. 새 비교를 시작하세요.',
    context_limit:'모든 패널이 공통 입력을 처리할 수 있도록 입력 한도를 확인하세요.',
    stale_generation:'시도가 변경되었습니다. 상태를 새로고침하세요.', call_limit_reached:'동시 호출 한도에 도달했습니다.'})[code]
    || (response.status === 404 ? '비교가 만료되었거나 삭제되었습니다.' : '요청을 처리하지 못했습니다. 입력을 보존했습니다.');
}
async function action(name, values = {}, role = null, retry = null) {
  if (busy && name !== 'stop') return;
  const stopping = name === 'stop';
  let accepted = false;
  if (!stopping) busy = true;
  const requestId = crypto.randomUUID();
  const url = retry ? `${path()}/children/${retry.child}/panels/${retry.panel}/retry` : `${path()}/${name}`;
  render();
  try {
    const response = await request(url, 'POST', {request_id:requestId, expected_version:state.version, ...values});
    if (!response.ok) {
      const payload = await response.json();
      throw new Error(errorMessage(response, payload.detail));
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
    if (name === 'turn') question.value = '';
    await refresh();
    notice.textContent = '요청 결과를 저장했습니다.';
    accepted = true;
  } catch (error) {
    notice.textContent = error.message || '연결이 끊겼습니다. 저장 상태를 확인하세요.';
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
      if (!response.ok) break;
      state = await response.json();
      render();
      if (!running()) return;
      await new Promise(resolve => setTimeout(resolve, 1000));
    } while (Date.now() < deadline);
  } catch { /* Stored state remains available for an explicit GET. */ }
  finally { clearTimeout(timeout); recovery = null; }
  notice.textContent = '복구 조회가 끝났습니다. 상태 새로고침 또는 중지를 사용하세요.';
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
  setup.hidden = true;
  document.getElementById('conversation-actions').hidden = false;
  dialogue.replaceChildren(node('h2', state.source_snapshot.title),
    node('p', `버전 ${state.version} · ${state.phase} · 만료 ${state.expires_at}`));
  for (const pair of state.pairs) {
    const row = node('article', null, {'data-pair':pair.turn_id});
    row.append(node('p', `교사: ${pair.teacher}`, {class:'comparison-text'}), node('p', `학생: ${pair.student}`, {class:'comparison-text'}));
    dialogue.append(row);
  }
  const allowed = state.allowed_actions || {};
  const target = state.pairs.at(-1)?.turn_id;
  controls.replaceChildren(button('질문 보내기', () => action('turn', {message:question.value}, 'student'), busy || !allowed.turn),
    button('멘토 도움', () => action('mentor', {target_turn_id:target, trigger:'manual'}, 'mentor'), busy || !allowed.mentor),
    button('대기 중 자동 검사', () => action('mentor', {target_turn_id:target, trigger:'auto'}, 'mentor'), busy || !state.pending_auto || running()),
    button('비교 종료 및 분석', () => action('end', {}, 'analysis'), busy || !allowed.end),
    button('상태 새로고침', () => refresh().catch(error => { notice.textContent = error.message; })));
  question.disabled = busy || !allowed.turn;
  results.replaceChildren();
  for (const child of state.children) {
    const section = node('section', null, {'aria-label':`${roles[child.target_role]} 결과`});
    section.append(node('h2', `${roles[child.target_role]} 결과`));
    const grid = node('div', null, {class:'conversation-panels'});
    for (const panel of child.panels) {
      const card = node('article', null, {'aria-label':`패널 ${panel.panel_id}`});
      grid.append(card);
      const pending = state.pending_student_child_id === child.comparison_run_id;
      const currentMentor = state.mentor_actions.some(entry => entry.child_id === child.comparison_run_id && entry.target_turn_id === target);
      const retryable = pending || currentMentor || state.final_analysis_child_id === child.comparison_run_id;
      renderComparisonResult(card, panel, child.target_role, {
        stop:() => action('stop', {child_id:child.comparison_run_id, generations:[{panel_id:panel.panel_id, generation_id:panel.run_id}]}),
        retry:() => action('retry', {generation_id:panel.run_id}, child.target_role, {child:child.comparison_run_id, panel:panel.panel_id}),
        adopt:() => adopt(child, panel), apply:() => apply(panel)
      }, busy || running());
      const buttons = [...card.querySelectorAll(':scope > button')];
      for (const button of buttons) {
        if (button.textContent === '학생 응답 채택') button.disabled ||= !pending || !allowed.adopt;
        if (button.textContent === '패널 재시도') button.disabled ||= !retryable;
      }
    }
    section.append(grid);
    results.append(section);
  }
  for (const action of state.mentor_actions) {
    results.append(node('p', `멘토 ${action.position}턴 · ${action.trigger}: ${Object.entries(action.panel_decisions).map(([panel,decision]) => `패널 ${panel} ${decision}`).join(' · ')}`));
  }
  const activeChild = state.children.find(child => child.panels.some(panel => panel.status === 'running'));
  if (activeChild) controls.append(button('전체 중지', () => action('stop', {child_id:activeChild.comparison_run_id,
    generations:activeChild.panels.filter(panel => panel.status === 'running').map(panel => ({panel_id:panel.panel_id, generation_id:panel.run_id}))})));
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
  if (!response.ok) return;
  const history = await response.json();
  const root = document.getElementById('conversation-history');
  root.replaceChildren(node('h2', '전체 비교 이력'));
  for (const item of history.items) {
    const row = node('p');
    row.append(node('a', `${item.title} · ${item.created_at} · ${item.status}`, {href:`/admin/model-comparison?conversation_id=${item.conversation_id}`}),
      button('비교 기록 삭제', async () => {
        if (!confirm('전체 비교 기록을 삭제하시겠습니까? 비용 원장은 보존됩니다.')) return;
        const response = await request(`${base}/${item.conversation_id}`, 'DELETE');
        if (!response.ok) { notice.textContent = '실행 중인 기록은 삭제할 수 없습니다.'; return; }
        if (state?.conversation_id === item.conversation_id) location.href = '/admin/model-comparison';
        else await loadHistory();
      }));
    root.append(row);
  }
  if (history.next_cursor) root.append(button('다음 20개', () => loadHistory(history.next_cursor)));
}
mountComparisonHistory(document.getElementById('legacy-comparison-history'), {request, apply:(_panel, comparison) => { location.href = `/admin/scenarios/new?from_comparison=${comparison.comparison_run_id}`; }, invalidate:() => {}, sourceId:() => null});
mountSetup();
if (state) { render(); if (running()) recover(); }
loadHistory().catch(() => { notice.textContent = '비교 이력을 불러오지 못했습니다.'; });
