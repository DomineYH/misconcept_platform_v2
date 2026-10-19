import {render, node, button, field, providers, roles, optionNotes} from './ai-render.js';

const status = document.getElementById('ai-status');
const error = document.getElementById('ai-error');
const editor = document.getElementById('ai-editor');
let state, busy = false, returnFocus;

let pendingRequests = 0, editorBaseline = '', catalogBaseline = '[]', editorOptions = null;
const editorValues = () => JSON.stringify({fields:[...editor.querySelectorAll('input:not([type=password]), select')].map(el => [el.name,el.value]), options:editorOptions?.() ?? null});
const catalogValues = () => JSON.stringify([...document.querySelectorAll('#ai-content select')].map(el => el.value));
uiLanguage.registerGuard(() => ({
  busy:busy || pendingRequests > 0 || Boolean(state?.models.some(model => Object.values(model.verification_state).some(role => role.status === 'verifying'))),
  dirty:(!editor.hidden && (editorValues() !== editorBaseline || [...editor.querySelectorAll('input[type=password]')].some(el => el.value))) || catalogValues() !== catalogBaseline
}));

function showError(code, location = document.activeElement.closest('#ai-editor, #ai-content article, #ai-content section')) {
  const messages = {401:uiLanguage.t('admin.please_log_in'), 403:uiLanguage.t('admin.check_your_permissions_or_security_token'),
    409:uiLanguage.t('admin.a_change_or_running_task_caused_a_conflict_reload_the_settings'),
    422:uiLanguage.t('admin.check_your_input_or_current_password'), 429:uiLanguage.t('admin.the_task_limit_has_been_reached_try_again_shortly')};
  error.textContent = messages[code] || uiLanguage.t('admin.could_not_complete_the_task_check_connection_settings_and_service_status');
  if (location) location.append(error);
  error.hidden = false;
  error.focus();
}

async function request(path, body) {
  const headers = {};
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    headers[uiLanguage.csrfHeader] = uiLanguage.csrfToken() || '';
  }
  const response = await fetch(`/admin/ai/${path}`, {method:body === undefined ? 'GET' : 'POST',
    credentials:'same-origin', headers:uiLanguage.headers(`/admin/ai/${path}`, headers), ...(body === undefined ? {} : {body:JSON.stringify(body)})});
  // Never display raw server detail, validation input, or exception text.
  if (!response.ok) throw response.status;
  return response.status === 204 ? null : response.json();
}

async function load() {
  status.textContent = uiLanguage.t('admin.loading_settings');
  pendingRequests++; try {
    const next = await request('state');
    state = next;
    status.after(error);
    render(state, actions);
    catalogBaseline = catalogValues();
    error.hidden = true;
    status.textContent = uiLanguage.t('admin.settings_loaded_opening_this_page_does_not_call_providers');
    return true;
  } catch (code) {
    status.after(error);
    showError(code, null);
    status.textContent = uiLanguage.t('admin.could_not_load_ai_connection_settings_check_that_administrator_connection_management_is_available');
    return false;
  } finally { pendingRequests--; }
}

async function submit(path, body, message = uiLanguage.t('admin.task_completed')) {
  if (busy) return;
  const submittedValues = editorValues();
  busy = true;
  error.hidden = true;
  status.textContent = uiLanguage.t('admin.working');
  const submitButton = editor.querySelector('button[type=submit]');
  if (submitButton) submitButton.disabled = true;
  pendingRequests++; try {
    const result = await request(path, body);
    const refreshed = await load();
    editorBaseline = submittedValues;
    status.textContent = refreshed ? message : `${message} ${uiLanguage.t('admin.reload_the_latest_state')}`;
    return result;
  } catch (code) {
    showError(code);
    status.textContent = uiLanguage.t('admin.task_failed');
  } finally { pendingRequests--;
    editor.querySelectorAll('input[type=password]').forEach(input => { input.value = ''; });
    if (submitButton) submitButton.disabled = false;
    busy = false;
  }
}

function closeEditor() {
  status.after(error);
  editor.replaceChildren();
  editor.hidden = true;
  let target = returnFocus;
  if (target && !target.isConnected) {
    const card = target.closest('[data-model], [data-provider]');
    const replacement = [...document.querySelectorAll('[data-model], [data-provider]')].find(el => card && el.dataset.model === card.dataset.model && el.dataset.provider === card.dataset.provider);
    target = [...(replacement?.querySelectorAll('button') || [])].find(el => el.textContent === returnFocus.textContent);
    if (target?.disabled) target = replacement?.querySelector('select');
  }
  (target || document.getElementById('ai-refresh')).focus();
}

