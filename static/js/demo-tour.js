/* Demo guided tour (contract D0-03 s9, s24, s24.1, s25): the single owner of the six-step map.
   Pure helpers are always defined; the runtime starts only on demo pages. */
window.demoTour = (() => {
  'use strict';
  const STEPS = ['start', 'scenario', 'dialogue', 'analysis', 'admin_csv', 'reset'];
  const KINDS = ['history_viewed', 'admin_record_viewed', 'csv_received'];
  const ADMIN_COLUMNS = ['session_id', 'scenario_id', 'scenario_title', 'teacher_id', 'teacher_username',
    'teacher_nickname', 'session_started_at', 'session_ended_at', 'message_id', 'message_created_at', 'role',
    'content', 'label', 'confidence', 'meta_json', 'feedback', 'classification_status', 'student_name',
    'snapshot_origin', 'snapshot_created_at', 'source_scenario_version', 'config_hash_kind',
    'analysis_schema_version', 'analysis_status', 'analysis_coverage_json', 'misconception_findings_json',
    'message_analysis_disposition'];
  const DEMO_COLUMNS = ['demo_response_source', 'demo_external_ai_called', 'demo_content_version',
    'demo_fixture_version', 'demo_record_origin'];
  const COLUMNS = [...ADMIN_COLUMNS, ...DEMO_COLUMNS];
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const CHANNEL = 'misconcept-demo-v1';
  const CONTROL_KEY = 'demo:control-v1';
  const NOTIFY_KEY = 'demo:notification';

  /* ---------- pure helpers ---------- */

  function computeSteps(facts, observations, resetDone = false) {
    const map = {};
    if (!facts) {
      STEPS.forEach(step => { map[step] = step === 'start' ? 'current' : 'pending'; });
      return map;
    }
    const journey = facts.journey_session_id;
    const kinds = new Set((observations || [])
      .filter(o => o && journey != null && o.journey_session_id === journey).map(o => o.kind));
    const analysis = !!facts.analysis_ready && (kinds.has('history_viewed') || kinds.has('admin_record_viewed'));
    const csv = analysis && kinds.has('admin_record_viewed') && kinds.has('csv_received');
    const raw = [facts.teacher_entered, facts.scenario_created, facts.dialogue_complete, analysis, csv,
      resetDone && csv && facts.teacher_deleted && facts.session_deleted].map(Boolean);
    const last = raw.lastIndexOf(true);
    let currentSet = false;
    STEPS.forEach((step, i) => {
      if (i <= last) map[step] = 'complete';
      else if (!currentSet) { map[step] = 'current'; currentSet = true; }
      else map[step] = 'pending';
    });
    return map;
  }

  function mapString(map) {
    const letters = {complete: 'C', current: 'X', pending: 'P'};
    return STEPS.map(step => letters[map[step]]).join(' ');
  }

  function mergeObservations(...lists) {
    const seen = new Map();
    for (const list of lists) {
      for (const o of list || []) if (o && !seen.has(o.kind)) seen.set(o.kind, o);
    }
    return [...seen.values()];
  }

  // RFC 4180 into arrays of cells; null when a quoted field never closes.
  function parseRows(text) {
    const src = text.charCodeAt(0) === 0xFEFF ? text.slice(1) : text;
    const rows = [];
    let row = [], field = '', quoted = false, started = false;
    for (let i = 0; i < src.length; i++) {
      const c = src[i];
      if (quoted) {
        if (c !== '"') field += c;
        else if (src[i + 1] === '"') { field += '"'; i++; }
        else quoted = false;
      } else if (c === '"' && !started) {
        quoted = true; started = true;
      } else if (c === ',') {
        row.push(field); field = ''; started = false;
      } else if (c === '\n' || c === '\r') {
        if (c === '\r' && src[i + 1] === '\n') i++;
        if (row.length || field !== '' || started) { row.push(field); rows.push(row); }
        row = []; field = ''; started = false;
      } else {
        field += c; started = true;
      }
    }
    if (quoted) return null;
    if (row.length || field !== '' || started) { row.push(field); rows.push(row); }
    return rows;
  }

  function table(text) {
    const parsed = parseRows(String(text));
    if (!parsed || !parsed.length) return null;
    const fields = parsed[0];
    const cells = parsed.slice(1);
    const rows = cells.map(r => Object.fromEntries(fields.map((f, i) => [f, r[i] ?? ''])));
    return {fields, cells, rows};
  }

  function parseCsv(text) {
    const t = table(text);
    return t ? {fields: t.fields, rows: t.rows} : {fields: [], rows: []};
  }

  function verifyCsv({status, echo, requestId, rowCount, text, journeyId, state}) {
    if (status !== 200 || !requestId || echo !== requestId || !state || journeyId == null) return false;
    const count = /^\d+$/.test(String(rowCount)) ? Number(rowCount) : 0;
    if (count < 1) return false;
    const t = table(text);
    if (!t || t.fields.length !== COLUMNS.length || t.fields.some((f, i) => f !== COLUMNS[i])) return false;
    if (t.rows.length !== count || t.cells.some(r => r.length !== COLUMNS.length)) return false;
    const id = String(journeyId);
    return t.rows.every(r => r.session_id === id && r.demo_response_source === 'prepared_example' &&
      r.demo_external_ai_called === 'false' && r.demo_content_version === state.content_version &&
      r.demo_fixture_version === state.fixture_version && r.demo_record_origin === 'visitor_generated') &&
      t.rows.some(r => r.role === 'summary');
  }

  const storageKey = (scope, generation) => `demo:${scope}:${generation}:tour-v1`;

  /* ---------- runtime state ---------- */

  let cfg = null, root = null, panel = null, statusEl = null, resumeBtn = null, titleEl = null, csvBtn = null;
  let confirmed = {scope: null, generation: null, auth_epoch: null};
  // The generation whose facts/observations are shown; it only moves on a ready state (§24.1).
  let journeyNs = {scope: null, generation: null};
  let lastState = null, facts = null, observations = [], map = null;
  let closed = false, skipped = false, frozen = false, expired = false, resetRan = false;
  let staleKey = null, checking = null, rerun = false, staleRetry = false;
  let pending = null, controlBusy = false, csvBusy = false, channel = null;
  // Observations can arrive before the journey's facts are known; hold a few until they are.
  let early = [];

  const tr = key => (window.uiLanguage ? window.uiLanguage.t(key) : key);
  const clone = value => (value == null ? value : JSON.parse(JSON.stringify(value)));
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const toInt = value => {
    const n = value == null || value === '' ? NaN : Number(value);
    return Number.isInteger(n) ? n : null;
  };
  const store = {
    get(key) { try { return localStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { localStorage.setItem(key, value); } catch { /* memory only */ } },
    del(key) { try { localStorage.removeItem(key); } catch { /* memory only */ } },
  };

  function setStatus(text) { if (statusEl) statusEl.textContent = text; }
  function currentKey() {
    return journeyNs.scope != null && journeyNs.generation != null
      ? storageKey(journeyNs.scope, journeyNs.generation) : null;
  }
  function nsHeaders(ns) {
    const h = {};
    if (ns.scope != null) h['X-Demo-Scope'] = ns.scope;
    if (ns.generation != null) h['X-Demo-Generation'] = String(ns.generation);
    if (ns.auth_epoch != null) h['X-Demo-Auth-Epoch'] = String(ns.auth_epoch);
    return h;
  }
  function sameNs(a, b) {
    return a.scope === b.scope && a.generation === b.generation && a.auth_epoch === b.auth_epoch;
  }
  function csrfToken() {
    for (const part of document.cookie.split(';')) {
      const i = part.indexOf('=');
      if (part.slice(0, i).trim() === cfg.csrf_cookie_name) {
        try { return decodeURIComponent(part.slice(i + 1).trim()); } catch { return null; }
      }
    }
    return null;
  }
  async function readJson(response) {
    try { return await response.json(); } catch { return null; }
  }
  const errorDetail = body => (body && body.detail && typeof body.detail === 'object' &&
    !Array.isArray(body.detail) ? body.detail : {});

  /* ---------- events and tab notification ---------- */

  function emit(name, state, reason) {
    const detail = {
      browser_scope_id: state.browser_scope_id ?? null,
      generation: state.generation ?? null,
      auth_epoch: state.auth_epoch ?? null,
      state: state.state ?? null,
      role: state.role ?? null,
      contract_version: state.contract_version ?? null,
      content_version: state.content_version ?? null,
      fixture_version: state.fixture_version ?? null,
      reason: reason || null,
    };
    document.dispatchEvent(new CustomEvent(name, {detail}));
    try {
      if (channel) channel.postMessage(detail);
      else store.set(NOTIFY_KEY, JSON.stringify(detail));
    } catch { /* the server remains the final defence */ }
  }

  // A notification is only a hint to re-check; its content is never applied.
  function onNotify(detail) {
    if (!detail || typeof detail !== 'object') return;
    if (confirmed.scope != null && detail.browser_scope_id !== confirmed.scope) return;
    const g = confirmed.generation || 0, e = confirmed.auth_epoch || 0;
    if (detail.generation > g || (detail.generation === g && detail.auth_epoch > e)) check('notification');
  }

  /* ---------- tour record (localStorage) ---------- */

  function cleanObs(d) {
    if (!d || typeof d !== 'object' || d.schema_version !== 1 || !KINDS.includes(d.kind) ||
        typeof d.request_id !== 'string' || !UUID.test(d.request_id) || typeof d.browser_scope_id !== 'string' ||
        !Number.isInteger(d.generation) || !Number.isInteger(d.auth_epoch) ||
        !Number.isInteger(d.journey_session_id)) return null;
    return {schema_version: 1, browser_scope_id: d.browser_scope_id, generation: d.generation,
      auth_epoch: d.auth_epoch, journey_session_id: d.journey_session_id, kind: d.kind, request_id: d.request_id};
  }
  const isCurrentNs = o => o.browser_scope_id === journeyNs.scope && o.generation === journeyNs.generation;

  function parseRecord(raw) {
    try {
      const rec = JSON.parse(raw);
      if (!rec || typeof rec !== 'object' || rec.schema_version !== 1) return null;
      return {
        journey_session_id: Number.isInteger(rec.journey_session_id) ? rec.journey_session_id : null,
        observations: (Array.isArray(rec.observations) ? rec.observations : [])
          .map(cleanObs).filter(o => o && isCurrentNs(o)),
        closed: rec.closed === true,
        skipped: rec.skipped === true,
      };
    } catch { return null; }
  }
  function readRecord(key) {
    const raw = store.get(key);
    return raw == null ? null : parseRecord(raw);
  }
  const journeyId = () => (facts && facts.journey_session_id != null ? facts.journey_session_id : null);

  function loadRecord() {
    const key = currentKey();
    const rec = key ? readRecord(key) : null;
    const journey = journeyId();
    const usable = rec && (journey == null || rec.journey_session_id == null || rec.journey_session_id === journey);
    observations = usable ? rec.observations : [];
    closed = rec ? rec.closed : false;
    skipped = rec ? rec.skipped : false;
  }

  // Read right before writing so a concurrent tab's observation is merged, never overwritten.
  function persist() {
    const key = currentKey();
    if (!key) return;
    const stored = readRecord(key);
    const journey = journeyId() ?? (stored ? stored.journey_session_id : null);
    const same = o => journey == null || o.journey_session_id === journey;
    observations = mergeObservations(observations.filter(same), stored ? stored.observations.filter(same) : []);
    store.set(key, JSON.stringify({schema_version: 1, journey_session_id: journey, observations, closed, skipped}));
  }

  function onStorage(event) {
    if (event.key === NOTIFY_KEY) {
      try { onNotify(JSON.parse(event.newValue)); } catch { /* ignore malformed hints */ }
      return;
    }
    const key = currentKey();
    if (!key || event.key !== key || event.newValue == null) return;
    const rec = parseRecord(event.newValue);
    if (!rec) return;
    const journey = journeyId();
    if (journey == null || rec.journey_session_id == null || rec.journey_session_id === journey) {
      observations = mergeObservations(observations, rec.observations);
    }
    closed = rec.closed;
    skipped = rec.skipped;
    render();
  }

  function accept(detail) {
    if (facts == null) {
      if (early.length < 20) early.push(detail);
      return false;
    }
    const o = cleanObs(detail);
    const journey = journeyId();
    if (!o || journey == null || o.journey_session_id !== journey) return false;
    if (o.browser_scope_id !== confirmed.scope || o.generation !== confirmed.generation ||
        o.auth_epoch !== confirmed.auth_epoch || o.generation !== journeyNs.generation) return false;
    observations = mergeObservations(observations, [o]);
    persist();
    render();
    return true;
  }

  /* ---------- render ---------- */

  function render() {
    map = frozen ? Object.fromEntries(STEPS.map(step => [step, 'complete'])) : computeSteps(facts, observations, false);
    if (!panel) return;
    const hidden = closed || skipped;
    panel.hidden = hidden;
    if (resumeBtn) resumeBtn.hidden = !hidden;
    const labels = {complete: panel.dataset.labelComplete, current: panel.dataset.labelCurrent,
      pending: panel.dataset.labelPending};
    for (const li of panel.querySelectorAll('[data-demo-step]')) {
      const value = map[li.dataset.demoStep] || 'pending';
      li.dataset.state = value;
      const label = li.querySelector('.demo-step-status');
      if (label) label.textContent = labels[value] || '';
      if (value === 'current') li.setAttribute('aria-current', 'step');
      else li.removeAttribute('aria-current');
    }
    const role = lastState ? lastState.role : null;
    for (const b of panel.querySelectorAll('[data-demo-action="role"]')) {
      b.hidden = expired || b.dataset.demoRole === role;
    }
    for (const b of panel.querySelectorAll('[data-demo-action="reset"]')) b.hidden = expired;
    if (csvBtn) {
      csvBtn.hidden = !(!expired && role === 'admin' && lastState && lastState.state === 'ready' &&
        journeyId() != null && map.analysis === 'complete');
    }
  }

  /* ---------- state GET ---------- */

  function apply(s) {
    if (!s || typeof s !== 'object' || typeof s.state !== 'string' ||
        !Number.isInteger(s.generation) || !Number.isInteger(s.auth_epoch)) return false;
    const cg = confirmed.generation, ce = confirmed.auth_epoch;
    // Never move backwards: an older response is dropped without event or render.
    if (cg != null && (s.generation < cg || (s.generation === cg && ce != null && s.auth_epoch < ce))) return true;
    const prev = lastState || {};
    let name = 'demo:state';
    if (s.state === 'expired') name = 'demo:expired';
    else if (s.state === 'resetting') { if (prev.state !== 'resetting') name = 'demo:reset-started'; }
    // A failed reset only reaches ready again through a successful reset retry (same generation).
    else if (s.state === 'ready' && ((prev.generation != null && s.generation > prev.generation) ||
        prev.state === 'resetting' || prev.state === 'reset_failed')) name = 'demo:reset-completed';
    else if (prev.generation === s.generation && s.auth_epoch > prev.auth_epoch) name = 'demo:role-changed';

    confirmed = {scope: s.browser_scope_id, generation: s.generation, auth_epoch: s.auth_epoch};
    lastState = clone(s);
    expired = s.state === 'expired';
    let notice = null;
    const matches = f => f && f.browser_scope_id === s.browser_scope_id && f.generation === s.generation;
    const moved = journeyNs.scope !== s.browser_scope_id || journeyNs.generation !== s.generation;
    if (s.state === 'ready' && moved) {
      // A reserved generation is not a reset success: the old journey stays until ready (§24.1).
      if (journeyNs.generation != null) {
        if (resetRan) {
          if (computeSteps(facts, observations, true).reset === 'complete') frozen = true;
          notice = panel ? panel.dataset.labelResetDone : null;
        }
        const oldKey = currentKey();
        if (oldKey) store.del(oldKey);
      }
      resetRan = false;
      journeyNs = {scope: s.browser_scope_id, generation: s.generation};
      facts = matches(s.tour_facts) ? clone(s.tour_facts) : null;
      loadRecord();
    } else if (s.state === 'ready' && matches(s.tour_facts)) {
      facts = clone(s.tour_facts);
    }
    if (s.state === 'reset_failed') resetRan = false;

    let text = notice;
    const rootNs = {scope: root.dataset.demoScope || null, generation: toInt(root.dataset.demoGeneration),
      auth_epoch: toInt(root.dataset.demoAuthEpoch)};
    if (!sameNs(rootNs, confirmed)) {
      if (panel) panel.dataset.stale = 'true';
      const key = `${confirmed.scope}:${confirmed.generation}:${confirmed.auth_epoch}`;
      if (key !== staleKey) {
        staleKey = key;
        text = text || tr(rootNs.generation !== confirmed.generation || rootNs.scope !== confirmed.scope
          ? 'demo.stale_generation' : 'demo.stale_role');
      }
    } else {
      if (panel) delete panel.dataset.stale;
      staleKey = null;
    }
    if (text) setStatus(text);
    render();
    emit(name, s, s.last_error);
    return true;
  }

  // Facts for the namespace the plain state GET just confirmed; never an event of its own.
  function applyFacts(s) {
    if (!s || !lastState || lastState.state !== 'ready' || s.browser_scope_id !== confirmed.scope ||
        s.generation !== confirmed.generation || s.auth_epoch !== confirmed.auth_epoch) return;
    const f = s.tour_facts;
    if (!f || f.browser_scope_id !== journeyNs.scope || f.generation !== journeyNs.generation) return;
    const first = facts == null || facts.journey_session_id !== f.journey_session_id;
    facts = clone(f);
    if (first) loadRecord();
    render();
    early.splice(0).forEach(accept);
  }

  function expire(detail, code, fallbackKey) {
    expired = true;
    lastState = {...(lastState || cfg), state: 'expired'};
    setStatus(tr(detail.recovery || fallbackKey));
    render();
    emit('demo:expired', lastState, code);
  }

  // §9.1: a bootstrap-bound current-state GET (no old selectors) adopts the namespace and cookies first.
  async function fetchState() {
    let response;
    try {
      response = await fetch(cfg.state_url, {credentials: 'same-origin', cache: 'no-store'});
    } catch {
      setStatus(tr('demo.retry_later'));
      return;
    }
    const body = await readJson(response);
    if (response.status !== 200) {
      const detail = errorDetail(body);
      if (response.status === 410) expire(detail, detail.code || 'DEMO_EXPIRED', 'demo.expired');
      else if (response.status === 401) expire(detail, 'DEMO_SESSION_REQUIRED', 'demo.start_required');
      else setStatus(tr(detail.recovery || 'demo.retry_later'));
      return;
    }
    if (!apply(body)) { setStatus(tr('demo.retry_later')); return; }
    // Dropped as older than the confirmed namespace, or not ready: no facts to fetch.
    if (lastState.generation !== body.generation || lastState.auth_epoch !== body.auth_epoch ||
        lastState.state !== 'ready') return;
    let tour;
    try {
      tour = await fetch(`${cfg.state_url}?include_tour=true`, {credentials: 'same-origin', cache: 'no-store',
        headers: nsHeaders(confirmed)});
    } catch {
      return;
    }
    const factsBody = await readJson(tour);
    if (tour.status === 200) applyFacts(factsBody);
    // The namespace moved between the two GETs: resync once more.
    else if (tour.status === 409 && /^DEMO_STALE_/.test(errorDetail(factsBody).code || '') && !staleRetry) {
      staleRetry = true;
      rerun = true;
    }
  }

  // Single flight: calls made while a check runs collapse into exactly one rerun.
  function check() {
    if (!cfg) return Promise.resolve();
    if (checking) { rerun = true; return checking; }
    staleRetry = false;
    checking = (async () => {
      try {
        do { rerun = false; await fetchState(); } while (rerun);
      } catch { setStatus(tr('demo.retry_later')); } finally { checking = null; }
    })();
    return checking;
  }

  /* ---------- control POSTs (start / role / reset) ---------- */

  function setPending(op) {
    pending = op;
    try {
      if (op) sessionStorage.setItem(CONTROL_KEY, JSON.stringify(op));
      else sessionStorage.removeItem(CONTROL_KEY);
    } catch { /* memory only */ }
  }
  function readPending() {
    try {
      const v = JSON.parse(sessionStorage.getItem(CONTROL_KEY));
      return v && ['start', 'role', 'reset'].includes(v.operation) && UUID.test(v.request_id) &&
        v.body && typeof v.body === 'object' ? v : null;
    } catch { return null; }
  }
  function done(op) { if (pending && pending.request_id === op.request_id) setPending(null); }
  const getLookup = op => fetch(`${cfg.state_url}?request_id=${encodeURIComponent(op.request_id)}`,
    {credentials: 'same-origin', cache: 'no-store'});
  function go(next) {
    if (typeof next === 'string' && /^\/(?!\/)/.test(next)) location.assign(next);
    else location.reload();
  }

  async function fail(op, response, body, fromLookup) {
    const d = errorDetail(body);
    if (response.status === 410 || response.status === 401) {
      done(op);
      resetRan = false;
      expire(d, response.status === 401 ? 'DEMO_SESSION_REQUIRED' : (d.code || 'DEMO_EXPIRED'),
        response.status === 401 ? 'demo.start_required' : 'demo.expired');
      return;
    }
    const retryAfter = response.headers.get('Retry-After');
    const text = tr(d.recovery || 'demo.retry_later') + (retryAfter ? ` (${retryAfter}s)` : '');
    setStatus(text);
    // A CSRF failure never re-posts: refresh the cookie, then ask what happened to this ID.
    if (response.status === 403 && d.code === 'DEMO_CSRF_INVALID' && !fromLookup) {
      await check('csrf');
      await lookup(op);
      return;
    }
    // A finished failure (§9.1): resync, resolve this ID's receipt, then retire it so a new
    // attempt gets a new ID with the current expected generation/epoch.
    await check('failure');
    if (!fromLookup) {
      let receipt = null;
      try { receipt = await getLookup(op); } catch { /* the failure itself is known */ }
      if (receipt && receipt.status === 200) { done(op); await check('control'); return; }
      if (receipt && receipt.status === 202) { await poll(op); return; }
    }
    done(op);
    if (op.operation === 'reset') resetRan = false;
    setStatus(text);
  }

  async function resolveLookup(op, response) {
    if (response.status === 200) { done(op); await check('control'); }
    else if (response.status === 202) await poll(op);
    else if (response.status !== 404) await fail(op, response, await readJson(response), true);
  }
  async function lookup(op) {
    try { await resolveLookup(op, await getLookup(op)); } catch { setStatus(tr('demo.retry_later')); }
  }
  async function poll(op) {
    for (let i = 0; i < 60; i++) {
      await sleep(1000);
      let response;
      try { response = await getLookup(op); } catch { continue; }
      if (response.status === 202) continue;
      await resolveLookup(op, response);
      return;
    }
    setStatus(tr('demo.retry_later'));
  }

  async function run(op) {
    controlBusy = true;
    try {
      const headers = {'Content-Type': 'application/json'};
      const token = csrfToken();
      if (token) headers[cfg.csrf_header_name] = token;
      let response, body = null;
      try {
        response = await fetch(cfg[`${op.operation}_url`], {method: 'POST', credentials: 'same-origin', headers,
          body: JSON.stringify({request_id: op.request_id, ...op.body})});
        body = await readJson(response);
      } catch { /* result unknown: fall through to the lookup */ }
      if (body === null) {
        setStatus(tr('demo.retry_later'));
        await lookup(op);
      } else if (response.status === 200 || response.status === 201) {
        done(op);
        const receipt = body.result !== undefined || body.historical !== undefined;
        await check('control');
        if (op.operation !== 'reset') go(receipt ? null : body.next_url);
      } else if (response.status === 202) {
        await check('control');
        await poll(op);
      } else {
        await fail(op, response, body, false);
      }
    } finally { controlBusy = false; }
  }

  function begin(operation, body) {
    if (controlBusy || typeof crypto === 'undefined' || !crypto.randomUUID) return;
    // Retrying the same intent must keep its ID and original body.
    const same = pending && pending.operation === operation && (operation === 'reset' || pending.body.role === body.role);
    const op = same ? pending : {operation, request_id: crypto.randomUUID(), body};
    setPending(op);
    if (operation === 'reset') resetRan = true;
    run(op);
  }

  /* ---------- tour CSV ---------- */

  async function tourCsv() {
    const journey = journeyId();
    if (csvBusy || journey == null || typeof crypto === 'undefined' || !crypto.randomUUID) return;
    const ns = {...confirmed};
    const req = crypto.randomUUID();
    csvBusy = true;
    csvBtn.disabled = true;
    let ok = false, failureText = null;
    try {
      const query = `ds=${encodeURIComponent(ns.scope)}&dg=${ns.generation}&da=${ns.auth_epoch}`;
      const response = await fetch(`/admin/sessions/${journey}/download?${query}`,
        {credentials: 'same-origin', headers: {'X-Demo-Export-Request-ID': req, ...nsHeaders(ns)}});
      // Bytes, not text(): the saved file keeps the BOM the server sent.
      const bytes = await response.arrayBuffer();
      const text = new TextDecoder('utf-8', {fatal: true, ignoreBOM: true}).decode(bytes);
      ok = verifyCsv({status: response.status, echo: response.headers.get('X-Demo-Export-Request-ID'),
        requestId: req, rowCount: response.headers.get('X-Demo-Export-Row-Count'), text, journeyId: journey,
        state: lastState}) && sameNs(ns, confirmed);
      if (ok) {
        const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(response.headers.get('Content-Disposition') || '');
        let name = `demo_d0-02-v1_admin_${journey}.csv`;
        if (match) { try { name = decodeURIComponent(match[1]).replace(/[\\/]/g, '_'); } catch { /* keep default */ } }
        const url = URL.createObjectURL(new Blob([bytes], {type: 'text/csv;charset=utf-8'}));
        const link = document.createElement('a');
        link.href = url;
        link.download = name;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 10000);
      } else if (response.status !== 200) {
        try { failureText = tr(errorDetail(JSON.parse(text)).recovery || 'demo.retry_later'); } catch { /* not JSON */ }
      }
    } catch { ok = false; } finally {
      csvBusy = false;
      csvBtn.disabled = false;
    }
    if (!ok) { setStatus(failureText || tr('demo.retry_later')); return; }
    accept({schema_version: 1, browser_scope_id: ns.scope, generation: ns.generation, auth_epoch: ns.auth_epoch,
      journey_session_id: journey, kind: 'csv_received', request_id: req});
  }

  /* ---------- guide actions and wiring ---------- */

  function setGuide(nextClosed, nextSkipped) {
    closed = nextClosed;
    skipped = nextSkipped;
    persist();
    render();
  }

  function onClick(event) {
    if (event.target.closest('#demo-tour-csv')) { tourCsv(); return; }
    const button = event.target.closest('[data-demo-action]');
    if (!button) return;
    const role = button.dataset.demoRole;
    switch (button.dataset.demoAction) {
      case 'start':
        if (role === 'teacher' || role === 'admin') begin('start', {role});
        break;
      case 'role':
        if (confirmed.generation == null) { setStatus(tr('demo.start_required')); break; }
        begin('role', {role, expected_generation: confirmed.generation, expected_auth_epoch: confirmed.auth_epoch});
        break;
      case 'reset':
        if (confirmed.generation == null) { setStatus(tr('demo.start_required')); break; }
        if (window.confirm(button.dataset.confirm || '')) {
          begin('reset', {expected_generation: confirmed.generation, expected_auth_epoch: confirmed.auth_epoch,
            confirm: true});
        }
        break;
      case 'close':
      case 'skip':
        setGuide(button.dataset.demoAction === 'close', button.dataset.demoAction === 'skip');
        if (resumeBtn) resumeBtn.focus();
        break;
      case 'resume':
        setGuide(false, false);
        if (titleEl) titleEl.focus();
        break;
      case 'restart':
        if (panel && panel.dataset.stale === 'true') {
          location.assign((lastState && lastState.next_url) || '/demo');
          break;
        }
        setGuide(false, false);
        check('restart');
        if (titleEl) titleEl.focus();
        break;
    }
  }

  function init() {
    const el = document.getElementById('demo-config');
    if (!el) return;
    try { cfg = JSON.parse(el.textContent); } catch { cfg = null; }
    if (!cfg || cfg.mode !== 'demo') { cfg = null; return; }
    root = document.documentElement;
    panel = document.getElementById('demo-tour');
    statusEl = document.getElementById('demo-tour-status');
    titleEl = document.getElementById('demo-tour-title');
    csvBtn = document.getElementById('demo-tour-csv');
    resumeBtn = document.querySelector('[data-demo-action="resume"]');
    confirmed = {scope: root.dataset.demoScope || null, generation: toInt(root.dataset.demoGeneration),
      auth_epoch: toInt(root.dataset.demoAuthEpoch)};
    journeyNs = {scope: confirmed.scope, generation: confirmed.generation};
    lastState = clone(cfg);
    facts = cfg.tour_facts ? clone(cfg.tour_facts) : null;
    loadRecord();
    render();
    if (typeof BroadcastChannel === 'function') {
      try {
        channel = new BroadcastChannel(CHANNEL);
        channel.onmessage = event => onNotify(event.data);
      } catch { channel = null; }
    }
    window.addEventListener('storage', onStorage);
    window.addEventListener('pageshow', event => { if (event.persisted) check('pageshow'); });
    document.addEventListener('demo:tour-observed', event => accept(event.detail));
    // The reason is a hint only; the next state GET is the sole source of truth.
    document.addEventListener('demo:state-check', () => check('state-check'));
    document.addEventListener('click', onClick);
    pending = readPending();
    if (pending && pending.operation === 'reset') resetRan = true;
    (async () => {
      if (pending) await lookup(pending);
      await check('load');
    })();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  return {
    STEPS, computeSteps, mapString, mergeObservations, parseCsv, verifyCsv, storageKey, check,
    snapshot: () => ({state: clone(lastState), facts: clone(facts), observations: clone(observations),
      map: clone(map), closed, skipped}),
  };
})();
