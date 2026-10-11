import {node, field, providers, roles} from './ai-render.js';

const states = {unverified:'미검증', verifying:'검증 중', succeeded:'성공', failed:'실패', stale:'재검증 필요'};

export function mountScenarioModels(data, onChange) {
  const selections = {};
  for (const [role, label] of Object.entries(roles)) {
    const select = document.getElementById(`${role}.resolved_model_config`);
    const options = document.getElementById(`${role}-model-options`);
    const status = document.getElementById(`${role}-model-status`);
    let saved = data.config[role].resolved_model_config;
    const defaultModel = data.role_defaults?.[role];
    if (data.id == null && !data.comparison_draft_id && saved == null && defaultModel?.available) {
      const model = data.model_choices.find(m => m.id === defaultModel.model_config_id);
      if (model) saved = identity(model);
    }
    const control = mountScenarioModel(data, role, label, {select, options, status}, saved, value => {
      selections[role] = value;
      onChange();
    }, defaultModel);
    selections[role] = control.selection;
    select.addEventListener('model:apply', event => control.set(event.detail));
  }
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
    status.textContent = `${reasons.join(' · ')}${changed ? ' · 모델 변경: 새 기본 옵션을 표시했습니다.' : ''}`;
    if (!selection) return;
    if (!model?.capabilities) {
      options.append(node('pre', JSON.stringify(selection.options, null, 2)));
      return;
    }
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
