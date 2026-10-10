import {node, button, field} from './ai-render.js';
import {mountScenarioModels} from './scenario-models.js';

const data = JSON.parse(document.getElementById('scenario-editor-data').textContent);
const get = path => path.split('.').reduce((value, key) => value?.[key], data.config) ?? data[path] ?? '';
const fields = [...document.querySelectorAll('[data-field]')];
const form = document.getElementById('scenario-form');
const status = document.getElementById('editor-status');
const publish = document.getElementById('publish-scenario');
let dirty = false, busy = false, edits = 0;
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
    container.append(node('legend', `분류 ${index + 1}`));
    for (const [key, label, limit] of [['id', 'ID', 64], ['name', '이름', 100], ['criteria', '판정 기준', 50000], ['level', '수준']]) {
      const input = field(container, `rubric-${index}-${key}`, `분류 ${index + 1} ${label}`, row[key], key === 'level' ? {} : {maxlength:limit}, key === 'level' ? [['', '지정하지 않음'], ['high', 'high'], ['low', 'low']] : null);
      input.dataset.field = `analysis.rubric.${index}.${key}`;
      input.addEventListener('input', () => { row[key] = input.value || (key === 'level' ? null : ''); });
      const errorId = `${input.dataset.field}-error`;
      input.setAttribute('aria-describedby', errorId);
      container.append(node('p', null, {id:errorId, class:'scenario-error', hidden:''}));
    }
    container.append(button(`분류 ${index + 1} 삭제`, () => {
      if ((row.id || row.name || row.criteria) && !window.confirm('이 분류 기준을 삭제하시겠습니까?')) return;
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
  document.getElementById('mentor-mode-note').textContent = {off:'멘토를 사용하지 않습니다. 숨긴 입력은 보존합니다.', manual:'완료된 학생 턴에서 도움 버튼으로 요청합니다. 수동 총횟수 상한은 없습니다.', auto:'완료 턴 뒤 조건을 검사합니다. 도움 버튼으로도 요청할 수 있습니다.'}[mode];
  document.querySelectorAll('[data-preview]').forEach(el => {
    const path = data.public_lesson_fields[el.dataset.preview];
    const hidden = ['mentor_name', 'greeting_message'].includes(el.dataset.preview) && mode === 'off';
    el.textContent = hidden ? '' : document.getElementById(path).value;
    el.hidden = hidden;
  });
  document.getElementById('execution-summary').textContent = `${Object.entries(models).map(([role, model]) => `${role}: ${model ? `${model.provider} / ${model.model_id}` : '선택 필요'}`).join('\n')}\n멘토 ${mode} · 분류 ${classification ? `${rubric.length}개` : '미사용'}`;
}
form.addEventListener('input', markDirty);
form.addEventListener('change', markDirty);
renderRubric();
const models = mountScenarioModels(data, markDirty);
showStep(0);
preview();

function publicationStatus() {
  status.textContent = `현재 상태: ${data.status === 'published' ? '게시됨' : '초안'} · 버전 ${data.config_version}`;
  publish.textContent = data.status === 'published' ? '수정 후 게시' : '게시';
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
    const input = document.getElementById(path) || [...document.querySelectorAll('[data-field]')].find(el => el.dataset.field === path);
    const panel = input?.closest('[data-panel]');
    const message = typeof error.message === 'string' ? error.message : '입력값을 확인하세요.';
    if (panel) counts[Number(panel.dataset.panel)]++;
    if (input) input.setAttribute('aria-invalid', 'true');
    const inline = document.getElementById(`${path}-error`);
    if (inline) { inline.textContent = message; inline.hidden = false; }
    const link = button(message, () => {
      if (panel) showStep(Number(panel.dataset.panel));
      (input || summary).focus();
    });
    const item = node('li');
    item.append(link);
    links.append(item);
  }
  document.querySelectorAll('[data-errors]').forEach(badge => {
    const count = counts[Number(badge.dataset.errors)];
    badge.textContent = count;
    badge.hidden = count === 0;
  });
  document.getElementById('validation-summary').textContent = `보완 항목 ${errors.length}개`;
  summary.hidden = errors.length === 0;
  if (errors.length) summary.focus();
}

async function save(action) {
  if (busy) return;
  if (action === 'save_draft' && data.status === 'published' && !window.confirm('초안으로 전환하면 교사의 새 세션 시작이 중단됩니다. 초안으로 저장하시겠습니까?')) return;
  busy = true;
  publish.disabled = true;
  document.getElementById('save-draft').disabled = true;
  document.getElementById('editor-conflict').hidden = true;
  showErrors([]);
  const submittedEdits = edits;
  try {
    const response = await fetch(data.id == null ? '/admin/scenarios' : `/admin/scenarios/${data.id}/update`, {
      method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json', 'x-csrf-token':document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/)?.[1] || ''}, body:JSON.stringify(payload(action))
    });
    if (response.status === 422) {
      const result = await response.json();
      showErrors(result.detail);
    } else if (response.status === 409) {
      const result = await response.json();
      const conflict = document.getElementById('editor-conflict');
      document.getElementById('editor-conflict-message').textContent = `다른 관리자가 변경했습니다. 내 입력을 보존했습니다. 최신 버전: ${result.detail.current_version}`;
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
      status.textContent = action === 'save_draft' ? '초안을 저장했습니다.' : '게시했습니다.';
    } else {
      const messages = {401:'로그인이 필요합니다.', 403:'권한 또는 보안 토큰을 확인하세요.', 404:'시나리오를 찾을 수 없습니다.'};
      status.textContent = messages[response.status] || '저장하지 못했습니다. 입력을 보존했습니다.';
    }
  } catch {
    status.textContent = '저장하지 못했습니다. 입력을 보존했습니다.';
  } finally {
    busy = false;
    reviewState();
    document.getElementById('save-draft').disabled = false;
  }
}
form.addEventListener('submit', event => { event.preventDefault(); save('publish'); });
document.getElementById('save-draft').addEventListener('click', () => save('save_draft'));
document.getElementById('editor-reload').addEventListener('click', () => {
  if (!window.confirm('최신 내용을 불러오면 저장하지 않은 입력을 잃습니다. 다시 불러오시겠습니까?')) return;
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
  if (note) note.textContent = blocked ? '게시 불가 · 필수 내용을 보완하고 서버에서 다시 검증해야 합니다.' : acknowledged ? '검토 확인됨 · 게시 시 필수 내용과 모델을 다시 검증합니다.' : '게시 불가 · 변환 경고 검토 확인이 필요합니다.';
}
if (data.review_required) {
  showErrors(data.review_reasons);
  document.getElementById('acknowledge-review').addEventListener('change', () => { markDirty(); reviewState(); });
}
reviewState();
