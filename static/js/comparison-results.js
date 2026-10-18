import {node, button, roles} from './ai-render.js';

export const completedStatuses = ['completed','ok','degraded'];
export const active = status => ['waiting', 'running', 'streaming'].includes(status);
export const statusLabels = {waiting:uiLanguage.t('admin.waiting'), running:uiLanguage.t('admin.processing'), streaming:uiLanguage.t('admin.streaming'), completed:uiLanguage.t('admin.completed'), ok:uiLanguage.t('teacher.analysis_ok'), degraded:uiLanguage.t('teacher.analysis_degraded'), failed:uiLanguage.t('admin.failed'), cancelled:uiLanguage.t('admin.cancel'), interrupted:uiLanguage.t('admin.interrupted')};
export const statusLabel = (status, role) => status === 'running' ? role === 'student' ? uiLanguage.t('admin.streaming') : role === 'analysis' ? uiLanguage.t('admin.operation_in_progress') : statusLabels[status] : statusLabels[status];

export function failureMessage(panel) {
  const code = panel.error_code;
  let message;
  if (['invalid_output','invalid_json','invalid_reference','invalid_schema','empty_output','empty_response','incomplete_analysis','partial_analysis'].includes(code)) message = uiLanguage.t('admin.response_validation_failed');
  else if (code === 'rate_limited') message = uiLanguage.t('admin.the_provider_request_limit_was_reached');
  else if (code === 'refused') message = uiLanguage.t('admin.the_provider_refused_the_response');
  else if (code?.startsWith('timeout_')) message = uiLanguage.t('admin.the_response_timed_out');
  else if (['output_limit','output_limit_exceeded','max_tokens'].includes(code)) message = uiLanguage.t('admin.the_output_limit_was_reached');
  else if (panel.status === 'cancelled' || code === 'stopped') message = uiLanguage.t('admin.this_run_was_cancelled_by_an_administrator');
  else if (code === 'configuration_unavailable' || code === 'version_conflict') message = uiLanguage.t('admin.permissions_or_ai_connection_model_availability_changed');
  else if (code === 'storage_error') message = uiLanguage.t('admin.saving_results_failed');
  else if (panel.status === 'interrupted' || ['interrupted','disconnected'].includes(code)) message = uiLanguage.t('admin.execution_was_interrupted');
  else message = uiLanguage.t('admin.the_provider_call_failed');
  return `${message} ${panel.status === 'degraded' && panel.result?.report ? uiLanguage.t('admin.validated_partial_results_available') : uiLanguage.t('admin.no_validated_partial_results')}`;
}

