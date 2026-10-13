import {node, field, providers, roles, optionNotes} from './ai-render.js';

const states = {unverified:'미검증', verifying:'검증 중', succeeded:'성공', failed:'실패', stale:'재검증 필요'};

export function commonModelReason(model, activeRoles) {
  if (!model.enabled) return '비활성';
  if (!model.connection_available) return '연결 사용 불가';
  if (!model.capabilities || model.capabilities.metadata_conflict) return '기능 정의 필요';
  const unavailable = activeRoles.filter(role => model.verification_state?.[role]?.status !== 'succeeded');
  return unavailable.map(role => `${roles[role]} ${states[model.verification_state?.[role]?.status] || '미검증'}`).join(' · ');
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
    common.replaceChildren(node('option', '공통 모델 선택 필요', {value:''}));
    for (const model of data.model_choices) {
      const reason = commonModelReason(model, active);
      const option = node('option', `${providers[model.provider]} / ${model.display_name}${reason ? ` · ${reason}` : ''}`, {value:model.id});
      option.disabled = Boolean(reason);
      common.append(option);
    }
    common.value = mixed ? '' : selections.student.model_config_id;
    note.textContent = mixed ? '기존 역할별 모델을 보존했습니다. 공통 모델을 명시적으로 선택해야 저장할 수 있습니다.' : '모든 활성 역할에 같은 모델을 사용합니다. 옵션은 역할별로 보존합니다.';
    for (const role of active) {
      for (const input of document.getElementById(`${role}-model-options`).querySelectorAll('input,select')) input.disabled = mixed;
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
  select.append(node('option', '모델 선택 필요', {value:''}));
  for (const model of data.model_choices) select.append(node('option', `${providers[model.provider]} / ${model.display_name}`, {value:model.id}));
  if (saved && !data.model_choices.some(m => m.id === saved.model_config_id)) select.append(node('option', `사용 불가 · ${saved.model_id}`, {value:saved.model_config_id}));
  select.value = saved?.model_config_id ?? '';
  function render(changed = false) {
    const selection = current;
    const model = data.model_choices.find(m => m.id === selection?.model_config_id);
    options.replaceChildren();
    const reasons = [];
    if (!selection) reasons.push(defaultModel && !defaultModel.available ? '기본 모델 사용 불가 · 모델 선택 필요' : '모델 선택 필요');
    else if (!model) reasons.push('등록 모델 사용 불가');
    else {
      reasons.push(states[model.verification_state[role].status] || '미검증');
      if (!model.enabled) reasons.push('비활성');
      if (!model.connection_available) reasons.push('연결 사용 불가');
      if (!model.capabilities) reasons.push('기능 정의 필요');
      if (model.capabilities?.metadata_conflict) reasons.push('기능 정의·제공자 목록 불일치');
    }
    status.textContent = `${selection ? `${providers[selection.provider]} / ${selection.model_id} · ` : ''}${reasons.join(' · ')}${changed ? ' · 모델 변경: 새 기본 옵션을 표시했습니다.' : ''}`;
    if (!selection) return;
    if (!model?.capabilities) {
      options.append(node('pre', JSON.stringify(selection.options, null, 2)));
      return;
    }
    optionNotes(options, model.capabilities, selection.options);
    for (const definition of model.capabilities.fields) {
      const value = definition.name.split('.').reduce((obj, key) => obj?.[key], selection.options);
      const numeric = ['integer', 'number'].includes(definition.type);
      const attrs = {type:numeric ? 'number' : 'text'};
      if (definition.min != null) attrs.min = definition.min;
      if (definition.max != null) attrs.max = definition.max;
      if (numeric) attrs.step = definition.type === 'integer' ? '1' : 'any';
      const input = field(options, `${select.id}-${definition.name}`, `${label} ${definition.label}`, value, attrs, definition.choices ? [['','지정하지 않음'], ...definition.choices.map(v => [v,v])] : null);
      input.dataset.field = `${select.id}.options.${definition.name}`;
      const errorId = `${input.dataset.field}-error`;
      input.setAttribute('aria-describedby', errorId);
      options.append(node('p', null, {id:errorId, class:'scenario-error', hidden:''}));
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
