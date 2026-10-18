export const providers = {openai:'OpenAI', anthropic:'Claude', google:'Gemini'};
export const roles = {student:uiLanguage.t('admin.student'), mentor:uiLanguage.t('admin.mentor'), analysis:uiLanguage.t('admin.post_session_analysis')};
const states = {unconfigured:uiLanguage.t('admin.unconfigured'), ready:uiLanguage.t('admin.available'), decryption_failed:uiLanguage.t('admin.decryption_failed'),
  unverified:uiLanguage.t('admin.unverified'), verifying:uiLanguage.t('admin.verifying'), succeeded:uiLanguage.t('admin.succeeded'), failed:uiLanguage.t('admin.failed'), stale:uiLanguage.t('admin.reverification_required')};
const roleErrors = {
  refused:uiLanguage.t('admin.the_response_was_refused_check_settings_and_test_again'),
  output_limit:uiLanguage.t('admin.the_output_limit_was_reached_check_settings_and_test_again'),
  invalid_json:uiLanguage.t('admin.the_response_is_not_complete_json_check_settings_and_test_again'),
  invalid_reference:uiLanguage.t('admin.a_label_utterance_reference_or_quotation_is_invalid_check_settings_and_test_again'),
  empty_response:uiLanguage.t('admin.required_results_are_empty_check_settings_and_test_again'),
  invalid_output:uiLanguage.t('admin.the_response_format_does_not_match_the_role_contract_check_settings_and_test_again'),
  transient:uiLanguage.t('admin.the_provider_encountered_a_temporary_problem_test_again_shortly'),
  timeout_connect:uiLanguage.t('admin.the_provider_connection_timed_out_check_the_connection'),
  timeout_first_output:uiLanguage.t('admin.the_first_response_timed_out_check_the_timeout_settings'),
  timeout_total:uiLanguage.t('admin.the_total_call_timeout_was_reached_check_the_timeout_settings'),
  call_limit_reached:uiLanguage.t('admin.the_concurrent_call_limit_was_reached_wait_for_active_calls_to_finish_before_testing_again')
};

export function node(tag, text, attrs = {}) {
  const el = document.createElement(tag);
  if (text != null) el.textContent = text;
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  return el;
}

export function button(text, action, disabled = false, role = '') {
  const el = node('button', text, {type:'button'});
  if (role) el.classList.add(`btn-${role}`);
  el.disabled = disabled;
  el.addEventListener('click', action);
  return el;
}

export function field(form, name, label, value = '', attrs = {}, choices = null) {
  const group = node('div', null, {class:'form-group'});
  const id = `ai-${name}`;
  const input = node(choices ? 'select' : 'input', null, {id, name, ...attrs});
  if (choices) for (const [key, text] of choices) input.append(node('option', text, {value:key}));
  if (choices && value != null && !choices.some(([key]) => String(key) === String(value))) {
    input.append(node('option', `${value} · ${uiLanguage.t('admin.unsupported_saved_value')}`, {value}));
  }
  input.value = value ?? '';
  group.append(node('label', label, {for:id}), input);
  form.append(group);
  return input;
}

export function optionNotes(container, capabilities, saved = {}, onRemove) {
  if (capabilities.definition_version) container.append(node('p', `${uiLanguage.t('admin.capability_definition')} ${capabilities.definition_version} · ${uiLanguage.t('admin.official_verification')} ${capabilities.checked_at}`));
  for (const definition of capabilities.fields || []) {
    if (definition.default != null) container.append(node('p', `${definition.label} ${uiLanguage.t('admin.default')} ${definition.default}`));
  }
  for (const note of capabilities.option_notes || []) container.append(node('p', note));
  const unknown = structuredClone(saved);
  for (const definition of capabilities.fields || []) {
    const path = definition.name.split('.');
    const target = path.slice(0, -1).reduce((obj, key) => obj?.[key], unknown);
    if (target) {
      delete target[path.at(-1)];
      if (path.length === 2 && Object.keys(target).length === 0) delete unknown[path[0]];
    }
  }
  if (Object.keys(unknown).length) {
    const notice = node('div');
    notice.append(node('p', uiLanguage.t('admin.these_saved_options_are_absent_from_the_capability_definition_editing_other_fields_preserves_them_ch')), node('pre', JSON.stringify(unknown, null, 2)));
    notice.append(button(uiLanguage.t('admin.remove_undefined_saved_options'), () => {
      const retained = {};
      for (const definition of capabilities.fields || []) {
        const path = definition.name.split('.');
        const value = path.reduce((obj, key) => obj?.[key], saved);
        if (value === undefined) continue;
        let target = retained;
        for (const part of path.slice(0, -1)) target = target[part] ||= {};
        target[path.at(-1)] = value;
      }
      onRemove(retained);
      notice.remove();
      container.querySelector('input:not(:disabled),select:not(:disabled)')?.focus();
    }, false, 'secondary'));
    container.append(notice);
  }
}

