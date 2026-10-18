import {node, field, providers, roles, optionNotes} from './ai-render.js';

const states = {unverified:uiLanguage.t('admin.unverified'), verifying:uiLanguage.t('admin.verifying'), succeeded:uiLanguage.t('admin.succeeded'), failed:uiLanguage.t('admin.failed'), stale:uiLanguage.t('admin.reverification_required')};

export function commonModelReason(model, activeRoles) {
  if (!model.enabled) return uiLanguage.t('admin.inactive');
  if (!model.connection_available) return uiLanguage.t('admin.connection_unavailable');
  if (!model.capabilities || model.capabilities.metadata_conflict) return uiLanguage.t('admin.capability_definition_required');
  const unavailable = activeRoles.filter(role => model.verification_state?.[role]?.status !== 'succeeded');
  return unavailable.map(role => `${roles[role]} ${states[model.verification_state?.[role]?.status] || uiLanguage.t('admin.unverified')}`).join(' · ');
}

export function mountScenarioModels(data, onChange) {
  const selections = {}, controls = {};
  const common = document.getElementById('common-model');
  const note = document.getElementById('common-model-status');
  const mode = document.getElementById('mentor.mode');
  const activeRoles = () => ['student', 'analysis', ...(mode.value === 'off' ? [] : ['mentor'])];
  let initialized = false;
  function identityKey(value) {
    return value ? JSON.stringify([value.model_config_id, value.provider_connection_id, value.provider, value.model_id]) : null;
  }
  function availability() {
    const active = activeRoles();
    const values = active.map(role => identityKey(selections[role]));
    const mixed = !values[0] || values.some(value => value !== values[0]);
    common.replaceChildren(node('option', uiLanguage.t('admin.select_a_common_model'), {value:''}));
    for (const model of data.model_choices) {
      const reason = commonModelReason(model, active);
      const option = node('option', `${providers[model.provider]} / ${model.display_name}${reason ? ` · ${reason}` : ''}`, {value:model.id});
      option.disabled = Boolean(reason);
      common.append(option);
    }
    common.value = mixed ? '' : selections.student.model_config_id;
    if (!data.model_choices.length) {
      note.replaceChildren(uiLanguage.t('admin.registered_and_verified_model_required'), ' ', node('a', uiLanguage.t('admin.ai_connections_and_models'), {href:'/admin/ai'}));
    } else {
      note.textContent = mixed ? uiLanguage.t('admin.existing_per_role_models_are_preserved_explicitly_select_a_common_model_before_saving') : uiLanguage.t('admin.all_active_roles_use_the_same_model_options_are_preserved_per_role');
    }
    for (const role of active) {
      for (const input of document.getElementById(`${role}-model-options`).querySelectorAll('input,select,button')) input.disabled = mixed;
    }
  }
  for (const [role, label] of Object.entries(roles)) {
    const select = document.getElementById(`${role}.resolved_model_config`);
    controls[role] = mountScenarioModel(data, role, label, {
      select, options:document.getElementById(`${role}-model-options`), status:document.getElementById(`${role}-model-status`)
    }, data.config[role].resolved_model_config, value => {
      selections[role] = value;
      if (initialized) onChange();
    });
    selections[role] = controls[role].selection;
    select.disabled = true;
    select.hidden = true;
    // Legacy application remains explicit; a normal save still validates common identity.
    select.addEventListener('model:apply', event => {
      const model = data.model_choices.find(model => model.id === event.detail.model_config_id);
      for (const active of activeRoles()) controls[active].set(active === role ? event.detail : identity(model));
      if (!activeRoles().includes(role)) controls[role].set(event.detail);
      availability();
    });
  }
  common.addEventListener('change', () => {
    const model = data.model_choices.find(model => String(model.id) === common.value);
    if (!model || commonModelReason(model, activeRoles())) return;
    for (const role of activeRoles()) controls[role].set(identity(model), true);
    availability();
  });
  common.addEventListener('model:apply', event => {
    const {identity:selection, role_options:options} = event.detail;
    for (const role of activeRoles()) controls[role].set({...selection, options:options[role]});
    availability();
  });
  mode.addEventListener('change', availability);
  if (data.id == null && !data.comparison_draft_id) {
    const model = data.model_choices.find(model => model.id === data.role_defaults?.student?.model_config_id);
    if (model && !commonModelReason(model, activeRoles())) {
      for (const role of activeRoles()) controls[role].set(identity(model));
    }
  }
  availability();
  initialized = true;
  return selections;
}

