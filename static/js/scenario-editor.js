import {node, button, field, roles} from './ai-render.js';
import {mountComparison} from './scenario-comparison.js';
import {mountScenarioModels} from './scenario-models.js';

const data = JSON.parse(document.getElementById('scenario-editor-data').textContent);
const get = path => path.split('.').reduce((value, key) => value?.[key], data.config) ?? data[path] ?? '';
const fields = [...document.querySelectorAll('[data-field]')];
const form = document.getElementById('scenario-form');
const status = document.getElementById('editor-status');
const publish = document.getElementById('publish-scenario');
let dirty = Boolean(data.comparison_draft_id), busy = false, edits = 0;
uiLanguage.registerGuard(() => ({dirty, busy}));
function markDirty() { dirty = true; edits++; preview(); }
for (const input of fields) {
  if (input.type === 'checkbox') input.checked = get(input.dataset.field);
  else input.value = get(input.dataset.field);
}
document.getElementById('runtime.context_turn_limit').max = 100;
for (const input of document.querySelectorAll('[name="groups"]')) input.checked = data.groups.includes(Number(input.value));

let rubric = structuredClone(data.config.analysis.rubric);
function renderRubric() {
  const root = document.getElementById('rubric-rows');
  root.replaceChildren();
  rubric.forEach((row, index) => {
    const container = node('fieldset');
    container.append(node('legend', `${uiLanguage.t('admin.classification')} ${index + 1}`));
    for (const [key, label, limit] of [['id', 'ID', 64], ['name', uiLanguage.t('admin.name'), 100], ['criteria', uiLanguage.t('admin.criteria'), 50000], ['level', uiLanguage.t('admin.level')]]) {
      const input = field(container, `rubric-${index}-${key}`, `${uiLanguage.t('admin.classification')} ${index + 1} ${label}`, row[key], key === 'level' ? {} : {maxlength:limit}, key === 'level' ? [['', uiLanguage.t('admin.unspecified')], ['high', uiLanguage.t('admin.high_level')], ['low', uiLanguage.t('admin.low_level')]] : null);
      input.dataset.field = `analysis.rubric.${index}.${key}`;
      input.addEventListener('input', () => { row[key] = input.value || (key === 'level' ? null : ''); });
      const errorId = `${input.dataset.field}-error`;
      input.setAttribute('aria-describedby', errorId);
      container.append(node('p', null, {id:errorId, class:'scenario-error', hidden:''}));
    }
    container.append(button(`${uiLanguage.t('admin.classification')} ${index + 1} ${uiLanguage.t('admin.delete')}`, () => {
      if ((row.id || row.name || row.criteria) && !window.confirm(uiLanguage.t('admin.delete_this_rubric_item'))) return;
      rubric.splice(index, 1);
      renderRubric();
      document.getElementById('add-rubric').focus();
      markDirty();
    }));
    root.append(container);
  });
  document.getElementById('add-rubric').disabled = rubric.length >= 20;
}
document.getElementById('add-rubric').addEventListener('click', () => {
  rubric.push({id:'', name:'', criteria:'', level:null});
  renderRubric();
  document.querySelector('#rubric-rows fieldset:last-child input').focus();
  markDirty();
});

let step = 0;
function showStep(next) {
  step = next;
  document.querySelectorAll('[data-panel]').forEach(panel => { panel.hidden = Number(panel.dataset.panel) !== step; });
  document.querySelectorAll('[data-step]').forEach(button => {
    if (Number(button.dataset.step) === step) button.setAttribute('aria-current', 'step');
    else button.removeAttribute('aria-current');
  });
  document.getElementById('previous-step').disabled = step === 0;
  document.getElementById('next-step').disabled = step === 4;
  document.getElementById('step-position').textContent = `${step + 1} / 5`;
}
document.querySelectorAll('[data-step]').forEach(button => button.addEventListener('click', () => showStep(Number(button.dataset.step))));
for (const [id, offset] of [['previous-step', -1], ['next-step', 1]]) {
  document.getElementById(id).addEventListener('click', () => {
    showStep(step + offset);
    document.querySelector(`[data-panel="${step}"] h2`).focus();
  });
}
function preview() {
  const mode = document.getElementById('mentor.mode').value;
  const classification = document.getElementById('analysis.classification_enabled').checked;
  document.getElementById('mentor-settings').hidden = mode === 'off';
  document.getElementById('mentor-auto-settings').hidden = mode !== 'auto';
  document.getElementById('rubric-settings').hidden = !classification;
  document.getElementById('mentor-mode-note').textContent = {off:uiLanguage.t('admin.mentor_disabled_hidden_input_is_preserved'), manual:uiLanguage.t('admin.request_help_after_a_completed_student_turn_manual_requests_have_no_total_count_cap'), auto:uiLanguage.t('admin.check_conditions_after_completed_turns_the_help_button_is_also_available')}[mode];
  document.querySelectorAll('[data-preview]').forEach(el => {
    const path = data.public_lesson_fields[el.dataset.preview];
    const hidden = ['mentor_name', 'greeting_message'].includes(el.dataset.preview) && mode === 'off';
    el.textContent = hidden ? '' : document.getElementById(path).value;
    el.hidden = hidden;
  });
  document.getElementById('execution-summary').textContent = `${Object.entries(models).map(([role, model]) => `${roles[role]}: ${model ? `${model.provider} / ${model.model_id}` : uiLanguage.t('admin.selection_required')}`).join('\n')}\n${uiLanguage.t('admin.mentor')} ${document.getElementById('mentor.mode').selectedOptions[0].textContent} · ${uiLanguage.t('admin.classification')} ${classification ? uiLanguage.t('admin.item_count', {count:rubric.length}) : uiLanguage.t('admin.disabled')}`;
}
form.addEventListener('input', markDirty);
form.addEventListener('change', markDirty);
renderRubric();
const models = mountScenarioModels(data, markDirty);
showStep(0);
preview();

