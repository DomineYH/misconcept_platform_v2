// Responses are injected at the browser HTTP boundary, never by product routes.
export const hidden = 'PRIVATE_REASON_AND_STRUCTURED_PARTIAL';
export async function comparisonFixture(page, statuses = ['completed','failed','interrupted']) {
  const requests = [];
  const unexpected = [];
  const origin = new URL(page.url()).origin;
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.origin !== origin || /^\/(sessions|admin\/sessions|api\/)/.test(url.pathname) || request.method() !== 'GET' && /^\/admin\/scenarios/.test(url.pathname)) unexpected.push(request.url());
  });
  const analysis = await (await page.request.get(new URL('/fixtures/s4/result?state=partial', page.url()).href)).json();
  const normal = await (await page.request.get(new URL('/fixtures/s4/result?state=ok', page.url()).href)).json();
  function localIds(value, key = '') {
    if (Array.isArray(value)) return value.map(item => localIds(item, key));
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k,v]) => [k,localIds(v,k)]));
    return typeof value === 'number' && (key === 'id' || key === 'message_id' || key.endsWith('_ids')) ? value - 100 : value;
  }
  for (const result of [analysis,normal]) {
    result.accepted_report = localIds(result.accepted_report);
    result.accepted_report.coverage.chunks = [];
    result.messages = localIds(result.messages);
  }
  let current, blocked = false, deleted = false;
  function panel(input, index, status) {
    return {...input, status, run_id:`run-${index}-${requests.length}`, seq:3,
      text:status === 'completed' ? `완료 응답 ${index + 1} <img src=x onerror="window.s5xss=1">` : '잠정 학생 응답',
      error_code:'transient', raw_error:hidden, partial_json:hidden, reasoning:hidden,
      timing:{started_at:'2026-10-11T00:00:00Z', first_output_ms:index === 0 ? 120 : null, finished_at:'2026-10-11T00:00:02Z', total_ms:2000},
      usage:{input_tokens:12, output_tokens:null, cost:index === 0 ? 0.01 : null},
      attempts:input.attempts || [], result: {should_intervene:index > 0, coaching:'검증된 코칭', reason_summary:hidden,
        report:status === 'ok' ? normal.accepted_report : analysis.accepted_report, messages:analysis.messages}};
  }
  await page.route('**/admin/comparisons**', async route => {
    const request = route.request(), url = new URL(request.url());
    if (request.method() === 'POST' && url.pathname === '/admin/comparisons') {
      const body = request.postDataJSON();
      requests.push(body);
      if (blocked) return route.fulfill({status:422,json:{detail:{code:'context_limit', errors:[{panel_id:'2', field:'history', message:'패널 2 입력 한도 초과 · 공통 이력을 줄이세요.'}]}}});
      current = {comparison_run_id:'comparison-1', target_role:body.target_role, context_hash:'same-context-hash',
        source_snapshot:body.source_snapshot, canonical_context:body.canonical_context, current_message:body.current_message,
        created_at:'2026-10-11T00:00:00Z', expires_at:'2026-11-10T00:00:00Z',
        panels:body.panels.map((input,i) => panel(input,i,statuses[i] || 'completed'))};
      return route.fulfill({json:current});
    }
    if (url.pathname.endsWith('/retry')) {
      requests.push(request.postDataJSON());
      const id = url.pathname.split('/').at(-2);
      const p = current.panels.find(p => p.panel_id === id);
      p.attempts.push({status:p.status, text:p.text, timing:p.timing, usage:p.usage});
      Object.assign(p, panel(p,Number(id)-1,'completed'));
      return route.fulfill({json:current});
    }
    if (url.pathname.endsWith('/stop')) {
      requests.push(request.postDataJSON());
      const id = url.pathname.split('/').at(-2);
      for (const p of current.panels) if (p.status === 'running' && (id === current.comparison_run_id || p.panel_id === id)) p.status = 'cancelled';
      return route.fulfill({json:current});
    }
    if (request.method() === 'DELETE') { deleted = true; return route.fulfill({status:204}); }
    if (url.pathname === '/admin/comparisons') return route.fulfill({json:{items:current && !deleted ? [{comparison_run_id:current.comparison_run_id,target_role:current.target_role,title:'과거 비교',created_at:current.created_at,status:'finished'}] : [], next_cursor:null}});
    return route.fulfill({json:current});
  });
  return {requests, unexpected, block(value=true) { blocked=value; }, current() { return current; }};
}

export async function openComparison(page) {
  await page.goto(new URL('/fixtures/s2/editor', page.url()).href);
  await page.getByRole('button', {name:'모델 비교 열기',exact:true}).click();
  await page.getByLabel('학생 현재 질문').fill('같은 전체에서 비교하면?');
}
