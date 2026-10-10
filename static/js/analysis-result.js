/* Public result projection shared by the analysis page and history modal. */
(() => {
  let nextId = 0;
  const statuses = {
    ok: '정상 분석', degraded: '부분 분석', failed: '분석 실패', legacy: '과거 분석',
    running: '분석 진행 중', cancelled: '분석 취소됨', interrupted: '분석 중단됨',
    no_dialogue: '대화 없음', ready: '분석 실행 전', plan_required: '분할 계획 확인 대기',
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
  function mount(root) {
    if (root.dataset.mounted) return;
    root.dataset.mounted = '1';
    const prefix = `analysis-result-${++nextId}`;
    let data, announcement, pending, busy = false, planHidden = false;
    let pollTimer, pollController, statusError = false;

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
        const response = await fetch(data.actions.status, {credentials: 'same-origin', signal});
        if (!response.ok) throw new Error();
        const result = await projection(response);
        if (signal.aborted || !root.isConnected) return;
        const changed = statusError || JSON.stringify(data) !== JSON.stringify(result);
        statusError = false;
        data = result;
        if (changed) {
          render();
          announcement.textContent = `최신 실행: ${statuses[data.latest_run.status] || '상태 확인 불가'}`;
        }
        schedulePolling();
      } catch {
        if (signal.aborted || !root.isConnected) return;
        statusError = true;
        render();
        announcement.textContent = '실행 상태를 확인할 수 없습니다. 분석을 새로 실행하지 않고 상태를 다시 확인하세요.';
      }
    }
    const observer = new MutationObserver(() => {
      if (!root.isConnected) {
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
      announcement.textContent = '분석을 요청하고 있습니다.';
      try {
        const response = await fetch(url, {
          method: 'POST', credentials: 'same-origin',
          headers: {...getAnalysisCsrfHeaders(), 'Content-Type': 'application/json'},
          body: JSON.stringify(body),
        });
        if (!response.ok && ![409, 422].includes(response.status)) throw new Error();
        const conflict = response.status === 409 ? (await response.clone().json()).detail : null;
        if (conflict?.code === 'request_conflict') {
          busy = false;
          pending = null;
          render();
          schedulePolling();
          announcement.textContent = '요청 입력이 변경되어 재전송할 수 없습니다. 결과를 새로고침하여 확인하세요.';
          announcement.focus();
          return;
        }
        if (conflict?.code === 'analysis_busy') {
          const statusUrl = url.replace(/\/analyze(?:_regenerate)?$/, `/analysis/runs/${encodeURIComponent(conflict.run_id)}`);
          const active = await fetch(statusUrl, {credentials: 'same-origin'});
          if (!active.ok) throw new Error();
          data = await projection(active);
        } else data = await projection(response);
        busy = false;
        pending = null;
        planHidden = false;
        render();
        schedulePolling();
        announcement.textContent = conflict?.code === 'analysis_busy'
          ? '진행 중인 분석 실행의 상태를 확인했습니다.'
          : response.status === 409
          ? '계획이 변경되었습니다. 새 범위를 확인하고 실행을 다시 확인하세요.'
          : response.status === 422
          ? '분석 계획의 한도를 초과해 실행하지 않았습니다.'
          : '분석 요청 결과를 확인하세요.';
      } catch {
        busy = false;
        render();
        announcement.textContent = '요청 결과를 확인할 수 없습니다. 같은 요청을 다시 전송할 수 있습니다.';
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
        node(parent, 'p', `원문 위치 알 수 없음: ${quote}`);
        return;
      }
      const link = node(parent, 'a', `원문 ${messageId}: ${quote}`);
      link.href = `#${prefix}-message-${messageId}`;
      link.addEventListener('click', event => {
        event.preventDefault();
        const target = document.getElementById(`${prefix}-message-${messageId}`);
        target.closest('details').open = true;
        target.focus();
        target.scrollIntoView({block: 'center'});
        announcement.textContent = `메시지 ${messageId} 원문으로 이동했습니다.`;
      });
    }
    function render() {
      const focusedMessage = root.contains(document.activeElement) ? document.activeElement.dataset.messageId : null;
      const transcriptOpen = root.querySelector('details')?.open;
      root.replaceChildren();
      announcement = node(root, 'p', '', 'analysis-result-notice');
      announcement.setAttribute('role', 'status');
      announcement.setAttribute('aria-live', 'polite');
      announcement.tabIndex = -1;
      const report = data.accepted_report;
      const run = data.latest_run;
      if (run) {
        node(root, 'h2', `최신 실행: ${statuses[run.status] || '상태 확인 불가'}`);
        if (run.superseded) node(root, 'p', '이 요청의 보고서는 이후 분석으로 대체되었습니다. 최신 결과는 세션 분석 화면에서 확인하세요.');
        if (run.preserved && report) node(root, 'p', `이전 ${report.status === 'degraded' ? '부분 분석을' : '정상 결과를'} 보존했습니다. 최신 실행의 결과와 구별해 확인하세요.`);
        if (run.status === 'failed') node(root, 'p', '분석을 완료하지 못했습니다. 다시 시도할 수 있습니다.');
        if (run.status === 'no_dialogue' || run.outcome?.outcome === 'no_dialogue') node(root, 'p', '분석 가능 범위: 0개 메시지 · 호출 없이 안내합니다.');
      }
      if (data.permissions.read_only) node(root, 'p', '과거 자료 · 읽기 전용 · 재분석할 수 없습니다.');
      if (!data.permissions.read_only) {
        const controls = node(root, 'div', null, 'analysis-actions');
        if (pending && !busy) button(controls, '같은 요청 다시 전송', () => send(pending.url, pending.body));
        if (run?.status === 'running') {
          node(controls, 'p', '취소해도 이미 발생한 제공자 비용은 되돌릴 수 없습니다.');
          if (data.actions.cancel) button(controls, '진행 중 분석 취소', () => send(data.actions.cancel, {}));
          if (statusError) button(controls, '실행 상태 다시 확인', checkStatus);
        }
        const plan = data.plan;
        if (plan && plan.status !== 'single' && !planHidden) {
          const section = node(root, 'section', null, 'analysis-section');
          node(section, 'h2', plan.status === 'blocked' ? '분석 계획 불가' : '분할 실행 전 확인');
          node(section, 'p', `대상 범위: 메시지 ${plan.message_ids.join(', ')} · ${plan.message_ids.length}개`);
          node(section, 'p', `${plan.chunks.length}개 분할 + 종합 · 예상 생성 호출 ${plan.generation_calls}회`);
          node(section, 'p', `추정 입력 ${plan.estimated_input_tokens ?? '알 수 없음'} · 추정 출력 ${plan.estimated_output_tokens ?? '알 수 없음'} 토큰`);
          node(section, 'p', '추정치이며 실제 사용량이나 비용을 보장하지 않습니다.');
          node(section, 'p', `일시 장애 재시도 상한: 호출당 ${plan.retry_limit}회 · 최대 ${plan.generation_calls * (plan.retry_limit + 1)}회 시도`);
          plan.chunks.forEach((chunk, index) => node(section, 'p', `분할 ${index + 1}: 메시지 ${chunk.message_ids.join(', ')}`));
          if (plan.merge) node(section, 'p', `종합 입력 상한 추정 ${plan.merge.estimated_input_tokens} · 종합 출력 추정 ${plan.merge.estimated_output_tokens} 토큰`);
          node(section, 'p', '기존 정상 결과는 새 정상 결과가 검증될 때까지 보존합니다. 부분 분석이나 실패로 덮어쓰지 않습니다.');
          if (plan.status === 'blocked') {
            const reasons = {
              unit_too_large: '한 입력 단위가 분석 한도를 초과합니다. 대화를 줄여서 실행하지 않습니다.',
              too_many_chunks: '대화가 최대 8개 분할을 초과합니다.',
              merge_too_large: '종합할 분석 결과가 한도를 초과합니다.',
              unknown_limits: '분석에 필요한 모델 한도를 확인할 수 없습니다.',
              catalog_limit_conflict: '카탈로그의 모델 한도가 검증된 한도보다 작아 실행할 수 없습니다.',
              output_limit_missing: '저장된 출력 상한이 없어 분석 계획을 만들 수 없습니다.',
              output_limit_exceeded: '저장된 출력 상한이 모델의 최대 출력 한도를 초과합니다.',
            };
            node(section, 'p', reasons[plan.blocked_code] || '분석 범위와 한도를 확인할 수 없어 실행하지 않습니다.');
          } else if (data.permissions.can_analyze || data.permissions.can_retry || data.permissions.can_regenerate) {
            button(section, '확인하고 분할 실행', () => analyze(plan.plan_hash));
            button(section, '분할 실행 취소', () => {
              planHidden = true;
              render();
              announcement.textContent = '분할 분석을 실행하지 않았습니다. 기존 결과는 유지됩니다.';
              announcement.focus();
            });
          }
        } else if (plan && plan.status !== 'single' && planHidden) {
          button(controls, '분할 계획 다시 보기', () => { planHidden = false; render(); });
        } else if (data.permissions.can_retry) button(controls, '분석 재시도', () => analyze());
        else if (data.permissions.can_regenerate) button(controls, '분석 재생성', () => analyze());
        else if (data.permissions.can_analyze) button(controls, '분석 실행', () => analyze());
      }
      if (report) {
        const section = node(root, 'section', null, 'analysis-section');
        node(section, 'h2', `채택된 보고서: ${statuses[report.status] || '상태 확인 불가'}`);
        node(section, 'p', `생성 시각: ${report.created_at || '알 수 없음'}`);
        for (const text of report.brief_feedback) node(section, 'p', text, 'analysis-brief-feedback');
        const coverage = report.coverage;
        if (coverage) {
          node(section, 'h3', '분석 범위');
          node(section, 'p', `대화 검토: ${coverage.reviewed_message_ids.length} / ${coverage.input_message_ids.length}개 메시지`);
          node(section, 'p', `검토한 메시지: ${coverage.reviewed_message_ids.join(', ') || '없음'}`);
          if (report.classification_enabled) {
            node(section, 'p', `분류 비율 분모: ${coverage.classified_message_ids.length}개 · 전체 교사 발화: ${coverage.teacher_message_ids.length}개`);
            for (const [field, label] of [
              ['non_analyzable_message_ids', '비분석'], ['unclassified_message_ids', '미분류'],
              ['missing_message_ids', '분류 누락'], ['invalid_message_ids', '검증 제외'],
            ]) {
              node(section, 'p', `${label}: ${coverage[field].length}개`);
              if (coverage[field].length) node(section, 'p', `해당 메시지: ${coverage[field].join(', ')}`);
            }
          }
          node(section, 'p', `학생 응답 없음: ${coverage.response_missing_message_ids.length}개`);
          if (coverage.response_missing_message_ids.length) {
            node(section, 'p', `해당 메시지: ${coverage.response_missing_message_ids.join(', ')} · 학생 이해 변화의 근거로 사용하지 않습니다.`);
          }
          for (const chunk of coverage.chunks) {
            node(section, 'p', `분할 ${statuses[chunk.status] || '미실행'} · 메시지 ${chunk.message_ids.join(', ')}`);
          }
        } else {
          node(section, 'p', '원문 근거·분석 범위: 알 수 없음 · 과거 결과를 그대로 표시합니다.');
        }
        node(section, 'h3', '질문 유형 분포');
        if (report.classification_enabled === false) node(section, 'p', '분류 미사용');
        else if (report.classification_enabled == null) node(section, 'p', '과거 분류 사용 여부: 알 수 없음');
        if (report.classification_enabled !== false && coverage && !coverage.classified_message_ids.length) node(section, 'p', '비율 산정 불가 · 분류 분모 0개');
        else for (const item of report.distribution) {
          node(section, 'p', `${item.name}: ${item.count}개 · ${item.percentage == null ? '비율 산정 불가' : item.percentage + '%'}`);
        }
        node(section, 'h3', '오개념 관찰');
        node(section, 'p', '학생 발화에 관한 관찰이며, 교사 개입의 인과 효과나 학생 능력 점수가 아닙니다.');
        const kinds = {maintained: '오개념 유지', changed: '관찰 가능한 변화', deviated: '설정 이탈', insufficient_evidence: '판단 근거 부족'};
        for (const finding of report.misconception_findings) {
          const card = node(section, 'article');
          node(card, 'h4', kinds[finding.kind] || '판단 근거 부족');
          node(card, 'p', finding.claim);
          for (const item of finding.evidence) evidence(card, item);
        }
        if (!report.misconception_findings.length) node(section, 'p', report.schema_version === 1 ? '과거 오개념 관찰: 알 수 없음' : '검증된 오개념 관찰 근거가 없습니다.');
        for (const [field, label, fields] of [
          ['strengths', '우수한 점', ['reason']],
          ['improvements', '개선할 점', ['missed_reason', 'alternative_question', 'alternative_reason']],
          ['dialogue_coaching', '대화코칭', ['note']],
          ['message_classifications', '발화별 분류', ['reason']],
        ]) {
          node(section, 'h3', label);
          for (const item of report[field]) {
            const card = node(section, 'article', null, 'strong-card');
            evidence(card, item);
            for (const key of fields) node(card, 'p', key === 'alternative_question' ? `대안 질문: ${item[key]}` : item[key]);
          }
        }
      }
      const transcript = node(root, 'details');
      transcript.open = transcriptOpen ?? root.dataset.transcriptOpen === 'true';
      node(transcript, 'summary', '원래 대화');
      const list = node(transcript, 'ol', null, 'coach-msg-list');
      for (const message of data.messages) {
        const row = node(list, 'li', null, 'coach-msg');
        row.id = `${prefix}-message-${message.id}`;
        row.dataset.messageId = message.id;
        row.tabIndex = -1;
        node(row, 'p', `메시지 ${message.id} · ${{teacher: '교사', student: '학생', tutor: '멘토'}[message.role] || message.role}`);
        if (message.turn_index != null) node(row, 'span', `${message.turn_index}번째 턴`, 'coach-msg__turn');
        node(row, 'p', message.content, 'coach-msg__content');
        if (message.created_at) node(row, 'p', `시각: ${message.created_at}`);
        if (report?.schema_version === 1 && message.role === 'teacher') {
          const question = data.questions?.find(q => q.message_id === message.id);
          const label = message.label == null ? null : (question?.label_name ?? message.label);
          node(row, 'p', `분류: ${label ?? '알 수 없음'} · 평가: ${question?.grade ?? '알 수 없음'}`);
          node(row, 'p', `판정 이유: ${question?.reasoning?.summary || '알 수 없음'}`);
          if (question?.reasoning?.improved_sentence) node(row, 'p', `개선한 문장: ${question.reasoning.improved_sentence}`);
        }
      }
      if (focusedMessage) document.getElementById(`${prefix}-message-${focusedMessage}`)?.focus({preventScroll: true});
    }
    fetch(root.dataset.resultUrl, {credentials: 'same-origin'})
      .then(response => { if (!response.ok) throw new Error(); return projection(response); })
      .then(result => { data = result; render(); schedulePolling(); })
      .catch(() => { node(root, 'p', '분석 결과를 불러올 수 없습니다.'); });
  }
  window.mountAnalysisResults = (parent = document) => {
    parent.querySelectorAll('[data-analysis-result]').forEach(mount);
  };
  document.addEventListener('htmx:afterSwap', event => mountAnalysisResults(event.detail.target));
  mountAnalysisResults();
})();
