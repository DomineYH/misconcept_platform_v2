/* Public result projection shared by the analysis page and history modal. */
(() => {
  let nextId = 0;
  const statuses = {
    ok: uiLanguage.t('teacher.analysis_ok'), degraded: uiLanguage.t('teacher.analysis_degraded'), failed: uiLanguage.t('teacher.analysis_failed'), legacy: uiLanguage.t('teacher.analysis_legacy'),
    running: uiLanguage.t('teacher.analysis_running'), cancelled: uiLanguage.t('teacher.analysis_cancelled'), interrupted: uiLanguage.t('teacher.analysis_interrupted'),
    no_dialogue: uiLanguage.t('teacher.no_dialogue'), ready: uiLanguage.t('teacher.analysis_ready'), plan_required: uiLanguage.t('teacher.plan_required'),
  };
  function node(parent, tag, text, className) {
    const el = document.createElement(tag);
    if (text != null) el.textContent = text;
    if (className) el.className = className;
    parent.append(el);
    return el;
  }
  async function projection(response) {
    const result = await response.json();
    if (!Array.isArray(result.messages) || !result.permissions || !result.actions) throw new Error();
    return result;
  }
  function mount(root, initialData = null) {
    if (root.dataset.mounted) return;
    root.dataset.mounted = '1';
    const prefix = `analysis-result-${++nextId}`;
    let data, announcement, pending, busy = false, planHidden = false;
    let pollTimer, pollController, statusError = false;
    const readGuard = () => ({busy: busy || Boolean(pending) || data?.latest_run?.status === 'running'});
    const unregister = uiLanguage.registerGuard(readGuard);
    function publishState() {
      document.dispatchEvent(new CustomEvent('analysis:state', {
        detail: {sessionId: root.dataset.sessionId, busy: readGuard().busy}
      }));
    }

    function stopPolling() {
      clearTimeout(pollTimer);
      if (pollController) pollController.abort();
    }
    function schedulePolling() {
      if (root.isConnected && data.latest_run?.status === 'running' && data.actions.status) {
        pollTimer = setTimeout(checkStatus, 1000);
      }
    }
    async function checkStatus() {
      if (!root.isConnected || busy) return;
      pollController = new AbortController();
      const signal = pollController.signal;
      try {
        const response = await fetch(data.actions.status, {credentials: 'same-origin', signal, headers: uiLanguage.headers(data.actions.status)});
        if (!response.ok) throw new Error();
        const result = await projection(response);
        if (signal.aborted || !root.isConnected) return;
        const announce = statusError || data.latest_run?.status !== result.latest_run?.status;
        const changed = statusError || JSON.stringify(data) !== JSON.stringify(result);
        statusError = false;
        data = result;
        if (changed) {
          render();
          if (announce) announcement.textContent = uiLanguage.t('teacher.latest_run', {status:statuses[data.latest_run?.status] || uiLanguage.t('teacher.status_unknown')});
        }
        schedulePolling();
      } catch {
        if (signal.aborted || !root.isConnected) return;
        statusError = true;
        render();
        announcement.textContent = uiLanguage.t('teacher.analysis_status_error');
      }
    }
    const observer = new MutationObserver(() => {
      if (!root.isConnected) {
        unregister();
        stopPolling();
        observer.disconnect();
      }
    });
    observer.observe(document.body, {childList: true, subtree: true});
    window.addEventListener('pagehide', () => {
      stopPolling();
      observer.disconnect();
    }, {once: true});

    function button(parent, label, action) {
      const el = node(parent, 'button', label, 'btn btn-secondary');
      el.type = 'button';
      el.disabled = busy;
      el.addEventListener('click', action);
    }
    async function send(url, body) {
      if (busy) return;
      stopPolling();
      busy = true;
      pending = {url, body};
      render();
      announcement.textContent = uiLanguage.t('teacher.analysis_requesting');
      try {
        const response = await fetch(url, {
          method: 'POST', credentials: 'same-origin',
          headers: uiLanguage.headers(url, {...getAnalysisCsrfHeaders(), 'Content-Type': 'application/json'}),
          body: JSON.stringify(body),
        });
        if (!response.ok && ![409, 422].includes(response.status)) throw new Error();
        const conflict = response.status === 409 ? (await response.clone().json()).detail : null;
        if (conflict?.code === 'request_conflict') {
          busy = false;
          pending = null;
          render();
          schedulePolling();
          announcement.textContent = uiLanguage.t('teacher.analysis_request_conflict');
          announcement.focus();
          return;
        }
        if (conflict?.code === 'analysis_busy') {
          const statusUrl = url.replace(/\/analyze(?:_regenerate)?$/, `/analysis/runs/${encodeURIComponent(conflict.run_id)}`);
          const active = await fetch(statusUrl, {credentials: 'same-origin', headers: uiLanguage.headers(statusUrl)});
          if (!active.ok) throw new Error();
          data = await projection(active);
        } else data = await projection(response);
        busy = false;
        pending = null;
        planHidden = false;
        render();
        schedulePolling();
        announcement.textContent = conflict?.code === 'analysis_busy'
          ? uiLanguage.t('teacher.analysis_busy_found')
          : response.status === 409
          ? uiLanguage.t('teacher.analysis_plan_changed')
          : response.status === 422
          ? uiLanguage.t('teacher.analysis_plan_limit')
          : uiLanguage.t('teacher.analysis_request_result');
      } catch {
        busy = false;
        render();
        announcement.textContent = uiLanguage.t('teacher.request_unknown');
      }
      announcement.focus();
    }
    function analyze(planHash) {
      const body = {request_id: crypto.randomUUID()};
      if (planHash) body.plan_hash = planHash;
      send(data.actions.analyze, body);
    }

    function evidence(parent, item) {
      const messageId = item.message_id ?? item.student_message_id;
      const quote = item.quote ?? item.student_quote ?? '';
      const message = data.messages.find(m => m.id === messageId);
      if (!message) {
        node(parent, 'p', uiLanguage.t('teacher.source_unknown', {quote:quote}));
        return;
      }
      const link = node(parent, 'a', uiLanguage.t('teacher.source_quote', {id:messageId, quote:quote}));
      link.href = `#${prefix}-message-${messageId}`;
      link.addEventListener('click', event => {
        event.preventDefault();
        const target = document.getElementById(`${prefix}-message-${messageId}`);
        if (!target) return;
        const transcript = target.closest('details');
        if (transcript) transcript.open = true;
        target.focus();
        target.scrollIntoView({block: 'center'});
        announcement.textContent = uiLanguage.t('teacher.source_moved', {id:messageId});
      });
    }
    function render() {
      publishState();
      const focusedMessage = root.contains(document.activeElement) ? document.activeElement.dataset.messageId : null;
      const focusedTranscript = root.querySelector('summary') === document.activeElement;
      const focusedButton = root.contains(document.activeElement) && document.activeElement.tagName === 'BUTTON' ? document.activeElement.textContent : null;
      const transcriptOpen = root.querySelector('details')?.open;
      for (const child of Array.from(root.children)) if (child !== announcement) child.remove();
      announcement ||= node(root, 'p', '', 'analysis-result-notice');
      announcement.setAttribute('role', 'status');
      announcement.setAttribute('aria-live', 'polite');
      announcement.tabIndex = -1;
      const report = data.accepted_report;
      const run = data.latest_run;
      if (run) {
        const latest = node(root, 'section', null, 'analysis-latest-run');
        latest.dataset.status = run.status;
        node(latest, 'h2', uiLanguage.t('teacher.latest_run', {status:statuses[run.status] || uiLanguage.t('teacher.status_unknown')}));
        if (run.started_at) node(latest, 'p', uiLanguage.t('teacher.run_started', {time:run.started_at}));
        if (run.finished_at) node(latest, 'p', uiLanguage.t('teacher.run_finished', {time:run.finished_at}));
        if (run.superseded) node(latest, 'p', uiLanguage.t('teacher.analysis_superseded'));
        if (run.preserved && report) node(latest, 'p', uiLanguage.t('teacher.report_preserved', {report:report.status === 'legacy' ? uiLanguage.t('teacher.preserved_legacy') : report.status === 'degraded' ? uiLanguage.t('teacher.preserved_degraded') : uiLanguage.t('teacher.preserved_ok')}));
        if (run.status === 'running' && report) node(latest, 'p', uiLanguage.t('teacher.analysis_preserved_running'));
        if (run.status === 'failed') node(latest, 'p', data.permissions.can_retry ? uiLanguage.t('teacher.analysis_retry_notice') : uiLanguage.t('teacher.analysis_failed_notice'));
        if (run.status === 'no_dialogue' || run.outcome?.outcome === 'no_dialogue') node(latest, 'p', uiLanguage.t('teacher.analysis_empty_coverage'));
      }
      if (data.permissions.read_only) node(root, 'p', uiLanguage.t('teacher.legacy_read_only'));
      if (!data.permissions.read_only) {
        const controls = node(root, 'div', null, 'analysis-actions');
        if (pending && !busy) button(controls, uiLanguage.t('teacher.resend_request'), () => send(pending.url, pending.body));
        if (run?.status === 'running') {
          node(controls, 'p', uiLanguage.t('teacher.cancel_cost_notice'));
          if (data.actions.cancel) button(controls, uiLanguage.t('teacher.cancel_analysis'), () => send(data.actions.cancel, {}));
          if (statusError) button(controls, uiLanguage.t('teacher.check_analysis_status'), checkStatus);
        }
        const plan = data.plan;
        if (plan && plan.status !== 'single' && !planHidden) {
          const section = node(root, 'section', null, 'analysis-section');
          node(section, 'h2', plan.status === 'blocked' ? uiLanguage.t('teacher.plan_blocked') : uiLanguage.t('teacher.plan_confirm'));
          node(section, 'p', uiLanguage.t('teacher.plan_range', {ids:plan.message_ids.join(', '), count:plan.message_ids.length}));
          node(section, 'p', uiLanguage.t('teacher.plan_calls', {chunks:plan.chunks.length, calls:plan.generation_calls}));
          node(section, 'p', uiLanguage.t('teacher.plan_tokens', {input:plan.estimated_input_tokens ?? uiLanguage.t('teacher.unknown'), output:plan.estimated_output_tokens ?? uiLanguage.t('teacher.unknown')}));
          node(section, 'p', uiLanguage.t('teacher.estimate_notice'));
          node(section, 'p', uiLanguage.t('teacher.plan_retries', {limit:plan.retry_limit, attempts:plan.generation_calls * (plan.retry_limit + 1)}));
          plan.chunks.forEach((chunk, index) => node(section, 'p', uiLanguage.t('teacher.chunk_messages', {number:index + 1, ids:chunk.message_ids.join(', ')})));
          if (plan.merge) node(section, 'p', uiLanguage.t('teacher.merge_tokens', {input:plan.merge.estimated_input_tokens, output:plan.merge.estimated_output_tokens}));
          node(section, 'p', uiLanguage.t('teacher.preserve_notice'));
          if (plan.status === 'blocked') {
            const reasons = {
              unit_too_large: uiLanguage.t('teacher.unit_too_large'),
              too_many_chunks: uiLanguage.t('teacher.too_many_chunks'),
              merge_too_large: uiLanguage.t('teacher.merge_too_large'),
              unknown_limits: uiLanguage.t('teacher.unknown_limits'),
              catalog_limit_conflict: uiLanguage.t('teacher.catalog_limit_conflict'),
              output_limit_missing: uiLanguage.t('teacher.output_limit_missing'),
              output_limit_exceeded: uiLanguage.t('teacher.output_limit_exceeded'),
            };
            node(section, 'p', reasons[plan.blocked_code] || uiLanguage.t('teacher.plan_unknown'));
          } else if (data.permissions.can_analyze || data.permissions.can_retry || data.permissions.can_regenerate) {
            button(section, uiLanguage.t('teacher.run_chunks'), () => analyze(plan.plan_hash));
            button(section, uiLanguage.t('teacher.cancel_chunks'), () => {
              planHidden = true;
              render();
              announcement.textContent = uiLanguage.t('teacher.chunks_cancelled');
              announcement.focus();
            });
          }
        } else if (plan && plan.status !== 'single' && planHidden) {
          button(controls, uiLanguage.t('teacher.view_plan'), () => { planHidden = false; render(); });
        } else if (data.permissions.can_retry) button(controls, uiLanguage.t('teacher.retry_analysis'), () => analyze());
        else if (data.permissions.can_regenerate) button(controls, uiLanguage.t('teacher.regenerate_analysis'), () => analyze());
        else if (data.permissions.can_analyze) button(controls, uiLanguage.t('teacher.run_analysis'), () => analyze());
      }
      if (report) {
        const section = node(root, 'section', null, 'analysis-section analysis-accepted-report');
        section.dataset.status = report.status;
        node(section, 'h2', uiLanguage.t('teacher.report_status', {report:root.dataset.comparison ? uiLanguage.t('teacher.comparison_report') : uiLanguage.t('teacher.accepted_report'), status:statuses[report.status] || uiLanguage.t('teacher.status_unknown')}));
        node(section, 'p', uiLanguage.t('teacher.report_created', {time:report.created_at || uiLanguage.t('teacher.unknown')}));
        for (const text of report.brief_feedback) node(section, 'p', text, 'analysis-brief-feedback');
        const coverage = report.coverage;
        if (coverage) {
          node(section, 'h3', uiLanguage.t('teacher.coverage'));
          node(section, 'p', uiLanguage.t('teacher.conversation_reviewed', {reviewed:coverage.reviewed_message_ids.length, total:coverage.input_message_ids.length}));
          node(section, 'p', uiLanguage.t('teacher.reviewed_messages', {ids:coverage.reviewed_message_ids.join(', ') || uiLanguage.t('teacher.none')}));
          if (report.classification_enabled) {
            node(section, 'p', uiLanguage.t('teacher.classification_denominator', {classified:coverage.classified_message_ids.length, total:coverage.teacher_message_ids.length}));
            for (const [field, label] of [
              ['non_analyzable_message_ids', uiLanguage.t('teacher.non_analyzable')], ['unclassified_message_ids', uiLanguage.t('teacher.unclassified')],
              ['missing_message_ids', uiLanguage.t('teacher.missing_classification')], ['invalid_message_ids', uiLanguage.t('teacher.invalid_classification')],
            ]) {
              node(section, 'p', uiLanguage.t('teacher.coverage_count', {label:label, count:coverage[field].length}));
              if (coverage[field].length) node(section, 'p', uiLanguage.t('teacher.affected_messages', {ids:coverage[field].join(', ')}));
            }
          }
          node(section, 'p', uiLanguage.t('teacher.missing_student_count', {count:coverage.response_missing_message_ids.length}));
          if (coverage.response_missing_message_ids.length) {
            node(section, 'p', uiLanguage.t('teacher.missing_student_evidence', {ids:coverage.response_missing_message_ids.join(', ')}));
          }
          for (const chunk of coverage.chunks) {
            node(section, 'p', uiLanguage.t('teacher.chunk_status', {status:statuses[chunk.status] || uiLanguage.t('teacher.not_run'), ids:chunk.message_ids.join(', ')}));
          }
        } else {
          node(section, 'p', uiLanguage.t('teacher.legacy_coverage'));
        }
        node(section, 'h3', uiLanguage.t('teacher.question_distribution'));
        if (report.classification_enabled === false) node(section, 'p', uiLanguage.t('teacher.classification_off'));
        else if (report.classification_enabled == null) node(section, 'p', uiLanguage.t('teacher.legacy_classification'));
        if (report.classification_enabled !== false && coverage && !coverage.classified_message_ids.length) node(section, 'p', uiLanguage.t('teacher.zero_denominator'));
        else for (const item of report.distribution) {
          node(section, 'p', uiLanguage.t('teacher.distribution_item', {name:item.name, count:item.count, percentage:item.percentage == null ? uiLanguage.t('teacher.percentage_unknown') : item.percentage + '%'}));
        }
        node(section, 'h3', uiLanguage.t('teacher.findings_title'));
        node(section, 'p', uiLanguage.t('teacher.findings_notice'));
        const kinds = {maintained: uiLanguage.t('teacher.maintained'), changed: uiLanguage.t('teacher.changed'), deviated: uiLanguage.t('teacher.deviated'), insufficient_evidence: uiLanguage.t('teacher.insufficient_evidence')};
        for (const finding of report.misconception_findings) {
          const card = node(section, 'article');
          node(card, 'h4', kinds[finding.kind] || uiLanguage.t('teacher.insufficient_evidence'));
          node(card, 'p', finding.claim);
          for (const item of finding.evidence) evidence(card, item);
        }
        if (!report.misconception_findings.length) node(section, 'p', report.schema_version === 1 ? uiLanguage.t('teacher.legacy_findings') : uiLanguage.t('teacher.no_findings'));
        for (const [field, label, fields] of [
          ['strengths', uiLanguage.t('teacher.strengths'), ['reason']],
          ['improvements', uiLanguage.t('teacher.improvements'), ['missed_reason', 'alternative_question', 'alternative_reason']],
          ['dialogue_coaching', uiLanguage.t('teacher.dialogue_coaching'), ['note']],
          ['message_classifications', uiLanguage.t('teacher.message_classifications'), ['reason']],
        ]) {
          node(section, 'h3', label);
          for (const item of report[field]) {
            const card = node(section, 'article', null, field === 'improvements' ? 'improve-card' : 'strong-card');
            evidence(card, item);
            for (const key of fields) node(card, 'p', key === 'alternative_question' ? uiLanguage.t('teacher.alternative_question', {question:item[key]}) : item[key]);
          }
        }
      }
      const transcript = node(root, 'details');
      transcript.open = transcriptOpen ?? root.dataset.transcriptOpen === 'true';
      node(transcript, 'summary', uiLanguage.t('teacher.original_conversation'));
      const list = node(transcript, 'ol', null, 'coach-msg-list');
      for (const message of data.messages) {
        const row = node(list, 'li', null, 'coach-msg');
        row.id = `${prefix}-message-${message.id}`;
        row.dataset.messageId = message.id;
        row.tabIndex = -1;
        node(row, 'p', uiLanguage.t('teacher.message_role', {id:message.id, role:{teacher: uiLanguage.t('teacher.teacher_role'), student: uiLanguage.t('teacher.student'), tutor: uiLanguage.t('teacher.mentor')}[message.role] || message.role}));
        if (message.turn_index != null) node(row, 'span', uiLanguage.t('teacher.turn_number', {number:message.turn_index}), 'coach-msg__turn');
        node(row, 'p', message.content, 'coach-msg__content');
        if (message.created_at) node(row, 'p', uiLanguage.t('teacher.message_time', {time:message.created_at}));
        if (report?.schema_version === 1 && message.role === 'teacher') {
          const question = data.questions?.find(q => q.message_id === message.id);
          const label = message.label == null ? null : (question?.label_name ?? message.label);
          node(row, 'p', uiLanguage.t('teacher.message_grade', {label:label ?? uiLanguage.t('teacher.unknown'), grade:question?.grade ?? uiLanguage.t('teacher.unknown')}));
          node(row, 'p', uiLanguage.t('teacher.message_reason', {reason:question?.reasoning?.summary || uiLanguage.t('teacher.unknown')}));
          if (question?.reasoning?.improved_sentence) node(row, 'p', uiLanguage.t('teacher.improved_sentence', {sentence:question.reasoning.improved_sentence}));
        }
      }
      if (focusedButton) Array.from(root.querySelectorAll('button')).find(el => el.textContent === focusedButton)?.focus({preventScroll: true});
      if (focusedTranscript) transcript.querySelector('summary').focus({preventScroll: true});
      if (focusedMessage) document.getElementById(`${prefix}-message-${focusedMessage}`)?.focus({preventScroll: true});
    }
    if (initialData) {
      data = initialData;
      render();
      return;
    }
    fetch(root.dataset.resultUrl, {credentials: 'same-origin', headers: uiLanguage.headers(root.dataset.resultUrl)})
      .then(response => { if (!response.ok) throw new Error(); return projection(response); })
      .then(result => { data = result; render(); schedulePolling(); })
      .catch(() => { node(root, 'p', uiLanguage.t('teacher.analysis_load_failed')); });
  }
  window.showAnalysisResult = (root, data) => mount(root, data);
  window.mountAnalysisResults = (parent = document) => {
    parent.querySelectorAll('[data-analysis-result]').forEach(root => mount(root));
  };
  document.addEventListener('htmx:afterSwap', event => mountAnalysisResults(event.detail.target));
  mountAnalysisResults();
})();
