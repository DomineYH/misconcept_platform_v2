import {node, button} from './ai-render.js';

export const completedStatuses = ['completed','ok','degraded'];
export const active = status => ['waiting', 'running', 'streaming'].includes(status);
export const statusLabels = {waiting:'대기', running:'처리 중', streaming:'스트리밍', completed:'완료', ok:'정상 분석', degraded:'부분 분석', failed:'실패', cancelled:'취소', interrupted:'중단'};
export const statusLabel = (status, role) => status === 'running' ? role === 'student' ? '스트리밍' : role === 'analysis' ? '진행 중' : statusLabels[status] : statusLabels[status];

export function failureMessage(panel) {
  const code = panel.error_code;
  let message;
  if (['invalid_output','invalid_json','invalid_reference','invalid_schema','empty_output','empty_response','incomplete_analysis','partial_analysis'].includes(code)) message = '응답 검증에 실패했습니다.';
  else if (code === 'rate_limited') message = '제공자 요청 한도에 도달했습니다.';
  else if (code === 'refused') message = '제공자가 응답을 거절했습니다.';
  else if (code?.startsWith('timeout_')) message = '응답 제한 시간을 초과했습니다.';
  else if (['output_limit','output_limit_exceeded','max_tokens'].includes(code)) message = '출력 한도에 도달했습니다.';
  else if (panel.status === 'cancelled' || code === 'stopped') message = '관리자가 취소한 실행입니다.';
  else if (code === 'configuration_unavailable' || code === 'version_conflict') message = '권한 또는 AI 연결·모델 사용 가능 상태가 변경되었습니다.';
  else if (code === 'storage_error') message = '결과 저장에 실패했습니다.';
  else if (panel.status === 'interrupted' || ['interrupted','disconnected'].includes(code)) message = '실행이 중단되었습니다.';
  else message = '제공자 호출에 실패했습니다.';
  return `${message} ${panel.status === 'degraded' && panel.result?.report ? '검증된 부분 결과 있음' : '검증된 부분 결과 없음'}`;
}

export function renderComparisonResult(root, panel, role, actions, locked) {
  const focused = root.contains(document.activeElement) ? document.activeElement.textContent : null;
  root.replaceChildren();
  root.append(node('p', statusLabel(panel.status, role) || '상태 unknown', {role:'status'}));
  if (['failed','cancelled','interrupted','degraded'].includes(panel.status)) root.append(node('p', failureMessage(panel), {class:'scenario-error'}));
  if (role === 'student') root.append(node('p', panel.text || '', {class:'comparison-text'}));
  if (role === 'student' && panel.status !== 'completed') root.append(node('p', '잠정 응답 · 미완료 · 채택 불가'));
  if (role === 'mentor' && panel.status === 'completed') {
    root.append(node('p', panel.result?.should_intervene === false ? '멘토 미개입' : '멘토 코칭'));
    if (panel.result?.should_intervene !== false) root.append(node('p', panel.result?.coaching || '', {class:'comparison-text'}));
  }
  if (role === 'analysis' && completedStatuses.includes(panel.status) && panel.result?.report) {
    const result = node('div', null, {'data-comparison':'true'});
    root.append(result);
    window.showAnalysisResult(result, {accepted_report:panel.result.report, latest_run:{status:panel.status},
      messages:panel.result.messages || [], permissions:{}, actions:{}});
  }
  const timing = panel.timing || {}, usage = panel.usage || {};
  root.append(node('p', `시작 ${timing.started_at ?? 'unknown'} · 종료 ${timing.finished_at ?? 'unknown'}`),
    node('p', `전체 시간 ${timing.total_ms == null ? 'unknown' : `${timing.total_ms} ms`} · 첫 본문 ${role !== 'student' ? 'N/A' : timing.first_output_ms == null ? 'unknown' : `${timing.first_output_ms} ms`}`),
    node('p', `실제 입력 ${usage.input_tokens ?? 'unknown'} · 출력 ${usage.output_tokens ?? 'unknown'} 토큰 · 비용 ${usage.cost ?? 'unknown'}`),
    node('p', `요청 모델 ${panel.model_config?.model_id ?? 'unknown'} · 반환 모델 ${panel.provider_model_id ?? 'unknown'}`));
  root.append(node('p', `적용 옵션: ${JSON.stringify(panel.applied_options ?? {})}`));
  const attempts = panel.attempts || [];
  root.append(node('p', `재시도는 추가 비용이 발생합니다. 이전 시도 ${attempts.length}회 · 취소해도 발생한 비용은 환불을 보장하지 않습니다.`));
  attempts.forEach((attempt, index) => {
    const previous = node('details');
    previous.append(node('summary', `이전 시도 ${index + 1} · ${statusLabel(attempt.status, role) || 'unknown'}`));
    if (['failed','cancelled','interrupted','degraded'].includes(attempt.status)) previous.append(node('p', failureMessage(attempt)));
    if (role === 'student') previous.append(node('p', attempt.text || '', {class:'comparison-text'}));
    if (role === 'mentor' && attempt.status === 'completed') {
      previous.append(node('p', attempt.result?.should_intervene === false ? '멘토 미개입' : '멘토 코칭'));
      if (attempt.result?.should_intervene !== false) previous.append(node('p', attempt.result?.coaching || '', {class:'comparison-text'}));
    }
    if (role === 'analysis' && attempt.result?.report) {
      const result = node('div', null, {'data-comparison':'true'});
      previous.append(result);
      window.showAnalysisResult(result, {accepted_report:attempt.result.report, latest_run:{status:attempt.status},
        messages:attempt.result.messages || [], permissions:{}, actions:{}});
    }
    previous.append(node('p', `전체 시간 ${attempt.timing?.total_ms ?? 'unknown'} ms · 비용 ${attempt.usage?.cost ?? 'unknown'}`));
    root.append(previous);
  });
  root.append(button('패널 중지', actions.stop, !active(panel.status) || !panel.run_id),
    button('패널 재시도', actions.retry, locked || !['failed','cancelled','interrupted','degraded'].includes(panel.status)));
  if (role === 'student') root.append(button('학생 응답 채택', actions.adopt, locked || panel.status !== 'completed'));
  root.append(button('이 모델 적용', actions.apply, locked || !panel.model_config || active(panel.status)));
  if (focused) [...root.querySelectorAll('button')].find(control => control.textContent === focused && !control.disabled)?.focus({preventScroll:true});
}
