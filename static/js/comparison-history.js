import {node, button, roles} from './ai-render.js';
import {active, renderComparisonResult, statusLabel} from './comparison-results.js';

export function mountComparisonHistory(root, {request, apply, invalidate, sourceId, sourceComparisonId = () => null}) {
  const list = node('section', null, {'aria-label':'비교 이력 목록'});
  const detail = node('section', null, {'aria-label':'비교 이력 상세', hidden:''});
  const notice = node('p', '내 비교 기록은 생성 후 30일 동안 보존합니다. 재시도해도 만료 시각은 연장되지 않습니다.', {role:'status'});
  let cursor = null;
  const next = button('다음 20개', () => load(cursor), true);
  const open = button('비교 이력', () => load());
  root.append(open, notice, list, next, detail);
  function unavailable(id) {
    detail.replaceChildren();
    detail.hidden = true;
    invalidate(id);
    notice.textContent = '비교가 만료되었거나 삭제되었습니다. 생성 후 30일이 지나면 조회·재시도·복사할 수 없습니다.';
  }
  async function load(after = null) {
    open.disabled = next.disabled = true;
    try {
      const response = await request(`/admin/comparisons${after ? `?cursor=${encodeURIComponent(after)}` : ''}`);
      if (!response.ok) throw new Error();
      const data = await response.json();
      list.replaceChildren(node('h3', '내 비교 이력 · 최신순 · 20개씩'));
      for (const item of data.items) {
        const row = node('article');
        row.append(node('p', `${roles[item.target_role]} · ${item.source_origin === 'saved_scenario_edit' ? '저장 시나리오의 미저장 수정' : '새 초안'} · ${item.created_at} · ${item.status === 'running' ? '진행 중' : '종료'}`),
          button(`${item.title || '제목 없는 초안'} 상세`, () => show(item.comparison_run_id)));
        for (const status of item.panel_statuses || []) row.append(node('p', statusLabel(status, item.target_role) || 'unknown'));
        list.append(row);
      }
      if (!data.items.length) list.append(node('p', '보존 중인 비교가 없습니다.'));
      cursor = data.next_cursor;
    } catch { notice.textContent = '비교 이력을 불러오지 못했습니다. 다시 확인하세요.'; }
    finally { open.disabled = false; next.disabled = cursor == null; }
  }
  async function show(id) {
    detail.replaceChildren();
    try {
      const response = await request(`/admin/comparisons/${id}`);
      if (response.status === 404) { unavailable(id); return; }
      if (!response.ok) throw new Error();
      const data = await response.json();
      detail.hidden = false;
      const title = node('h3', '비교 이력 상세', {tabindex:'-1'});
      detail.append(title, node('p', `${data.source_snapshot?.title || '제목 없는 초안'} · ${roles[data.target_role]}`),
        node('p', `생성 ${data.created_at} · 만료 ${data.expires_at} · 30일 보존`));
      detail.append(node('h4', '기준 대화 이력'));
      for (const message of data.canonical_context || []) detail.append(node('p', `${message.role === 'teacher' ? '교사' : '학생'}: ${message.content}`, {class:'comparison-text'}));
      detail.append(node('p', `현재 질문: ${data.current_message ?? '없음'}`, {class:'comparison-text'}),
        node('p', `공통 입력 hash: ${data.context_hash ?? 'unknown'}`));
      const running = data.panels.some(p => active(p.status));
      for (const panel of data.panels) {
        const card = node('article', null, {'aria-label':`과거 패널 ${panel.panel_id}`});
        detail.append(card);
        const canApply = data.source_available !== false && data.source_snapshot?.scenario_id != null && data.source_snapshot.scenario_id === sourceId()
          || sourceComparisonId() === data.comparison_run_id;
        renderComparisonResult(card, panel, data.target_role, {stop:() => {}, retry:() => {}, adopt:() => {}, apply:() => apply(panel, data)}, true);
        card.querySelectorAll('button').forEach(control => control.remove());
        if (canApply) card.append(button('이 모델 적용', () => apply(panel, data), running));
      }
      if (data.source_available !== false && data.source_snapshot?.scenario_id != null && data.source_snapshot.scenario_id !== sourceId()) {
        detail.append(node('a', '원본 시나리오 편집 폼 열기', {href:`/admin/scenarios/${data.source_snapshot.scenario_id}/edit?comparison_id=${encodeURIComponent(id)}`}));
      }
      detail.append(node('a', '이 조건으로 새 초안 열기', {href:`/admin/scenarios/new?from_comparison=${encodeURIComponent(id)}`}));
      detail.append(button('비교 기록 삭제', async () => {
        if (!window.confirm('이 비교의 입력과 결과를 삭제하시겠습니까? 비용 원장은 보존됩니다.')) return;
        const response = await request(`/admin/comparisons/${id}`, 'DELETE');
        if (response.ok) {
          detail.replaceChildren();
          detail.hidden = true;
          invalidate(id);
          await load();
          notice.textContent = '비교 기록을 삭제했습니다. 발생한 비용 원장은 보존됩니다.';
          open.focus();
        } else if (response.status === 404) unavailable(id);
        else notice.textContent = '삭제하지 못했습니다. 진행 중 비교는 중지 후 다시 삭제하세요.';
      }, running));
      if (running) detail.append(node('p', '진행 중 비교는 삭제할 수 없습니다. 중지 후 다시 확인하세요.'));
      title.focus();
    } catch { notice.textContent = '비교 상세를 불러오지 못했습니다. 다시 확인하세요.'; }
  }
}