export function render(state, actions) {
  const root = document.getElementById('ai-content');
  root.replaceChildren();
  if (!state.master_key_available) root.append(node('p', uiLanguage.t('admin.master_key_required_restore_the_original_infrastructure_master_key_and_version_saving_keys_and_ai_ca')));
  const connections = node('section', null, {'aria-label':uiLanguage.t('admin.provider_connections')});
  connections.append(node('h2', uiLanguage.t('admin.1_provider_connections')), node('p', uiLanguage.t('admin.non_generation_verification_and_catalog_refresh_are_separate_from_role_verification_and_educational_')));
  const cards = node('div', null, {class:'ai-grid'});
  for (const p of state.providers) {
    const card = node('article', null, {'data-provider':p.provider});
    card.append(node('h3', providers[p.provider]),
      node('p', `${uiLanguage.t('admin.key')} ${p.key_registered ? uiLanguage.t('admin.registered') : uiLanguage.t('admin.not_registered')} ${p.masked_hint || ''} · ${p.enabled ? uiLanguage.t('admin.active') : uiLanguage.t('admin.inactive')} · ${states[p.status] || uiLanguage.t('admin.unavailable')}`),
      node('p', `${uiLanguage.t('admin.latest_non_generation_verification')} ${p.verified_at || uiLanguage.t('admin.none')}`));
    if (p.status === 'decryption_failed') card.append(node('p', uiLanguage.t('admin.restore_the_original_master_key_and_version_if_recovery_is_impossible_delete_and_register_the_key_ag')));
    if (p.error_code) card.append(node('p', uiLanguage.t('admin.latest_non_generation_verification_failed_check_connection_settings_and_access_permissions')));
    const row = node('div', null, {class:'ai-actions'});
    row.append(button(p.key_registered ? uiLanguage.t('admin.replace_key') : uiLanguage.t('admin.save_key'), () => actions.connection(p, 'key'), !state.master_key_available || p.status === 'decryption_failed', 'primary'),
      button(p.enabled ? uiLanguage.t('admin.disable') : uiLanguage.t('admin.re_enable'), () => actions.connection(p, 'enabled'), !p.key_registered || (!p.enabled && (!state.master_key_available || p.status === 'decryption_failed')), 'secondary'),
      button(uiLanguage.t('admin.delete_key'), () => actions.connection(p, 'delete'), !p.key_registered, 'danger'),
      button(uiLanguage.t('admin.verify_without_generation_and_refresh_catalog'), () => actions.catalog(p), !p.enabled || p.status !== 'ready' || !state.master_key_available || p.catalog.available === false, 'secondary'));
    card.append(row, node('p', p.catalog.available === false ? uiLanguage.t('admin.the_model_catalog_is_not_available_yet') : !p.catalog.fetched_at && !p.catalog.stale ? uiLanguage.t('admin.model_catalog_no_lookup_recorded') : `${uiLanguage.t('admin.model_catalog')} ${p.catalog.stale ? uiLanguage.t('admin.stale_catalog') : uiLanguage.t('admin.recent_catalog')} · ${uiLanguage.t('admin.last_refreshed')} ${p.catalog.fetched_at || uiLanguage.t('admin.none')}`));
    if (p.catalog.stale) card.append(node('p', uiLanguage.t('admin.this_catalog_is_for_reference_it_does_not_guarantee_access_or_successful_role_verification')));
    const choices = [['', uiLanguage.t('admin.select_a_model')], ...p.catalog.models.map(model => [model.model_id,
      `${model.model_id} · ${model.lifecycle || uiLanguage.t('admin.lifecycle_unknown')} · ${model.preview === true ? 'Preview' : model.preview === false ? uiLanguage.t('admin.stable') : uiLanguage.t('admin.release_status_unknown')}`])];
    const select = field(card, `${p.provider}-catalog-model`, `${providers[p.provider]} ${uiLanguage.t('admin.choose_model_id')}`, '', {}, choices);
    const register = button(uiLanguage.t('admin.register_selected_model'), () => actions.register(p, select.value), true, 'primary');
    select.addEventListener('change', () => { register.disabled = !select.value || state.models_available === false; });
    card.append(register, button(uiLanguage.t('admin.enter_model_id_manually'), () => actions.register(p, ''), state.models_available === false, 'secondary'));
    cards.append(card);
  }
  connections.append(cards);
  root.append(connections);
  const models = node('section', null, {'aria-label':uiLanguage.t('admin.model_settings')});
  models.append(node('h2', uiLanguage.t('admin.2_model_settings_and_role_verification')), node('p', uiLanguage.t('admin.check_response_format_verification_separately_for_each_role_test_success_does_not_guarantee_educatio')));
  if (!state.models.length && state.models_available !== false) models.append(node('p', uiLanguage.t('admin.no_registered_models_choose_a_catalog_entry_under_a_provider_connection_or_enter_a_model_id_manually')));
  if (state.models_available === false) models.append(node('p', uiLanguage.t('admin.model_registration_and_role_tests_are_not_available_yet')));
  if (state.probes_available === false) models.append(node('p', uiLanguage.t('admin.role_tests_are_not_available_yet')));
  for (const m of state.models) {
    const p = state.providers.find(p => p.provider === m.provider);
    const card = node('article', null, {'data-model':m.id, 'aria-labelledby':`ai-model-${m.id}`});
    card.append(node('h3', `${m.display_name} · ${providers[m.provider]} / ${m.model_id}`, {id:`ai-model-${m.id}`}),
      node('p', `${m.capabilities?.preview ? 'Preview · ' : ''}${m.enabled ? uiLanguage.t('admin.active') : uiLanguage.t('admin.inactive')} · ${m.capabilities ? uiLanguage.t('admin.capability_definition_available') : uiLanguage.t('admin.capability_definition_required')}`),
      button(uiLanguage.t('admin.edit_display_name_activation_and_options'), () => actions.model(m), false, 'secondary'));
    if (m.capabilities?.metadata_conflict) card.append(node('p', uiLanguage.t('admin.capability_definition_and_provider_catalog_do_not_match_role_tests_and_execution_are_unavailable')));
    const roleGrid = node('div', null, {class:'ai-role-grid'});
    for (const [role, label] of Object.entries(roles)) {
      const verification = m.verification_state[role];
      const row = node('div', null, {class:'ai-role', role:'group', 'aria-label':`${m.display_name} · ${label} ${uiLanguage.t('admin.role_verification')}`});
      const heading = node('p', `${label}: `);
      heading.append(node('span', states[verification.status] || uiLanguage.t('admin.unverified'), {class:`ai-verification ai-verification-${verification.status}`}));
      row.append(heading);
      if (verification.verified_at) row.append(node('p', `${uiLanguage.t('admin.latest_role_verification')} ${verification.verified_at}`));
      if (verification.error_code) row.append(node('p', roleErrors[verification.error_code] || uiLanguage.t('admin.the_role_response_format_test_failed_check_settings_and_test_again')));
      if (m.capabilities && !m.probe_budgets?.[role]) row.append(node('p', `${label} ${uiLanguage.t('admin.the_test_output_limit_conflicts_with_saved_options_explicitly_update_the_saved_options')}`));
      const reason = state.probes_available === false || (state.probe_roles && !state.probe_roles.includes(role)) ? uiLanguage.t('admin.this_role_test_is_not_available_yet')
        : !m.capabilities ? uiLanguage.t('admin.role_tests_require_a_capability_definition')
        : m.capabilities.metadata_conflict ? uiLanguage.t('admin.resolve_the_capability_definition_and_provider_catalog_mismatch_before_testing')
        : !state.master_key_available ? uiLanguage.t('admin.restore_the_master_key_before_testing')
        : p.status === 'decryption_failed' ? uiLanguage.t('admin.resolve_the_connection_key_decryption_failure_before_testing')
        : !p.key_registered ? uiLanguage.t('admin.register_a_provider_key_before_testing')
        : !p.enabled ? uiLanguage.t('admin.re_enable_the_provider_connection_before_testing')
        : p.status !== 'ready' ? uiLanguage.t('admin.check_provider_connection_settings_before_testing')
        : !m.probe_budgets?.[role] ? uiLanguage.t('admin.update_the_test_output_limit_and_saved_options_before_testing')
        : verification.status === 'verifying' ? uiLanguage.t('admin.you_can_check_or_cancel_the_running_test') : '';
      const trial = button(`${label} ${uiLanguage.t('admin.test')}`, () => actions.probe(m, role), !!reason, 'primary');
      if (reason) {
        const id = `ai-probe-reason-${m.id}-${role}`;
        row.append(node('p', reason, {id, class:'ai-help'}));
        trial.setAttribute('aria-describedby', id);
      }
      row.append(trial);
      if (verification.status === 'verifying') row.append(button(`${label} ${uiLanguage.t('admin.check_progress')}`, () => actions.poll(verification.probe_request_id), false, 'secondary'), button(`${label} ${uiLanguage.t('admin.cancel_test')}`, () => actions.cancel(verification.probe_request_id), false, 'secondary'));
      roleGrid.append(row);
    }
    card.append(roleGrid);
    models.append(card);
  }
  root.append(models);
  const settings = node('section', null, {'aria-label':uiLanguage.t('admin.authoring_defaults_and_call_settings')});
  settings.append(node('h2', uiLanguage.t('admin.3_authoring_defaults_and_call_settings')), node('p', uiLanguage.t('admin.authoring_defaults_apply_only_to_new_scenarios_existing_scenarios_and_sessions_remain_unchanged_unav')));
  if (!state.settings) {
    settings.append(node('p', uiLanguage.t('admin.authoring_defaults_and_call_settings_are_not_available_yet')));
    root.append(settings);
    return;
  }
  for (const [role, label] of Object.entries(roles)) {
    const d = state.settings.defaults[role];
    const m = state.models.find(m => m.id === d?.model_config_id);
    settings.append(node('p', `${label}: ${d ? `${m?.display_name || d.model_config_id} · ${d.available ? uiLanguage.t('admin.available') : uiLanguage.t('admin.default_model_unavailable')}` : uiLanguage.t('admin.no_default')}`));
  }
  settings.append(button(uiLanguage.t('admin.edit_authoring_defaults_and_call_settings'), () => actions.settings(state), false, 'secondary'));
  root.append(settings);
}
