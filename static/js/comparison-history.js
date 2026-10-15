import {node, button, roles} from './ai-render.js';
import {active, renderComparisonResult, statusLabel} from './comparison-results.js';

export function mountComparisonHistory(root, {request, apply, invalidate, sourceId, sourceComparisonId = () => null}) {
  const list = node('section', null, {'aria-label':uiLanguage.t('admin.comparison_history_list')});
  const detail = node('section', null, {'aria-label':uiLanguage.t('admin.comparison_history_details'), hidden:''});
  const notice = node('p', uiLanguage.t('admin.your_comparisons_are_retained_for_30_days_after_creation_retrying_does_not_extend_their_expiry'), {role:'status'});
  let cursor = null;
  const next = button(uiLanguage.t('admin.next_20'), () => load(cursor), true);
  const open = button(uiLanguage.t('admin.comparison_history'), () => load());
  root.append(open, notice, list, next, detail);
  function unavailable(id) {
    detail.replaceChildren();
    detail.hidden = true;
    invalidate(id);
    notice.textContent = uiLanguage.t('admin.could_not_find_or_open_the_comparison_viewing_retrying_and_copying_are_unavailable_after_30_days');
  }
  async function load(after = null) {
    open.disabled = next.disabled = true;
    try {
      const response = await request(`/admin/comparisons${after ? `?cursor=${encodeURIComponent(after)}` : ''}`);
      if (!response.ok) throw new Error();
      const data = await response.json();
      list.replaceChildren(node('h3', uiLanguage.t('admin.my_comparison_history_newest_first_20_per_page')));
      for (const item of data.items) {
        const row = node('article');
        row.append(node('p', `${roles[item.target_role]} · ${item.source_origin === 'saved_scenario_edit' ? uiLanguage.t('admin.unsaved_edits_to_a_saved_scenario') : uiLanguage.t('admin.new_draft')} · ${item.created_at} · ${item.status === 'running' ? uiLanguage.t('admin.in_progress_2') : uiLanguage.t('admin.end')}`),
          button(`${item.title || uiLanguage.t('admin.untitled_draft')} ${uiLanguage.t('admin.details')}`, () => show(item.comparison_run_id)));
        for (const status of item.panel_statuses || []) row.append(node('p', statusLabel(status, item.target_role) || uiLanguage.t('admin.unknown')));
        list.append(row);
      }
      if (!data.items.length) list.append(node('p', uiLanguage.t('admin.no_retained_comparisons')));
      cursor = data.next_cursor;
    } catch { notice.textContent = uiLanguage.t('admin.could_not_load_comparison_history_try_again'); }
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
      const title = node('h3', uiLanguage.t('admin.comparison_history_details'), {tabindex:'-1'});
      detail.append(title, node('p', `${data.source_snapshot?.title || uiLanguage.t('admin.untitled_draft')} · ${roles[data.target_role]}`),
        node('p', `${uiLanguage.t('admin.created_2')} ${data.created_at} · ${uiLanguage.t('admin.expires')} ${data.expires_at} · ${uiLanguage.t('admin.retained_for_30_days')}`));
      detail.append(node('h4', uiLanguage.t('admin.canonical_conversation_history')));
      for (const message of data.canonical_context || []) detail.append(node('p', `${message.role === 'teacher' ? uiLanguage.t('admin.teacher') : uiLanguage.t('admin.student_label')}: ${message.content}`, {class:'comparison-text'}));
      detail.append(node('p', `${uiLanguage.t('admin.current_question')} ${data.current_message ?? uiLanguage.t('admin.none')}`, {class:'comparison-text'}),
        node('p', `${uiLanguage.t('admin.common_input_hash')} ${data.context_hash ?? uiLanguage.t('admin.unknown')}`));
      const running = data.panels.some(p => active(p.status));
      for (const panel of data.panels) {
        const card = node('article', null, {'aria-label':`${uiLanguage.t('admin.historical_panel')} ${panel.panel_id}`});
        detail.append(card);
        const canApply = data.source_available !== false && data.source_snapshot?.scenario_id != null && data.source_snapshot.scenario_id === sourceId()
          || sourceComparisonId() === data.comparison_run_id;
        renderComparisonResult(card, panel, data.target_role, {stop:() => {}, retry:() => {}, adopt:() => {}, apply:() => apply(panel, data)}, true);
        card.querySelectorAll('button').forEach(control => control.remove());
        if (canApply) card.append(button(uiLanguage.t('admin.apply_this_model'), () => apply(panel, data), running));
      }
      if (data.source_available !== false && data.source_snapshot?.scenario_id != null && data.source_snapshot.scenario_id !== sourceId()) {
        detail.append(node('a', uiLanguage.t('admin.open_source_scenario_editor'), {href:`/admin/scenarios/${data.source_snapshot.scenario_id}/edit?comparison_id=${encodeURIComponent(id)}`}));
      }
      detail.append(node('a', uiLanguage.t('admin.open_new_draft_with_these_conditions'), {href:`/admin/scenarios/new?from_comparison=${encodeURIComponent(id)}`}));
      detail.append(button(uiLanguage.t('admin.delete_comparison_record'), async () => {
        if (!window.confirm(uiLanguage.t('admin.delete_this_comparison_s_input_and_results_the_cost_ledger_will_be_retained'))) return;
        const response = await request(`/admin/comparisons/${id}`, 'DELETE');
        if (response.ok) {
          detail.replaceChildren();
          detail.hidden = true;
          invalidate(id);
          await load();
          notice.textContent = uiLanguage.t('admin.comparison_deleted_the_incurred_cost_ledger_is_retained');
          open.focus();
        } else if (response.status === 404) unavailable(id);
        else notice.textContent = uiLanguage.t('admin.could_not_delete_stop_the_running_comparison_before_deleting_again');
      }, running));
      if (running) detail.append(node('p', uiLanguage.t('admin.running_comparisons_cannot_be_deleted_stop_the_comparison_and_check_again')));
      title.focus();
    } catch { notice.textContent = uiLanguage.t('admin.could_not_load_comparison_details_try_again'); }
  }
}
