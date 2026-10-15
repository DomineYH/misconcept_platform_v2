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
const phases = {open:uiLanguage.t('admin.conversation_open'), generating:uiLanguage.t('admin.generating_responses'), awaiting_adoption:uiLanguage.t('admin.awaiting_student_response_adoption'), retry_required:uiLanguage.t('admin.current_panel_retry_required'), analyzing:uiLanguage.t('admin.analyzing'), ended:uiLanguage.t('admin.comparison_ended')};
let pendingRequests = 0, uncertainVersion = null, setupBaseline;
const setupValues = () => JSON.stringify({fields:[...setup.querySelectorAll('input,select')].map(el => [el.id,el.value]), options:selected.map(panel => Object.values(panel.controls).map(control => control.selection))});
uiLanguage.registerGuard(() => ({busy:Boolean(busy || pendingRequests || recovery || running() || uncertainVersion !== null || state?.pending_auto), dirty:Boolean(question.value || !state && setupValues() !== setupBaseline)}));
const base = '/admin/comparison-conversations';
const path = () => `${base}/${state.conversation_id}`;
const running = () => state?.children.some(child => child.panels.some(panel => panel.status === 'running'));
async function request(url, method = 'GET', body = null, signal = undefined) {
  return fetch(url, {method, credentials:'same-origin', signal, headers:uiLanguage.headers(url, {'Content-Type':'application/json',
    'x-csrf-token':document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/)?.[1] || ''}),
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
  if (!response.ok) throw new Error(uiLanguage.t('admin.could_not_retrieve_comparison_records'));
  state = await response.json();
  if (uncertainVersion !== null && state.version > uncertainVersion) uncertainVersion = null;
  unavailable = false;
  render();
  notice.textContent = uiLanguage.t('admin.saved_comparison_state_confirmed');
  return true;
}
function missing() {
  unavailable = true;
  notice.textContent = uiLanguage.t('admin.could_not_find_or_open_this_comparison_conversation_your_input_is_preserved_choose_a_saved_scenario_');
  render();
}
function errorMessage(response, detail) {
  const code = detail?.code;
  return ({version_conflict:uiLanguage.t('admin.another_request_was_processed_first_refresh_the_state'),
    comparison_busy:uiLanguage.t('admin.a_comparison_is_running'), adoption_or_auto_required:uiLanguage.t('admin.adopt_a_completed_response_or_handle_the_automatic_check_first'),
    configuration_unavailable:uiLanguage.t('admin.check_the_model_connection_and_role_verification'), new_comparison_required:uiLanguage.t('admin.the_execution_contract_changed_start_a_new_comparison'),
    context_limit:uiLanguage.t('admin.check_the_input_limits_so_all_panels_can_process_the_common_input'),
    stale_generation:uiLanguage.t('admin.the_attempt_changed_refresh_the_state'), call_limit_reached:uiLanguage.t('admin.the_concurrent_call_limit_was_reached')})[code]
    || (response.status === 404 ? uiLanguage.t('admin.could_not_find_or_open_this_comparison_conversation') : uiLanguage.t('admin.could_not_process_the_request_your_input_is_preserved'));
}
async function action(name, values = {}, role = null, retry = null) {
  if (unavailable || busy && name !== 'stop') return;
  const stopping = name === 'stop';
  const submittedVersion = state.version;
  let accepted = false, rejection = null;
  if (!stopping) busy = true;
  const requestId = crypto.randomUUID();
  const url = retry ? `${path()}/children/${retry.child}/panels/${retry.panel}/retry` : `${path()}/${name}`;
  render();
  pendingRequests++; try {
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
    notice.textContent = uiLanguage.t('admin.request_results_saved');
    accepted = true;
  } catch {
    // The old version cannot prove that an unacknowledged mutation has settled.
    if (!rejection && !stopping) uncertainVersion = Math.max(uncertainVersion ?? 0, submittedVersion);
    notice.textContent = rejection || uiLanguage.t('admin.could_not_confirm_the_request_result_your_input_is_preserved_refresh_the_state_to_check_saved_result');
    if (state?.conversation_id && !stopping) await recover();
  } finally { pendingRequests--;
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
  pendingRequests++; try {
    do {
      const response = await request(path(), 'GET', null, controller.signal);
      if (response.status === 404) { missing(); return; }
      if (!response.ok) break;
      state = await response.json();
      if (uncertainVersion !== null && state.version > uncertainVersion) uncertainVersion = null;
      unavailable = false;
      render();
      if (!running()) return;
      await new Promise(resolve => setTimeout(resolve, 1000));
    } while (Date.now() < deadline);
  } catch { /* Stored state remains available for an explicit GET. */ }
  finally { pendingRequests--; clearTimeout(timeout); recovery = null; }
  notice.textContent = uiLanguage.t('admin.recovery_lookup_ended_without_confirming_the_request_result_refresh_the_state_or_use_an_allowed_stop');
}
async function adopt(child, panel) {
  const accepted = await action('adopt', {child_id:child.comparison_run_id, panel_id:panel.panel_id, generation_id:panel.run_id});
  if (accepted && state.pending_auto) await action('mentor', {target_turn_id:state.pairs.at(-1).turn_id, trigger:'auto'}, 'mentor');
}
async function apply(panel) {
  pendingRequests++; try {
    const response = await request(`${path()}/panels/${panel.panel_id}/model`);
    if (!response.ok) throw new Error(uiLanguage.t('admin.check_the_current_model_connection_role_verification_or_source_scenario'));
    const handoff = await response.json();
    location.href = `/admin/scenarios/${handoff.scenario_id}/edit?conversation=${state.conversation_id}&panel=${encodeURIComponent(panel.panel_id)}`;
  } catch (error) { notice.textContent = error.message; } finally { pendingRequests--; }
}
function render() {
  if (!state) return;
  const focused = document.activeElement;
  const cardId = focused.closest?.('[data-result-panel]')?.dataset.resultPanel;
  const controlText = controls.contains(focused) || cardId ? focused.textContent : null;
  setup.hidden = true;
  document.getElementById('conversation-actions').hidden = false;
  dialogue.replaceChildren(node('h2', state.source_snapshot.title), node('h3', uiLanguage.t('admin.canonical_conversation_history')),
    node('p', `${uiLanguage.t('admin.overall_status')} ${phases[state.phase] || state.phase} · ${uiLanguage.t('admin.version')} ${state.version} · ${uiLanguage.t('admin.expires')} ${state.expires_at}`, {class:'comparison-state'}));
  if (running()) dialogue.append(node('p', uiLanguage.t('admin.other_panels_are_running_completed_responses_cannot_be_adopted_yet')));
  else if (state.pending_auto) dialogue.append(node('p', uiLanguage.t('admin.an_automatic_mentor_check_is_pending_for_the_adopted_turn_complete_it_before_the_next_question_or_en')));
  else if (state.phase === 'awaiting_adoption') dialogue.append(node('p', state.allowed_actions?.adopt && !unavailable
    ? uiLanguage.t('admin.adopt_one_eligible_completed_response_to_update_the_canonical_conversation_history')
    : uiLanguage.t('admin.response_adoption_is_currently_unavailable_refresh_the_state_to_check_eligibility')));
  else if (state.phase === 'retry_required') dialogue.append(node('p', uiLanguage.t('admin.no_completed_response_is_available_to_adopt_check_retry_eligibility_for_the_current_panels')));
  for (const pair of state.pairs) {
    const row = node('article', null, {'data-pair':pair.turn_id});
    row.append(node('p', `${uiLanguage.t('admin.teacher_prefix')} ${pair.teacher}`, {class:'comparison-text'}), node('p', `${uiLanguage.t('admin.student_prefix')} ${pair.student}`, {class:'comparison-text'}));
    dialogue.append(row);
  }
  const allowed = unavailable ? {} : state.allowed_actions || {};
  const target = state.pairs.at(-1)?.turn_id;
  controls.replaceChildren(button(uiLanguage.t('admin.send_question'), () => action('turn', {message:question.value}, 'student'), busy || !allowed.turn),
    button(uiLanguage.t('admin.mentor_help'), () => action('mentor', {target_turn_id:target, trigger:'manual'}, 'mentor'), busy || !allowed.mentor),
    button(uiLanguage.t('admin.pending_automatic_check'), () => action('mentor', {target_turn_id:target, trigger:'auto'}, 'mentor'), unavailable || busy || !state.pending_auto || running()),
    button(uiLanguage.t('admin.end_comparison_and_analyze'), () => action('end', {}, 'analysis'), busy || !allowed.end),
    button(uiLanguage.t('admin.refresh_state'), () => refresh().catch(error => { notice.textContent = error.message; })));
  question.disabled = busy || !allowed.turn;
  results.replaceChildren();
  for (const child of state.children) {
    const section = node('section', null, {'aria-label':`${roles[child.target_role]} ${uiLanguage.t('admin.results')}`});
    section.append(node('h2', `${roles[child.target_role]} ${uiLanguage.t('admin.results')}`));
    const grid = node('div', null, {class:'conversation-panels'});
    for (const panel of child.panels) {
      const card = node('article', null, {'aria-label':`${uiLanguage.t('admin.panel')} ${panel.panel_id}`, 'data-result-panel':`${child.comparison_run_id}/${panel.panel_id}`});
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
        if (button.dataset.action === 'adopt') button.disabled ||= !pending || !allowed.adopt;
        if (button.dataset.action === 'retry') button.disabled ||= !retryable;
        if (button.dataset.action === 'stop') button.disabled ||= unavailable;
      }
    }
    section.append(grid);
    results.append(section);
  }
  for (const action of state.mentor_actions) {
    results.append(node('p', `${uiLanguage.t('admin.mentor')} ${action.position}${uiLanguage.t('admin.turn')} · ${uiLanguage.t('admin.trigger_' + action.trigger)} (${action.trigger}): ${Object.entries(action.panel_decisions).map(([panel,decision]) => `${uiLanguage.t('admin.panel')} ${panel} ${uiLanguage.t('admin.decision_' + decision)} (${decision})`).join(' · ')}`));
  }
  const activeChild = state.children.find(child => child.panels.some(panel => panel.status === 'running'));
  if (activeChild && !unavailable) controls.append(button(uiLanguage.t('admin.stop_all'), () => action('stop', {child_id:activeChild.comparison_run_id,
    generations:activeChild.panels.filter(panel => panel.status === 'running').map(panel => ({panel_id:panel.panel_id, generation_id:panel.run_id}))})));
  if (controlText) {
    const root = cardId ? [...results.querySelectorAll('[data-result-panel]')].find(card => card.dataset.resultPanel === cardId) : controls;
    [...(root?.querySelectorAll('button') || [])].find(button => button.textContent === controlText && !button.disabled)?.focus({preventScroll:true});
  }
}
function mountSetup() {
  if (!data.source) { setup.append(node('p', uiLanguage.t('admin.choose_a_saved_scenario_first'))); return; }
  const activeRoles = ['student', 'analysis', ...(data.source.config.mentor.mode === 'off' ? [] : ['mentor'])];
  setup.append(node('h2', data.source.title));
  const count = field(setup, 'panel-count', uiLanguage.t('admin.number_of_comparison_models'), '1', {}, [['1','1'],['2','2'],['3','3']]);
  const panels = node('div', null, {class:'conversation-panels'});
  setup.append(panels);
  const start = button(uiLanguage.t('admin.start_comparison'), async () => {
    if (busy || selected.some(panel => !panel.model)) return;
    busy = true; start.disabled = true;
    pendingRequests++; try {
      const response = await request(base, 'POST', {request_id:crypto.randomUUID(), scenario_id:data.source.id,
        expected_scenario_version:data.source.config_version,
        panels:selected.map(panel => ({panel_id:panel.id, model_config_id:panel.model.id,
          role_options:Object.fromEntries(activeRoles.map(role => [role,panel.controls[role].selection.options]))}))});
      if (!response.ok) { const payload = await response.json(); throw new Error(errorMessage(response, payload.detail)); }
      state = await response.json(); remember(); render();
    } catch (error) { notice.textContent = error.message; }
    finally { pendingRequests--; busy = false; start.disabled = false; render(); }
  }, true);
  function draw() {
    selected = []; panels.replaceChildren();
    for (let i = 1; i <= Number(count.value); i++) {
      const card = node('fieldset'); card.append(node('legend', `${uiLanguage.t('admin.panel')} ${i}`)); panels.append(card);
      const select = field(card, `panel-${i}`, `${uiLanguage.t('admin.panel')} ${i} ${uiLanguage.t('admin.common_model')}`, '', {}, [['',uiLanguage.t('admin.select_a_model')]]);
      for (const model of data.model_choices) {
        const reason = commonModelReason(model, activeRoles);
        const option = node('option', `${providers[model.provider]} / ${model.display_name}${reason ? ` · ${reason}` : ''}`, {value:model.id});
        option.disabled = Boolean(reason); select.append(option);
      }
      const panel = {id:String(i), model:null, controls:{}}; selected.push(panel);
      for (const role of activeRoles) {
        const roleSelect = node('select', null, {id:`panel-${i}-${role}`, hidden:''});
        const options = node('div'), status = node('p'); card.append(roleSelect, status, options);
        panel.controls[role] = mountScenarioModel(data, role, `${uiLanguage.t('admin.panel')} ${i} ${roles[role]}`, {select:roleSelect, options, status}, null, () => {});
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
  if (!response.ok) throw new Error(uiLanguage.t('admin.could_not_load_comparison_history_try_again'));
  const history = await response.json();
  const root = document.getElementById('conversation-history');
  root.replaceChildren(node('h2', uiLanguage.t('admin.conversation_comparison_history')));
  if (!history.items.length) root.append(node('p', uiLanguage.t('admin.no_retained_comparison_conversations')));
  for (const item of history.items) {
    const row = node('p');
    row.append(node('a', `${item.title} · ${item.created_at} · ${phases[item.status] || uiLanguage.t('admin.unknown')}`, {href:`/admin/model-comparison?conversation_id=${item.conversation_id}`}),
      button(uiLanguage.t('admin.delete_comparison_record'), async () => {
        if (!confirm(uiLanguage.t('admin.delete_the_entire_comparison_record_the_cost_ledger_will_be_retained'))) return;
        pendingRequests++;
        const response = await request(`${base}/${item.conversation_id}`, 'DELETE').catch(() => null);
        pendingRequests--;
        if (!response) { notice.textContent = uiLanguage.t('admin.could_not_confirm_deletion_the_current_list_is_preserved'); return; }
        if (!response.ok) { notice.textContent = uiLanguage.t('admin.could_not_delete_the_comparison_record_check_the_state_again'); return; }
        if (state?.conversation_id === item.conversation_id) location.href = '/admin/model-comparison';
        else await loadHistory().catch(historyError);
      }));
    root.append(row);
  }
  if (history.next_cursor) root.append(button(uiLanguage.t('admin.next_20'), () => loadHistory(history.next_cursor).catch(historyError)));
}
function historyError() { notice.textContent = uiLanguage.t('admin.could_not_load_comparison_history_try_again'); }
mountComparisonHistory(document.getElementById('legacy-comparison-history'), {request, apply:(_panel, comparison) => { location.href = `/admin/scenarios/new?from_comparison=${comparison.comparison_run_id}`; }, invalidate:() => {}, sourceId:() => null});
mountSetup();
setupBaseline = setupValues();
if (state) { render(); if (running()) recover(); }
loadHistory().catch(historyError);