function form(title, onSubmit) {
  editorOptions = null;
  returnFocus = document.activeElement;
  editor.replaceChildren(node('h2', title));
  editor.hidden = false;
  error.hidden = true;
  editor.append(error);
  const form = node('form');
  form.addEventListener('submit', event => { event.preventDefault(); onSubmit(form); });
  editor.append(form);
  return form;
}

function finish(form, label, role = 'primary') {
  editorBaseline = editorValues();
  form.append(node('button', label, {type:'submit', class:`btn-${role}`}), button(uiLanguage.t('admin.close'), closeEditor, false, 'secondary'));
  (form.querySelector('input, select') || form.querySelector('button')).focus();
  editor.scrollIntoView({block:'nearest'});
}

const actions = {
  connection(p, operation) {
    if (busy) return;
    const f = form(`${providers[p.provider]} ${uiLanguage.t('admin.change_connection')}`, f => {
      const payload = {expected_version:p.connection_version, current_password:f.elements.current_password.value};
      if (operation === 'key') payload.api_key = f.elements.api_key.value;
      if (operation === 'enabled') payload.enabled = !p.enabled;
      f.querySelectorAll('input[type=password]').forEach(input => { input.value = ''; });
      submit(`providers/${p.provider}/${operation}`, payload);
    });
    f.append(node('p', uiLanguage.t('admin.replacing_a_key_or_re_enabling_a_connection_requires_role_reverification_calls_started_before_replac')));
    const impact = node('ul');
    for (const item of p.impact) impact.append(node('li', item));
    f.append(node('p', uiLanguage.t('admin.affected_items')), impact);
    if (operation === 'key') field(f, 'api_key', uiLanguage.t('admin.new_api_key'), '', {type:'password', required:'', autocomplete:'off'});
    field(f, 'current_password', uiLanguage.t('admin.current_password'), '', {type:'password', required:'', autocomplete:'off'});
    finish(f, uiLanguage.t('admin.submit_change'), operation === 'delete' ? 'danger' : 'primary');
  },
  catalog(p) { submit(`providers/${p.provider}/catalog`, {expected_version:p.connection_version}, uiLanguage.t('admin.non_generation_verification_and_catalog_refresh_completed')); },
  register(p, modelId) {
    if (busy) return;
    const f = form(`${providers[p.provider]} ${uiLanguage.t('admin.register_model')}`, f => {
      submit('models', {provider:p.provider, model_id:f.elements.model_id.value.trim(),
        display_name:f.elements.display_name.value.trim()}, uiLanguage.t('admin.model_registered_role_testing_is_required_before_execution'));
    });
    f.append(node('p', uiLanguage.t('admin.new_models_are_registered_as_disabled_and_unverified_catalog_listing_does_not_replace_role_testing_m')));
    field(f, 'model_id', uiLanguage.t('admin.model_id'), modelId, {type:'text', required:''});
    field(f, 'display_name', uiLanguage.t('admin.display_name'), modelId, {type:'text', required:''});
    finish(f, uiLanguage.t('admin.register_model'));
  },
  model(m) {
    if (busy) return;
    let savedOptions = structuredClone(m.default_options);
    const f = form(`${m.model_id} ${uiLanguage.t('admin.model_settings')}`, f => {
      const options = structuredClone(savedOptions);
      for (const definition of m.capabilities?.fields || []) {
        const value = f.elements[definition.name].value;
        const path = definition.name.split('.');
        let target = options;
        for (const part of path.slice(0, -1)) target = target[part] ||= {};
        if (value === '') {
          delete target[path.at(-1)];
          if (path.length > 1 && Object.keys(target).length === 0) delete options[path[0]];
        } else target[path.at(-1)] = ['integer', 'number'].includes(definition.type) ? Number(value) : value;
      }
      submit(`models/${m.id}/update`, {expected_version:m.config_version,
        display_name:f.elements.display_name.value, enabled:f.elements.enabled.value === 'true',
        default_options:options});
    });
    field(f, 'display_name', uiLanguage.t('admin.display_name'), m.display_name, {type:'text', required:''});
    const enabled = field(f, 'enabled', uiLanguage.t('admin.model_enabled'), String(m.enabled), {}, [['false',uiLanguage.t('admin.inactive')],['true',uiLanguage.t('admin.active')]]);
    enabled.disabled = !m.enabled && (!m.capabilities || m.capabilities.metadata_conflict || !Object.values(m.verification_state).some(v => v.status === 'succeeded'));
    if (enabled.disabled) f.append(node('p', uiLanguage.t('admin.enabling_requires_a_matching_capability_definition_and_catalog_plus_successful_current_role_verifica')));
    f.append(node('p', uiLanguage.t('admin.model_ids_cannot_change_after_registration_disabling_blocks_new_calls_only_the_server_validates_supp')));
    if (!m.capabilities) f.append(node('p', uiLanguage.t('admin.capability_definition_required_option_editing_and_role_tests_are_unavailable')));
    editorOptions = () => savedOptions;
    if (m.capabilities) optionNotes(f, m.capabilities, savedOptions, options => { savedOptions = options; });
    else f.append(node('pre', JSON.stringify(m.default_options, null, 2)));
    for (const definition of m.capabilities?.fields || []) {
      const value = definition.name.split('.').reduce((obj, key) => obj?.[key], m.default_options);
      const attrs = {type:['integer','number'].includes(definition.type) ? 'number' : 'text'};
      if (definition.min != null) attrs.min = definition.min;
      if (definition.max != null) attrs.max = definition.max;
      if (definition.type === 'integer') attrs.step = '1';
      if (definition.type === 'number') attrs.step = 'any';
      field(f, definition.name, definition.label, value, attrs,
        definition.choices ? [['',uiLanguage.t('admin.unspecified')], ...definition.choices.map(v => [v,v])] : null);
    }
    finish(f, uiLanguage.t('admin.save_model_settings'));
  },
  probe(m, role) {
    if (busy) return;
    const requestId = crypto.randomUUID();
    const f = form(`${roles[role]} ${uiLanguage.t('admin.role_test')}`, async f => {
      const result = await submit(`models/${m.id}/probes`, {role, request_id:requestId,
        expected_version:m.config_version}, uiLanguage.t('admin.role_test_started_check_progress_or_cancel_explicitly'));
      if (result) f.querySelector('button[type=submit]').disabled = true;
    });
    const contracts = {student:uiLanguage.t('admin.plain_text_and_streaming'), mentor:uiLanguage.t('admin.verify_manual_coaching_and_automatic_no_intervention_as_separate_structured_responses'), analysis:uiLanguage.t('admin.v2_single_analysis_and_evidence_subset_synthesis_json')};
    f.append(node('p', `${roles[role]} · ${contracts[role]} · ${uiLanguage.t('admin.up_to_2_generation_calls_maximum_output_per_call')} ${m.probe_budgets[role]} ${uiLanguage.t('admin.tokens_no_automatic_retries')}`),
      node('p', uiLanguage.t('admin.charges_may_apply_tests_use_fixed_synthetic_inputs_success_does_not_guarantee_educational_quality_a_')),
      node('p', uiLanguage.t('admin.each_administrator_can_run_one_test_batch_at_a_time_tests_do_not_queue_when_capacity_is_unavailable_')));
    finish(f, uiLanguage.t('admin.start_test'));
  },
  async poll(requestId) {
    if (busy) return;
    pendingRequests++; try {
      const result = await request(`probes/${encodeURIComponent(requestId)}`);
      await load();
      const labels = {verifying:uiLanguage.t('admin.verifying'), succeeded:uiLanguage.t('admin.succeeded'), failed:uiLanguage.t('admin.failed'), stale:uiLanguage.t('admin.reverification_required')};
      status.textContent = `${uiLanguage.t('admin.test_status')} ${labels[result.status] || uiLanguage.t('admin.stopped')}`;
    } catch (code) { showError(code); } finally { pendingRequests--; }
  },
  cancel(requestId) {
    submit(`probes/${encodeURIComponent(requestId)}/cancel`, {}, uiLanguage.t('admin.cancellation_requested_provider_processing_and_incurred_costs_may_not_be_cancelled'));
  },
  settings(state) {
    if (busy) return;
    const settings = state.settings;
    const f = form(uiLanguage.t('admin.authoring_defaults_and_call_settings'), f => {
      const defaults = {}, limits = {}, timeouts = {};
      for (const role of Object.keys(roles)) defaults[role] = f.elements[`default_${role}`].value === '' ? null : Number(f.elements[`default_${role}`].value);
      for (const key of Object.keys(settings.limits)) limits[key] = Number(f.elements[`limit_${key}`].value);
      for (const key of Object.keys(settings.timeouts)) timeouts[key] = Number(f.elements[`timeout_${key}`].value);
      if (limits.admin >= limits.total || timeouts.student_first_output > timeouts.student_total || timeouts.mentor_first_output > timeouts.mentor_total) {
        editor.append(error);
        error.hidden = false;
        error.textContent = uiLanguage.t('admin.the_admin_limit_must_be_below_the_total_limit_and_first_output_timeouts_must_not_exceed_total_timeou');
        error.focus();
        return;
      }
      submit('settings/update', {expected_version:settings.settings_version, defaults, limits, timeouts});
    });
    f.append(node('p', uiLanguage.t('admin.defaults_apply_to_new_scenarios_only_existing_scenarios_and_sessions_remain_unchanged_unavailable_de')));
    for (const [role, label] of Object.entries(roles)) {
      const current = settings.defaults[role];
      const choices = [['', uiLanguage.t('admin.no_default')]];
      for (const m of state.models) {
        if (m.id === current?.model_config_id && !current.available) continue;
        const p = state.providers.find(p => p.provider === m.provider);
        if (p.enabled && p.status === 'ready' && state.master_key_available && m.enabled && m.capabilities && !m.capabilities.metadata_conflict && m.verification_state[role].status === 'succeeded') choices.push([m.id, `${providers[m.provider]} / ${m.display_name}`]);
      }
      const unavailable = current && !choices.some(([id]) => id === current.model_config_id);
      if (unavailable) choices.push([current.model_config_id, `${uiLanguage.t('admin.default_model_unavailable')} (${current.model_config_id})`]);
      const select = field(f, `default_${role}`, `${label} ${uiLanguage.t('admin.default_authoring_model')}`, current?.model_config_id, {}, choices);
      if (unavailable) select.lastElementChild.disabled = true;
    }
    const limitLabels = {total:uiLanguage.t('admin.total_call_limit'), openai:uiLanguage.t('admin.openai_call_limit'), anthropic:uiLanguage.t('admin.claude_call_limit'), google:uiLanguage.t('admin.gemini_call_limit'), admin:uiLanguage.t('admin.admin_task_limit')};
    const limitGroup = node('fieldset');
    limitGroup.append(node('legend', uiLanguage.t('admin.concurrent_call_limits')));
    for (const [key, label] of Object.entries(limitLabels)) field(limitGroup, `limit_${key}`, label, settings.limits[key], {type:'number', required:'', min:key === 'admin' ? 1 : 2, ...(key === 'admin' ? {max:3} : {}), step:1});
    f.append(limitGroup);
    const timeoutLabels = {connect:uiLanguage.t('admin.connection_timeout_seconds'), student_first_output:uiLanguage.t('admin.student_first_output_timeout_seconds'), student_total:uiLanguage.t('admin.student_total_timeout_seconds'), mentor_first_output:uiLanguage.t('admin.mentor_first_output_timeout_seconds'), mentor_total:uiLanguage.t('admin.mentor_total_timeout_seconds'), analysis_total:uiLanguage.t('admin.analysis_total_timeout_seconds'), model_list_total:uiLanguage.t('admin.catalog_total_timeout_seconds')};
    const timeoutGroup = node('fieldset');
    timeoutGroup.append(node('legend', uiLanguage.t('admin.timeouts')));
    for (const [key, label] of Object.entries(timeoutLabels)) field(timeoutGroup, `timeout_${key}`, label, settings.timeouts[key], {type:'number', required:'', min:1, step:1});
    f.append(timeoutGroup);
    finish(f, uiLanguage.t('admin.save_settings'));
  }
};
document.getElementById('ai-refresh').addEventListener('click', () => { if (!busy) load(); });
editor.addEventListener('keydown', event => { if (event.key === 'Escape') closeEditor(); });
window.addEventListener('pagehide', () => editor.querySelectorAll('input[type=password]').forEach(input => { input.value = ''; }));
load();
