import {node, button, field, roles, providers} from './ai-render.js';
import {mountComparisonHistory} from './comparison-history.js';
import {readSSE} from './student-sse.js';
import {active, renderComparisonResult} from './comparison-results.js';
import {identity, mountScenarioModel} from './scenario-models.js';

export function mountComparison(data, draft) {
  let pendingRequests = 0, uncertain = false, unacknowledgedStart = false, comparisonBaseline = null;
  const inputSnapshot = value => JSON.stringify(value, (_key, item) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return item;
    return Object.fromEntries(Object.entries(item).sort(([left], [right]) => left.localeCompare(right)));
  });
  const comparisonInput = (role, history, message, selections) => inputSnapshot([role, history.map(({role,content}) => ({role,content})), message ?? '', selections]);
  const comparisonValues = () => comparisonInput(target.value, commonHistory(), question.value, panels.map(panel => panel.control.selection));
  const open = document.getElementById('open-comparison');
  const root = document.getElementById('scenario-comparison');
  const heading = node('h2', uiLanguage.t('admin.model_comparison_with_common_context'), {tabindex:'-1'});
  const source = node('p');
  const close = button(uiLanguage.t('admin.close_comparison'), () => {
    root.hidden = true;
    open.setAttribute('aria-expanded', 'false');
    open.focus();
  }, false, 'secondary');
  const setup = node('fieldset');
  setup.append(node('legend', uiLanguage.t('admin.common_input')));
  const target = field(setup, 'comparison-target', uiLanguage.t('admin.comparison_role'), 'student', {}, Object.entries(roles));
  const note = node('p');
  const history = node('div', null, {id:'comparison-history-input'});
  const pairs = [];
  let panels = [], nextPanel = 0, nextPair = 0, run = null, submitting = false;
  const notice = node('p', null, {role:'status', tabindex:'-1'});
  const errors = node('div', null, {role:'alert', tabindex:'-1'});
  const total = node('p', null, {id:'comparison-total'});
  const editorLocks = new Map();
  let reset, previousRole = 'student', parentRunId = null;
  const rolePanels = new Map(), roleBaselines = new Map();
  function savedComparison() {
    const selections = run.panels.map(panel => panel.model_config);
    comparisonBaseline = comparisonInput(run.target_role, run.canonical_context, run.current_message, selections);
    roleBaselines.set(run.target_role, inputSnapshot(selections));
  }
  let recovery = null, streamToken = null;
  function remember(id) {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set('comparison_id', id);
    else url.searchParams.delete('comparison_id');
    window.history.replaceState(null, '', url);
  }
  function textarea(parent, id, label) {
    const input = node('textarea', null, {id, rows:3, maxlength:50000});
    parent.append(node('label', label, {for:id}), input);
    return input;
  }
  function addPair(teacher = '', student = '') {
    const row = node('fieldset');
    const index = ++nextPair;
    row.append(node('legend', `${uiLanguage.t('admin.history')} ${index}`));
    const t = textarea(row, `comparison-teacher-${index}`, `${uiLanguage.t('admin.history')} ${index} ${uiLanguage.t('admin.teacher')}`);
    const s = textarea(row, `comparison-student-${index}`, `${uiLanguage.t('admin.history')} ${index} ${uiLanguage.t('admin.student_label')}`);
    t.value = teacher;
    s.value = student;
    const pair = {row, teacher:t, student:s};
    pairs.push(pair);
    row.append(button(`${uiLanguage.t('admin.history')} ${index} ${uiLanguage.t('admin.delete')}`, () => {
      pairs.splice(pairs.indexOf(pair), 1);
      row.remove();
      addHistory.focus();
    }, false, 'secondary'));
    history.append(row);
    t.focus();
  }
  const addHistory = button(uiLanguage.t('admin.add_completed_pair'), () => addPair(), false, 'secondary');
  const questionLabel = node('label', uiLanguage.t('admin.current_student_question'), {for:'comparison-question'});
  const question = node('textarea', null, {id:'comparison-question', rows:3, maxlength:50000});
  setup.append(note, history, addHistory, questionLabel, question);
  const panelNote = node('p', null, {role:'status'});
  const tabs = node('div', null, {class:'comparison-tabs', role:'tablist', 'aria-label':uiLanguage.t('admin.comparison_panels')});
  let selectedPanel = null;
  const grid = node('div', null, {class:'comparison-grid', role:'region', 'aria-label':uiLanguage.t('admin.comparison_panel_results'), tabindex:'0'});
  const addPanel = button(uiLanguage.t('admin.add_panel'), () => addModel(null), false, 'secondary');
  function available(model) {
    return model.enabled && model.connection_available && model.capabilities && !model.capabilities.metadata_conflict && model.verification_state[target.value]?.status === 'succeeded';
  }
  function syncPanels() {
    panelNote.textContent = panels.length === 0 ? uiLanguage.t('admin.unconfigured_verify_the_target_role_in_ai_connections_and_models') : panels.length === 1 ? uiLanguage.t('admin.single_panel_test') : `${panels.length}${uiLanguage.t('admin.panels_common_context_comparison')}`;
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
      const tab = button(`${uiLanguage.t('admin.panel')} ${panel.id}`, () => selectTab(panel.id), false, 'secondary');
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
  function addModel(saved, panelId = null) {
    if (panels.length >= 3) return;
    const id = panelId ?? ++nextPanel;
    nextPanel = Math.max(nextPanel, Number(id));
    const card = node('article', null, {'aria-label':`${uiLanguage.t('admin.panel')} ${id}`, 'data-comparison-panel':id, id:`comparison-panel-${id}`});
    const settings = node('fieldset');
    settings.append(node('legend', `${uiLanguage.t('admin.panel')} ${id} ${uiLanguage.t('admin.model_and_options')}`));
    const select = node('select', null, {id:`comparison-model-${id}`});
    const options = node('div');
    const status = node('p', null, {role:'status'});
    settings.append(node('label', `${uiLanguage.t('admin.panel')} ${id} ${uiLanguage.t('admin.provider_model')}`, {for:select.id}), select, status, options);
    const output = node('div');
    output.append(node('p', uiLanguage.t('admin.waiting'), {role:'status'}));
    const panel = {id:String(id), card, settings, output};
    panel.control = mountScenarioModel(data, target.value, `${uiLanguage.t('admin.panel')} ${id}`, {select, options, status}, saved, () => {});
    settings.append(button(`${uiLanguage.t('admin.panel')} ${id} ${uiLanguage.t('admin.remove')}`, () => {
      panels = panels.filter(p => p !== panel);
      card.remove();
      syncPanels();
      addPanel.focus();
    }, false, 'secondary'));
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
    roleBaselines.set(target.value, inputSnapshot(panels.map(panel => panel.control.selection)));
  }
  function roleNote() {
    question.hidden = questionLabel.hidden = target.value === 'mentor';
    questionLabel.textContent = target.value === 'analysis' ? uiLanguage.t('admin.last_teacher_message_analysis_optional') : uiLanguage.t('admin.current_student_question');
    const mode = draft().config.mentor.mode;
    note.textContent = uiLanguage.t('admin.the_full_canonical_conversation_history_is_used_classroom_recent_turn_limits_automatic_truncation_an') + (target.value === 'mentor' ? mode === 'off' ? uiLanguage.t('admin.mentor_off_enable_it_in_the_editor_before_running') : mode === 'auto' ? uiLanguage.t('admin.intervention_judgment_test_classroom_start_turn_interval_and_count_limits_do_not_apply') : uiLanguage.t('admin.request_manual_coaching_for_the_last_completed_pair') : target.value === 'analysis' ? uiLanguage.t('admin.analyze_the_full_dialogue_in_one_call_comparison_results_do_not_replace_educational_quality_approval') : uiLanguage.t('admin.compare_student_responses_to_the_current_question'));
  }
  target.addEventListener('change', () => {
    rolePanels.set(previousRole, panels.map(p => structuredClone(p.control.selection)));
    previousRole = target.value;
    recovery = streamToken = null;
    remember(null);
    parentRunId = run?.comparison_run_id || parentRunId;
    run = null;
    total.textContent = '';
    defaults();
    roleNote();
  });
  const start = button(uiLanguage.t('admin.run_comparison'), () => execute(), false, 'primary');
  const stop = button(uiLanguage.t('admin.stop_all'), () => stopRun(), false, 'secondary');
  const refresh = button(uiLanguage.t('admin.refresh_state'), () => refreshRun(), false, 'secondary');
  root.append(heading, source, close, notice, errors, setup, panelNote, addPanel, start, stop, refresh, total, tabs, grid);
  function locked() { return submitting || run?.panels.some(p => active(p.status)); }
  function sync() {
    const running = locked();
    setup.disabled = running;
    if (reset) reset.disabled = running;
    start.disabled = running || panels.length === 0;
    addPanel.disabled = running || panels.length >= 3;
    stop.disabled = !run?.panels.some(p => active(p.status)) || run.panels.some(p => active(p.status) && !p.run_id);
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
      panel.output.append(node('p', `${uiLanguage.t('admin.common_input_hash')} ${run.context_hash || uiLanguage.t('admin.unknown')}`));
    }
    total.textContent = `${unknown ? uiLanguage.t('admin.partial_subtotal_unknown_costs_present') : uiLanguage.t('admin.total_cost')}: ${cost}`;
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
    notice.textContent = uiLanguage.t('admin.canonical_history_is_ready_for_the_next_comparison_the_original_comparison_is_preserved');
  }
  async function apply(state, comparison = run) {
    if (locked()) return;
    if (comparison.source_snapshot?.scenario_id !== data.id && data.comparison_draft_id !== comparison.comparison_run_id) {
      notice.textContent = uiLanguage.t('admin.open_the_source_editor_or_a_new_draft_with_these_conditions_to_apply_the_model');
      return;
    }
    const token = streamToken;
    pendingRequests++; try {
      const response = await request(`/admin/comparisons/${comparison.comparison_run_id}/panels/${state.panel_id}/model`);
      if (!response.ok) {
        const messages = {409:uiLanguage.t('admin.the_comparison_is_running_apply_after_it_ends'),
          404:uiLanguage.t('admin.could_not_find_or_open_the_comparison_or_panel_check_the_history_again'),
          422:uiLanguage.t('admin.check_current_role_verification_connection_and_options_before_applying')};
        notice.textContent = messages[response.status] || uiLanguage.t('admin.could_not_apply_the_model_check_again');
        return;
      }
      const selection = await response.json();
      if (locked() || streamToken !== token) return;
      const select = document.getElementById(`${selection.target_role}.resolved_model_config`);
      select.dispatchEvent(new CustomEvent('model:apply', {detail:selection.model_config}));
      document.querySelector(`[data-step="${{student:2,mentor:3,analysis:4}[selection.target_role]}"]`).click();
      document.getElementById('common-model').focus();
      if (selection.target_role === 'mentor' && document.activeElement !== document.getElementById('common-model')) document.getElementById('mentor.mode').focus();
      notice.textContent = uiLanguage.t('admin.model_and_options_were_applied_to_the_unsaved_form_save_or_publish_separately');
    } catch {
      notice.textContent = uiLanguage.t('admin.check_current_role_verification_connection_and_options_before_applying');
    } finally { pendingRequests--; }
  }
  async function request(url, method = 'GET', body, signal) {
    return fetch(url, {method, signal, credentials:'same-origin', headers:uiLanguage.headers(url, {'Content-Type':'application/json',
      [uiLanguage.csrfHeader]:uiLanguage.csrfToken() || ''}), body:body == null ? undefined : JSON.stringify(body)});
  }
  function showError(response, result, missingId = run?.comparison_run_id) {
    errors.replaceChildren();
    const missingComparison = response.status === 404 && (!result || result.detail?.code === 'comparison_not_found');
    if (missingComparison) uncertain = false;
    if (missingComparison && parentRunId === missingId) parentRunId = null;
    if ([401,403].includes(response.status) || missingComparison && run?.comparison_run_id === missingId) {
      recovery = streamToken = null;
      run = null;
      remember(null);
      for (const panel of panels) panel.output.replaceChildren();
      total.textContent = '';
      sync();
    }
    const messages = {404:uiLanguage.t('admin.could_not_find_or_open_the_comparison_records_are_retained_for_30_days_after_creation'),
      409:uiLanguage.t('admin.a_comparison_is_running_or_an_attempt_changed_check_the_state_again'), 429:uiLanguage.t('admin.could_not_reserve_capacity_for_all_panels_try_again_shortly'),
      503:uiLanguage.t('admin.check_ai_connection_and_model_settings'), 422:uiLanguage.t('admin.check_common_input_and_model_options'), 413:uiLanguage.t('admin.the_input_is_too_large'), 401:uiLanguage.t('admin.please_log_in'), 403:uiLanguage.t('admin.check_your_permissions_or_security_token')};
    errors.append(node('p', result?.detail?.code === 'scenario_not_found' ? uiLanguage.t('admin.source_scenario_not_found_compare_from_a_new_draft') : messages[response.status] || uiLanguage.t('admin.could_not_process_the_comparison_request_your_input_is_preserved')));
    for (const error of result?.detail?.errors || []) {
      const panel = panels.find(p => p.id === error.panel_id);
      const input = error.field === 'current_message' ? question : error.field === 'history' ? addHistory : panel?.settings.querySelector('select');
      if (input) input.setAttribute('aria-invalid', 'true');
      errors.append(button(error.message || uiLanguage.t('common.check_input'), () => input?.focus(), false, 'secondary'));
    }
    errors.focus();
  }
  function applySnapshot(snapshot) {
    for (const state of snapshot.panels) {
      const prior = run?.comparison_run_id === snapshot.comparison_run_id && run.panels.find(p => p.panel_id === state.panel_id && p.run_id === state.run_id);
      state.lastSeq = prior?.lastSeq ?? state.seq ?? -1;
      if (prior?.status === 'streaming' && state.status === 'running') state.status = 'streaming';
      if (!state.text && prior?.text && (active(state.status) || state.status === 'cancelled')) state.text = prior.text;
    }
    if (run?.comparison_run_id === snapshot.comparison_run_id) Object.assign(run, snapshot);
    else run = snapshot;
    remember(snapshot.comparison_run_id);
    sync();
  }
  function event(type, value) {
    const state = run?.panels.find(p => p.panel_id === value.panel_id);
    if (!state || (run.comparison_run_id && value.comparison_run_id !== run.comparison_run_id)) return;
    if (type === 'run.accepted') {
      if (state.run_id || !active(state.status)) return;
      state.run_id = value.run_id;
      run.comparison_run_id = value.comparison_run_id;
      remember(value.comparison_run_id);
      run.context_hash = value.context_hash ?? run.context_hash;
      for (const key of ['source_snapshot', 'canonical_context', 'current_message_id', 'created_at', 'expires_at']) {
        if (value[key] !== undefined) run[key] = value[key];
      }
      if (value.model_config) state.model_config = value.model_config;
      if (value.applied_options) state.applied_options = value.applied_options;
    }
    if (value.run_id !== state.run_id || (!active(state.status) && !(state.status === 'cancelled' && type === 'run.cancelled')) || value.seq <= (state.lastSeq ?? -1)) return;
    state.lastSeq = value.seq;
    if (type === 'run.accepted') Object.assign(state, {status:'running'});
    else if (type === 'output.delta' && run.target_role === 'student') { state.text += value.text || ''; state.status = 'streaming'; }
    else if (type === 'output.completed') Object.assign(state, value, {status:value.outcome_status || 'completed'});
    else if (['run.failed','run.cancelled','run.interrupted'].includes(type)) Object.assign(state, value, {status:type.slice(4)});
    sync();
  }
  async function consume(response, token) {
    if (response.headers.get('Content-Type')?.includes('application/json')) {
      const snapshot = await response.json();
      if (streamToken === token) applySnapshot(snapshot);
    }
    else {
      const snapshot = run;
      await readSSE(response.body, (type, value) => {
        if (run !== snapshot || streamToken !== token) return false;
        event(type, value);
      });
    }
  }
  async function execute(retry = null) {
    if (locked()) return;
    const sourceDraft = draft();
    roleNote();
    if (!retry && (target.value === 'student' && !question.value.trim() || target.value === 'mentor' && (pairs.length === 0 || sourceDraft.config.mentor.mode === 'off') || target.value === 'analysis' && pairs.length === 0 && !question.value.trim())) {
      errors.replaceChildren(node('p', target.value === 'mentor' ? uiLanguage.t('admin.enable_the_mentor_and_add_a_completed_pair') : uiLanguage.t('admin.enter_a_teacher_message')));
      errors.focus();
      return;
    }
    const prior = run;
    recovery = null;
    const token = streamToken = {};
    const body = retry ? {request_id:crypto.randomUUID(), generation_id:retry.run_id} : {
      request_id:crypto.randomUUID(), target_role:target.value,
      source_snapshot:{...sourceDraft, scenario_id:data.id, source_version:data.id == null ? null : data.config_version, source_kind:'unsaved_draft'},
      canonical_context:commonHistory(), current_message:target.value === 'mentor' ? null : question.value,
      panels:panels.map(p => ({panel_id:p.id, model_config:structuredClone(p.control.selection)})),
      parent_run_id:run?.comparison_run_id || parentRunId,
    };
    submitting = true;
    errors.replaceChildren();
    root.querySelectorAll('[aria-invalid]').forEach(input => input.removeAttribute('aria-invalid'));
    notice.textContent = uiLanguage.t('admin.checking_common_input_for_all_panels');
    sync();
    pendingRequests++; try {
      const url = retry ? `/admin/comparisons/${run.comparison_run_id}/panels/${retry.panel_id}/retry` : '/admin/comparisons';
      const response = await request(url, 'POST', body);
      if (!response.ok) { showError(response, await response.json().catch(() => null), retry ? run.comparison_run_id : body.parent_run_id); return; }
      if (retry) {
        retry.attempts = [...(retry.attempts || []), structuredClone({...retry, attempts:[]})];
        Object.assign(retry, {run_id:null, status:'waiting', lastSeq:-1, text:''});
      } else run = {...body, comparison_run_id:null, panels:body.panels.map(p => ({...p, status:'waiting', text:'', lastSeq:-1}))};
      submitting = false;
      sync();
      await consume(response, token);
      if (!locked()) { uncertain = false; savedComparison(); }
      if (streamToken !== token) return;
      notice.textContent = locked() ? uiLanguage.t('admin.operation_in_progress') : uiLanguage.t('admin.comparison_ended_check_each_panel_s_results');
      if (locked()) notice.textContent = uiLanguage.t('admin.in_progress_refresh_the_state_or_stop');
    } catch {
      if (streamToken !== token) return;
      uncertain = true;
      // A lookup of the previous run cannot confirm an admission whose ID was lost.
      if (!retry && (!run?.comparison_run_id || run === prior)) unacknowledgedStart = true;
      if (!run?.comparison_run_id) run = prior;
      notice.textContent = uiLanguage.t('admin.connection_interrupted_refresh_the_state_to_check_saved_results_your_input_is_preserved');
    } finally { pendingRequests--; if (streamToken === token || streamToken === null) { submitting = false; sync(); } }
    if (streamToken === token && run?.comparison_run_id && locked()) recoverRun(run.comparison_run_id);
  }
  async function recoverRun(id, restore = false) {
    if (recovery?.id === id) return;
    uncertain = true;
    const token = recovery = {id};
    const deadline = Date.now() + 10000;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    pendingRequests++; try {
      while (recovery === token) {
        const response = await request(`/admin/comparisons/${id}`, 'GET', null, controller.signal);
        if (recovery !== token) return;
        if (!response.ok) { showError(response, null, id); return; }
        const snapshot = await response.json();
        if (recovery !== token) return;
        if (restore) {
          target.value = previousRole = snapshot.target_role;
          source.textContent = `${snapshot.source_snapshot?.title || uiLanguage.t('admin.untitled_draft')} · ${uiLanguage.t('admin.frozen_conditions_from_a_saved_comparison')}`;
          panels = [];
          nextPanel = nextPair = 0;
          grid.replaceChildren();
          history.replaceChildren();
          pairs.splice(0);
          for (const panel of snapshot.panels) addModel(panel.model_config, panel.panel_id);
          for (let i = 0; i < snapshot.canonical_context.length; i += 2) addPair(snapshot.canonical_context[i].content, snapshot.canonical_context[i+1].content);
          question.value = snapshot.current_message || '';
          roleNote();
          syncPanels();
          restore = false;
          submitting = false;
        }
        applySnapshot(snapshot);
        uncertain = false;
        if (!snapshot.panels.some(p => active(p.status))) savedComparison();
        if (!snapshot.panels.some(p => active(p.status))) {
          notice.textContent = uiLanguage.t('admin.saved_comparison_results_recovered');
          return;
        }
        const remaining = deadline - Date.now();
        if (remaining <= 1000) break;
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
      if (recovery === token) notice.textContent = uiLanguage.t('admin.recovery_lookup_ended_refresh_the_state_or_stop');
    } catch {
      if (recovery === token) notice.textContent = uiLanguage.t('admin.recovery_lookup_ended_refresh_the_state_or_stop');
    } finally { pendingRequests--;
      clearTimeout(timeout);
      if (recovery === token) recovery = null;
      if (restore) { submitting = false; sync(); }
    }
  }
  async function refreshRun() {
    if (!run?.comparison_run_id || submitting) return;
    const comparison = run, token = streamToken;
    pendingRequests++; try {
      const response = await request(`/admin/comparisons/${run.comparison_run_id}`);
      if (run !== comparison || streamToken !== token) return;
      if (response.ok) {
        const snapshot = await response.json();
        if (run === comparison && streamToken === token) { applySnapshot(snapshot); uncertain = false; if (!locked()) savedComparison(); }
      }
      else showError(response, null);
    } catch { notice.textContent = uiLanguage.t('admin.could_not_confirm_the_state_check_again'); } finally { pendingRequests--; }
  }
  async function stopRun(state = null) {
    if (!run?.comparison_run_id) return;
    const comparison = run, token = streamToken;
    recovery = null;
    pendingRequests++; try {
      const url = `/admin/comparisons/${run.comparison_run_id}${state ? `/panels/${state.panel_id}` : ''}/stop`;
      const body = state ? {generation_id:state.run_id} : {generations:run.panels.filter(p => active(p.status)).map(p => ({panel_id:p.panel_id,generation_id:p.run_id}))};
      const response = await request(url, 'POST', body);
      if (run !== comparison || streamToken !== token) return;
      if (response.ok) {
        const snapshot = await response.json();
        if (run === comparison && streamToken === token) { applySnapshot(snapshot); uncertain = false; if (!locked()) savedComparison(); }
      }
      else showError(response, null);
    } catch { notice.textContent = uiLanguage.t('admin.could_not_confirm_the_stop_state_refresh_the_state'); } finally { pendingRequests--; }
  }
  reset = button(uiLanguage.t('admin.reset_comparison_input'), () => {
    if (locked()) return;
    recovery = streamToken = null;
    remember(null);
    parentRunId = null;
    run = null;
    pairs.splice(0);
    history.replaceChildren();
    nextPair = 0;
    question.value = '';
    rolePanels.clear();
    roleBaselines.clear();
    target.value = previousRole = 'student';
    errors.replaceChildren();
    total.textContent = '';
    defaults();
    roleNote();
    comparisonBaseline = comparisonValues();
    notice.textContent = uiLanguage.t('admin.comparison_input_reset_saved_comparison_records_are_preserved');
    target.focus();
  }, false, 'secondary');
  root.append(reset);
  mountComparisonHistory(root, {request, apply, sourceId:() => data.id, sourceComparisonId:() => data.comparison_draft_id, invalidate:id => {
    if (parentRunId === id) parentRunId = null;
    if (run?.comparison_run_id !== id) return;
    recovery = streamToken = null;
    remember(null);
    run = null;
    for (const panel of panels) panel.output.replaceChildren();
    total.textContent = '';
    sync();
  }});
  sync();
  let initialized = false;
  uiLanguage.registerGuard(() => ({busy:Boolean(locked() || recovery || pendingRequests || uncertain || unacknowledgedStart), dirty:initialized && (comparisonValues() !== comparisonBaseline || [...rolePanels].some(([role,selections]) => role !== target.value && inputSnapshot(selections) !== roleBaselines.get(role)))}));
  open.addEventListener('click', () => {
    source.textContent = `${draft().title || uiLanguage.t('admin.untitled_draft')} · ${uiLanguage.t('admin.unsaved_draft')} · ${data.id == null ? uiLanguage.t('admin.new_draft_no_source') : `${uiLanguage.t('admin.source_scenario')} #${data.id} · ${uiLanguage.t('admin.version')} ${data.config_version}`}`;
    if (!initialized) { defaults(); initialized = true; comparisonBaseline = comparisonValues(); }
    roleNote();
    root.hidden = false;
    open.setAttribute('aria-expanded', 'true');
    heading.focus();
  });
  const params = new URLSearchParams(window.location.search);
  const restoreId = params.get('comparison_id') || data.comparison_draft_id;
  if (params.get('compare') === '1' || restoreId) open.click();
  if (restoreId) {
    submitting = true;
    sync();
    recoverRun(restoreId, true);
  }
}