export function mountScenarioModel(data, role, label, {select, options, status}, saved, onChange, defaultModel = null) {
  let current = structuredClone(saved);
  select.append(node('option', uiLanguage.t('admin.select_a_model'), {value:''}));
  for (const model of data.model_choices) select.append(node('option', `${providers[model.provider]} / ${model.display_name}`, {value:model.id}));
  if (saved && !data.model_choices.some(m => m.id === saved.model_config_id)) select.append(node('option', `${uiLanguage.t('admin.unavailable')} · ${saved.model_id}`, {value:saved.model_config_id}));
  select.value = saved?.model_config_id ?? '';
  function render(changed = false) {
    const selection = current;
    const model = data.model_choices.find(m => m.id === selection?.model_config_id);
    options.replaceChildren();
    const reasons = [];
    if (!selection) reasons.push(defaultModel && !defaultModel.available ? uiLanguage.t('admin.default_model_unavailable_select_a_model') : uiLanguage.t('admin.select_a_model'));
    else if (!model) reasons.push(uiLanguage.t('admin.registered_model_unavailable'));
    else {
      reasons.push(states[model.verification_state[role].status] || uiLanguage.t('admin.unverified'));
      if (!model.enabled) reasons.push(uiLanguage.t('admin.inactive'));
      if (!model.connection_available) reasons.push(uiLanguage.t('admin.connection_unavailable'));
      if (!model.capabilities) reasons.push(uiLanguage.t('admin.capability_definition_required'));
      if (model.capabilities?.metadata_conflict) reasons.push(uiLanguage.t('admin.capability_definition_and_provider_catalog_mismatch'));
    }
    status.textContent = `${selection ? `${providers[selection.provider]} / ${selection.model_id} · ` : ''}${reasons.join(' · ')}${changed ? ` · ${uiLanguage.t('admin.model_changed_new_default_options_displayed')}` : ''}`;
    if (!selection) return;
    if (!model?.capabilities) {
      options.append(node('pre', JSON.stringify(selection.options, null, 2)));
      return;
    }
    optionNotes(options, model.capabilities, selection.options, value => {
      selection.options = value;
      onChange(current);
    });
    for (const definition of model.capabilities.fields) {
      const value = definition.name.split('.').reduce((obj, key) => obj?.[key], selection.options);
      const numeric = ['integer', 'number'].includes(definition.type);
      const attrs = {type:numeric ? 'number' : 'text'};
      if (definition.min != null) attrs.min = definition.min;
      if (definition.max != null) attrs.max = definition.max;
      if (numeric) attrs.step = definition.type === 'integer' ? '1' : 'any';
      const input = field(options, `${select.id}-${definition.name}`, `${label} ${definition.label}`, value, attrs, definition.choices ? [['',uiLanguage.t('admin.unspecified')], ...definition.choices.map(v => [v,v])] : null);
      input.dataset.field = `${select.id}.options.${definition.name}`;
      const errorId = `${input.dataset.field}-error`;
      input.setAttribute('aria-describedby', errorId);
      input.parentElement.append(node('p', null, {id:errorId, class:'scenario-error', hidden:''}));
      input.addEventListener('input', () => {
        const path = definition.name.split('.');
        let target = selection.options;
        for (const part of path.slice(0, -1)) target = target[part] ||= {};
        if (input.value === '') {
          delete target[path.at(-1)];
          if (path.length > 1 && Object.keys(target).length === 0) delete selection.options[path[0]];
        } else target[path.at(-1)] = numeric ? Number(input.value) : input.value;
        onChange(current);
      });
    }
  }
  function set(value, changed = false) {
    current = structuredClone(value);
    select.value = current?.model_config_id ?? '';
    render(changed);
    onChange(current);
  }
  select.addEventListener('change', () => {
    const model = data.model_choices.find(m => String(m.id) === select.value);
    set(model ? identity(model) : null, true);
  });
  render();
  return {get selection() { return current; }, set};
}

export function identity(model) {
  return {model_config_id:model.id, provider_connection_id:model.provider_connection_id, provider:model.provider,
    model_id:model.model_id, options:structuredClone(model.default_options)};
}