function publicationStatus() {
  status.textContent = `${uiLanguage.t('admin.current_status')} ${data.status === 'published' ? uiLanguage.t('admin.published') : uiLanguage.t('admin.draft')} · ${uiLanguage.t('admin.version')} ${data.config_version}`;
  publish.textContent = data.status === 'published' ? uiLanguage.t('admin.publish_changes') : uiLanguage.t('admin.publish');
}
publicationStatus();

function payload(action) {
  const config = structuredClone(data.config);
  const result = {title:'', subject:'', target_grade:'', config_schema_version:data.config_schema_version, config, action,
    groups:[...document.querySelectorAll('[name="groups"]:checked')].map(input => Number(input.value))};
  if (data.id != null) result.expected_version = data.config_version;
  for (const input of fields) {
    const path = input.dataset.field.split('.');
    const value = input.type === 'checkbox' ? input.checked : input.type === 'number' ? Number(input.value) : input.value;
    if (path.length === 1) result[path[0]] = value;
    else {
      let target = config;
      for (const part of path.slice(0, -1)) target = target[part];
      target[path.at(-1)] = value;
    }
  }
  for (const role of Object.keys(models)) config[role].resolved_model_config = structuredClone(models[role]);
  config.analysis.rubric = structuredClone(rubric);
  if (data.review_required) result.acknowledge_review = document.getElementById('acknowledge-review').checked;
  return result;
}

function showErrors(errors) {
  const summary = document.getElementById('editor-errors');
  const links = document.getElementById('editor-error-links');
  links.replaceChildren();
  document.querySelectorAll('[aria-invalid]').forEach(input => input.removeAttribute('aria-invalid'));
  document.querySelectorAll('.scenario-error').forEach(el => { el.hidden = true; });
  const counts = [0, 0, 0, 0, 0];
  for (const error of errors) {
    const path = error.path.replace(/^config\./, '');
    const input = document.getElementById(path) || [...document.querySelectorAll('[data-field]')].find(el => el.dataset.field === path) || (path === 'analysis.rubric' ? document.getElementById('add-rubric') : null);
    const panel = input?.closest('[data-panel]');
    const message = typeof error.message === 'string' ? error.message : uiLanguage.t('admin.check_your_input');
    if (panel) counts[Number(panel.dataset.panel)]++;
    if (input) input.setAttribute('aria-invalid', 'true');
    if (path.endsWith('resolved_model_config')) document.getElementById('common-model').setAttribute('aria-invalid', 'true');
    const inline = document.getElementById(`${path}-error`);
    if (inline) { inline.textContent = message; inline.hidden = false; }
    const link = button(message, () => {
      if (panel) showStep(Number(panel.dataset.panel));
      let target = input || summary;
      if (path.endsWith('resolved_model_config') || input?.disabled) target = document.getElementById('common-model');
      else if (input?.closest('#rubric-settings[hidden]')) target = document.getElementById('analysis.classification_enabled');
      else if (input?.closest('#mentor-settings[hidden], #mentor-auto-settings[hidden]')) target = document.getElementById('mentor.mode');
      target.focus();
    });
    const item = node('li');
    item.append(link);
    links.append(item);
  }
  document.querySelectorAll('[data-errors]').forEach(badge => {
    const count = counts[Number(badge.dataset.errors)];
    badge.textContent = count;
    badge.setAttribute('aria-label', `${uiLanguage.t('admin.items_to_complete')} ${uiLanguage.t('admin.item_count', {count})}`);
    badge.hidden = count === 0;
  });
  document.getElementById('validation-summary').textContent = `${uiLanguage.t('admin.items_to_complete')} ${uiLanguage.t('admin.item_count', {count:errors.length})}`;
  summary.hidden = errors.length === 0;
  if (errors.length) summary.focus();
}