export function renderComparisonResult(root, panel, role, actions, locked) {
  const focused = root.contains(document.activeElement) ? document.activeElement.textContent : null;
  root.replaceChildren();
  root.append(node('h3', `${uiLanguage.t('admin.panel')} ${panel.panel_id} · ${roles[role]} · ${panel.model_config?.display_name || panel.model_config?.model_id || uiLanguage.t('admin.model_unknown')}`, {class:'comparison-role', 'data-role':role}),
    node('p', statusLabel(panel.status, role) || uiLanguage.t('admin.status_unknown'), {class:'comparison-state', 'data-status':panel.status}));
  if (['failed','cancelled','interrupted','degraded'].includes(panel.status)) root.append(node('p', failureMessage(panel), {class:panel.status === 'failed' ? 'comparison-failure' : ''}));
  if (role === 'student') root.append(node('p', panel.text || '', {class:'comparison-text'}));
  if (role === 'student' && panel.status !== 'completed') root.append(node('p', uiLanguage.t('admin.provisional_response_incomplete_cannot_adopt')));
  if (role === 'mentor' && panel.status === 'completed') {
    root.append(node('p', panel.result?.should_intervene === false ? uiLanguage.t('admin.no_mentor_intervention') : uiLanguage.t('admin.mentor_coaching')));
    if (panel.result?.should_intervene !== false) root.append(node('p', panel.result?.coaching || '', {class:'comparison-text'}));
  }
  if (role === 'analysis' && completedStatuses.includes(panel.status) && panel.result?.report) {
    const result = node('div', null, {'data-comparison':'true'});
    root.append(result);
    window.showAnalysisResult(result, {accepted_report:panel.result.report, latest_run:{status:panel.status},
      messages:panel.result.messages || [], permissions:{}, actions:{}});
  }
  const timing = panel.timing || {}, usage = panel.usage || {};
  root.append(node('p', `${uiLanguage.t('admin.started_timestamp_label')} ${timing.started_at ?? uiLanguage.t('admin.unknown')} · ${uiLanguage.t('admin.end')} ${timing.finished_at ?? uiLanguage.t('admin.unknown')}`),
    node('p', `${uiLanguage.t('admin.total_time')} ${timing.total_ms == null ? uiLanguage.t('admin.unknown') : `${timing.total_ms} ms`} · ${uiLanguage.t('admin.first_body_timestamp_label')} ${role !== 'student' ? 'N/A' : timing.first_output_ms == null ? uiLanguage.t('admin.unknown') : `${timing.first_output_ms} ms`}`),
    node('p', `${uiLanguage.t('admin.actual_input')} ${usage.input_tokens ?? uiLanguage.t('admin.unknown')} · ${uiLanguage.t('admin.output')} ${usage.output_tokens ?? uiLanguage.t('admin.unknown')} ${uiLanguage.t('admin.tokens_cost')} ${usage.cost ?? uiLanguage.t('admin.unknown')}`),
    node('p', `${uiLanguage.t('admin.requested_model')} ${panel.model_config?.model_id ?? uiLanguage.t('admin.unknown')} · ${uiLanguage.t('admin.returned_model')} ${panel.provider_model_id ?? uiLanguage.t('admin.unknown')}`));
  root.append(node('p', `${uiLanguage.t('admin.applied_options')} ${JSON.stringify(panel.applied_options ?? {})}`));
  const attempts = panel.attempts || [];
  root.append(node('p', `${uiLanguage.t('admin.retries_incur_additional_costs_previous_attempts')} ${attempts.length}${uiLanguage.t('admin.cancellation_does_not_guarantee_refunds_of_incurred_costs')}`));
  attempts.forEach((attempt, index) => {
    const previous = node('details');
    previous.append(node('summary', `${uiLanguage.t('admin.previous_attempt')} ${index + 1} · ${statusLabel(attempt.status, role) || uiLanguage.t('admin.unknown')}`));
    if (['failed','cancelled','interrupted','degraded'].includes(attempt.status)) previous.append(node('p', failureMessage(attempt)));
    if (role === 'student') {
      previous.append(node('p', attempt.text || '', {class:'comparison-text'}));
      if (attempt.status !== 'completed') previous.append(node('p', uiLanguage.t('admin.provisional_response_incomplete_cannot_adopt')));
    }
    if (role === 'mentor' && attempt.status === 'completed') {
      previous.append(node('p', attempt.result?.should_intervene === false ? uiLanguage.t('admin.no_mentor_intervention') : uiLanguage.t('admin.mentor_coaching')));
      if (attempt.result?.should_intervene !== false) previous.append(node('p', attempt.result?.coaching || '', {class:'comparison-text'}));
    }
    if (role === 'analysis' && attempt.result?.report) {
      const result = node('div', null, {'data-comparison':'true'});
      previous.append(result);
      window.showAnalysisResult(result, {accepted_report:attempt.result.report, latest_run:{status:attempt.status},
        messages:attempt.result.messages || [], permissions:{}, actions:{}});
    }
    previous.append(node('p', `${uiLanguage.t('admin.total_time')} ${attempt.timing?.total_ms ?? uiLanguage.t('admin.unknown')} ms · ${uiLanguage.t('admin.cost')} ${attempt.usage?.cost ?? uiLanguage.t('admin.unknown')}`));
    root.append(previous);
  });
  const controls = [
    ['stop', 'stop_panel', !active(panel.status) || !panel.run_id],
    ['retry', 'retry_panel', locked || !['failed','cancelled','interrupted','degraded'].includes(panel.status)],
    ...(role === 'student' ? [['adopt', 'adopt_student_response', locked || panel.status !== 'completed']] : []),
    ['apply', 'apply_this_model', locked || !panel.model_config || active(panel.status)]
  ];
  for (const [action, label, disabled] of controls) {
    const control = button(uiLanguage.t('admin.' + label), actions[action], disabled, ['adopt','apply'].includes(action) ? 'primary' : 'secondary');
    control.dataset.action = action;
    root.append(control);
  }
  if (focused) [...root.querySelectorAll('button')].find(control => control.textContent === focused && !control.disabled)?.focus({preventScroll:true});
}