async function save(action) {
  if (busy) return;
  if (action === 'save_draft' && data.status === 'published' && !window.confirm(uiLanguage.t('admin.changing_to_a_draft_prevents_teachers_from_starting_new_sessions_save_as_a_draft'))) return;
  const wasDirty = dirty;
  dirty = true;
  busy = true;
  publicationStatus();
  delete status.dataset.state;
  publish.disabled = true;
  document.getElementById('save-draft').disabled = true;
  document.getElementById('editor-conflict').hidden = true;
  showErrors([]);
  const submittedEdits = edits;
  try {
    const response = await fetch(data.id == null ? '/admin/scenarios' : `/admin/scenarios/${data.id}/update`, {
      method:'POST', credentials:'same-origin', headers:uiLanguage.headers('/admin/scenarios', {'Content-Type':'application/json', 'x-csrf-token':document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/)?.[1] || ''}), body:JSON.stringify(payload(action))
    });
    if (response.status === 422) {
      const result = await response.json();
      showErrors(result.detail);
    } else if (response.status === 409) {
      const result = await response.json();
      const conflict = document.getElementById('editor-conflict');
      document.getElementById('editor-conflict-message').textContent = `${uiLanguage.t('admin.another_administrator_made_changes_your_input_is_preserved_latest_version')} ${result.detail.current_version}`;
      conflict.hidden = false;
      conflict.focus();
    } else if (response.ok) {
      const result = await response.json();
      data.id = result.id ?? data.id;
      data.config_version = result.version;
      data.status = result.status;
      if (window.location.pathname === '/admin/scenarios/new') window.history.replaceState(null, '', `/admin/scenarios/${data.id}/edit`);
      dirty = edits !== submittedEdits;
      data.review_required = result.review_required ?? (action === 'publish' ? false : data.review_required);
      data.review_reasons = result.review_reasons ?? (action === 'publish' ? [] : data.review_reasons);
      const review = document.getElementById('conversion-review');
      if (review) {
        review.hidden = !data.review_required;
        document.getElementById('acknowledge-review').checked = false;
        if (data.review_required) showErrors(data.review_reasons);
      }
      publicationStatus();
      status.dataset.state = 'success';
      status.textContent = action === 'save_draft' ? uiLanguage.t('admin.draft_saved') : uiLanguage.t('admin.publication_succeeded');
    } else {
      const messages = {401:uiLanguage.t('admin.please_log_in'), 403:uiLanguage.t('admin.check_your_permissions_or_security_token'), 404:uiLanguage.t('admin.scenario_not_found')};
      status.textContent = messages[response.status] || uiLanguage.t('admin.could_not_save_your_input_is_preserved');
    }
    if (!response.ok) dirty = wasDirty || edits !== submittedEdits;
  } catch {
    status.dataset.state = 'uncertain';
    status.textContent = uiLanguage.t('admin.could_not_confirm_the_save_result_your_input_is_preserved');
  } finally {
    busy = false;
    reviewState();
    document.getElementById('save-draft').disabled = false;
  }
}
form.addEventListener('submit', event => { event.preventDefault(); save('publish'); });
document.getElementById('save-draft').addEventListener('click', () => save('save_draft'));
document.getElementById('editor-reload').addEventListener('click', () => {
  if (!window.confirm(uiLanguage.t('admin.reloading_the_latest_version_discards_unsaved_input_reload'))) return;
  dirty = false;
  window.location.reload();
});
window.addEventListener('beforeunload', event => {
  if (!dirty) return;
  event.preventDefault();
  event.returnValue = '';
});

function reviewState() {
  const blocked = data.review_required && data.review_reasons.some(reason => reason.blocking);
  const acknowledged = !data.review_required || document.getElementById('acknowledge-review').checked;
  publish.disabled = busy || data.publication_available === false || blocked || !acknowledged;
  const note = document.getElementById('review-publication-status');
  if (note) note.textContent = blocked ? uiLanguage.t('admin.cannot_publish_complete_required_fields_and_validate_again_on_the_server') : acknowledged ? uiLanguage.t('admin.review_acknowledged_required_content_and_models_will_be_validated_again_on_publication') : uiLanguage.t('admin.cannot_publish_acknowledge_the_conversion_warnings_first');
}
if (data.review_required) {
  showErrors(data.review_reasons);
  document.getElementById('acknowledge-review').addEventListener('change', () => { markDirty(); reviewState(); });
}
reviewState();
if (data.model_handoff) {
  document.getElementById('common-model').dispatchEvent(new CustomEvent('model:apply', {detail:data.model_handoff}));
  status.textContent = uiLanguage.t('admin.comparison_model_and_per_role_options_applied_save_or_publish_separately');
}

mountComparison(data, () => payload('compare'));
