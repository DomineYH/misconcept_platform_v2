/* Sessions page: filters, modals, bulk download */
var sessionPendingRequests = 0;
uiLanguage.registerGuard(function () { return {busy:sessionPendingRequests > 0}; });

function submitFilters() {
  var form = document.getElementById('filter-form');
  var params = new URLSearchParams();
  for (var i = 0; i < form.elements.length; i++) {
    var el = form.elements[i];
    if (el.name && el.value !== '') {
      params.set(el.name, el.value);
    }
  }
  var qs = params.toString();
  window.location.href =
    window.location.pathname + (qs ? '?' + qs : '');
}

function fetchWithRetry(url, retries) {
  if (retries === undefined) retries = 1;
  return fetch(url, { credentials: 'same-origin', headers:uiLanguage.headers(url) })
    .then(function (response) {
      if (response.redirected ||
          response.url.includes('/login')) {
        if (retries > 0) {
          return fetchWithRetry(url, retries - 1);
        }
        throw new Error('Session expired');
      }
      if (!response.ok) {
        // A demo envelope carries its own hint and is never retried.
        return uiLanguage.responseRecovery(response).then(function (hint) {
          var error = new Error('Request failed');
          error.hint = hint;
          throw error;
        });
      }
      return response.text();
    });
}

function sanitizeHTML(html) {
  var parser = new DOMParser();
  var doc = parser.parseFromString(html, 'text/html');
  doc.querySelectorAll('script')
    .forEach(function (el) { el.remove(); });
  return doc.body.innerHTML;
}

function showModal(id) {
  var modal = document.getElementById(id);
  modal.classList.remove('hidden');
  modal.classList.add('flex');
}

function hideModal(id) {
  var modal = document.getElementById(id);
  modal.classList.add('hidden');
  modal.classList.remove('flex');
}

/* --- Analysis Modal --- */
var analysisModal = document.getElementById('analysis-modal');
var analysisModalBody =
  document.getElementById('analysis-modal-body');
var analysisModalRequest = 0;

function showAnalysisModal(sessionId) {
  var requestId = ++analysisModalRequest;
  var captured = uiLanguage.storageKey('');
  analysisModalBody.replaceChildren(document.getElementById('admin-session-loading').content.cloneNode(true));
  analysisModal.classList.remove('hidden');
  analysisModal.classList.add('flex');
  modalLifecycle.open(analysisModal, closeAnalysisModal);

  sessionPendingRequests++;
  fetchWithRetry(
    '/admin/sessions/' + sessionId + '/analysis_modal'
  )
    .then(function (html) {
      if (requestId !== analysisModalRequest) return;
      if (uiLanguage.storageKey('') !== captured) return;
      analysisModalBody.innerHTML = sanitizeHTML(html);
      mountAnalysisResults(analysisModalBody);
      var close = analysisModalBody.querySelector('.close-modal-btn');
      if (close && !analysisModal.contains(document.activeElement)) close.focus();
      uiLanguage.observe('admin_record_viewed', sessionId, captured);
    })
    .catch(function (error) {
      if (requestId !== analysisModalRequest) return;
      var loading = analysisModalBody.querySelector('[role="status"]');
      if (loading) { loading.replaceChildren(); loading.removeAttribute('role'); }
      modalLifecycle.error(analysisModal,
        (error && error.hint) || uiLanguage.t('teacher.analysis_load_failed'));
    }).finally(function () { sessionPendingRequests--; });
}

function closeAnalysisModal() {
  ++analysisModalRequest;
  analysisModal.classList.add('hidden');
  analysisModal.classList.remove('flex');
  analysisModalBody.innerHTML = '';
  modalLifecycle.close(analysisModal);
}

// A new demo namespace drops in-flight responses and closes the stale record.
uiLanguage.registerStale(function () {
  ++analysisModalRequest;
  if (!analysisModal.classList.contains('hidden')) closeAnalysisModal();
});

analysisModal.addEventListener('click', function (e) {
  if (e.target === this) closeAnalysisModal();
});

document.addEventListener('click', function (e) {
  var btn = e.target.classList.contains('view-analysis-btn')
    ? e.target
    : e.target.closest('.view-analysis-btn');
  if (btn) showAnalysisModal(btn.dataset.sessionId);
});

/* --- Bulk Download --- */
var selectAll = document.getElementById('select-all');
var btnDownloadAll = document.getElementById('btn-download-all');
var btnDownloadSelected =
  document.getElementById('btn-download-selected');
var selectedCountEl = document.getElementById('selected-count');

function getCsrfToken() {
  return uiLanguage.csrfToken() ?? '';
}

function buildFilterQuery() {
  var form = document.getElementById('filter-form');
  var params = new URLSearchParams();
  for (var i = 0; i < form.elements.length; i++) {
    var el = form.elements[i];
    if (el.name && el.value !== '') {
      params.set(el.name, el.value);
    }
  }
  return params.toString();
}

function updateDownloadAllHref() {
  var qs = buildFilterQuery();
  btnDownloadAll.href =
    '/admin/sessions/export' + (qs ? '?' + qs : '');
}
updateDownloadAllHref();

function getCheckboxes() {
  return document.querySelectorAll('.session-checkbox');
}

function getCheckedIds() {
  return Array.from(getCheckboxes())
    .filter(function (cb) { return cb.checked; })
    .map(function (cb) { return cb.value; });
}

function updateSelectedUI() {
  var checked = getCheckedIds();
  var count = checked.length;
  selectedCountEl.textContent = uiLanguage.t('admin.selected_count', {count:count});
  btnDownloadSelected.disabled = count === 0;

  var boxes = getCheckboxes();
  var allChecked = boxes.length > 0 &&
    Array.from(boxes).every(function (cb) {
      return cb.checked;
    });
  selectAll.checked = allChecked;
  selectAll.indeterminate = count > 0 && !allChecked;
}

selectAll.addEventListener('change', function () {
  var checked = this.checked;
  getCheckboxes().forEach(function (cb) {
    cb.checked = checked;
  });
  updateSelectedUI();
});

document.getElementById('sessions-tbody')
  .addEventListener('change', function (e) {
    if (e.target.classList.contains('session-checkbox')) {
      updateSelectedUI();
    }
  });

/* RFC 4180 parser; stray quote or unterminated quote returns null. */
function parseCsv(text) {
  var rows = [], row = [], field = '', quoted = false, closed = false;
  for (var i = 0; i < text.length; i++) {
    var c = text[i];
    if (quoted) {
      if (c !== '"') field += c;
      else if (text[i + 1] === '"') { field += '"'; i++; }
      else { quoted = false; closed = true; }
    } else if (c === '"') {
      if (field !== '' || closed) return null;
      quoted = true;
    } else if (c === ',') {
      row.push(field); field = ''; closed = false;
    } else if (c === '\r' || c === '\n') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(field); rows.push(row); row = []; field = ''; closed = false;
    } else if (closed) {
      return null;
    } else {
      field += c;
    }
  }
  if (quoted) return null;
  if (field !== '' || closed || row.length) { row.push(field); rows.push(row); }
  return rows;
}

/* §24.1 csv_received: the exact one-record journey export, nothing else. */
var DEMO_CSV_TAIL = ['demo_response_source', 'demo_external_ai_called',
  'demo_content_version', 'demo_fixture_version', 'demo_record_origin'];

function journeyCsv(buffer, response, ids, exportId) {
  if (ids.length !== 1 || response.status !== 200 ||
      response.headers.get('X-Demo-Export-Request-ID') !== exportId) return false;
  var count = response.headers.get('X-Demo-Export-Row-Count') || '';
  if (!/^\d+$/.test(count) || Number(count) < 1) return false;
  // The default TextDecoder strips exactly one leading BOM.
  var rows = parseCsv(new TextDecoder('utf-8', {fatal: true}).decode(buffer));
  if (!rows || rows.length !== Number(count) + 1) return false;
  var head = rows[0];
  if (head.length !== 32 || head[0] !== 'session_id' ||
      DEMO_CSV_TAIL.some(function (name, i) { return head[27 + i] !== name; })) return false;
  var role = head.indexOf('role');
  var summary = false;
  for (var r = 1; r < rows.length; r++) {
    var row = rows[r];
    if (row.length !== 32 || row[0] !== String(ids[0]) ||
        row[28] !== 'false' || row[31] !== 'visitor_generated') return false;
    if (role >= 0 && row[role] === 'summary') summary = true;
  }
  return summary;
}

btnDownloadSelected.addEventListener('click', function () {
  var ids = getCheckedIds();
  if (ids.length === 0) return;

  var originalText = btnDownloadSelected.textContent;
  btnDownloadSelected.disabled = true;
  btnDownloadSelected.textContent = uiLanguage.t('admin.downloading');

  var body = new URLSearchParams();
  ids.forEach(function (id) { body.append('session_ids', id); });

  var captured = uiLanguage.storageKey('');
  var exportId = captured !== '' ? crypto.randomUUID() : '';
  var initial = {
    'Content-Type': 'application/x-www-form-urlencoded',
    [uiLanguage.csrfHeader]: getCsrfToken(),
  };
  if (captured !== '') initial['X-Demo-Export-Request-ID'] = exportId;

  sessionPendingRequests++;
  fetch('/admin/sessions/export-selected', {
    method: 'POST',
    headers: uiLanguage.headers('/admin/sessions/export-selected', initial),
    credentials: 'same-origin',
    body: body,
  })
    .then(function (response) {
      if (!response.ok) {
        return uiLanguage.responseRecovery(response).then(function (hint) {
          return response.text().then(function (t) {
            var error = new Error(t || uiLanguage.t('admin.download_failed'));
            error.hint = hint;
            throw error;
          });
        });
      }
      // Demo reads bytes so the CSV can be verified; normal keeps blob().
      return (captured !== '' ? response.arrayBuffer() : response.blob()).then(function (data) {
        var blob = captured !== '' ?
          new Blob([data], {type: response.headers.get('Content-Type') || ''}) : data;
        var disposition =
          response.headers.get('Content-Disposition') || '';
        var match = disposition.match(/filename=([^;]+)/);
        var filename = match
          ? match[1].trim()
          : 'selected_sessions.csv';
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);
        // A failed check must never reach the download-failed handler.
        try {
          if (captured !== '' && journeyCsv(data, response, ids, exportId)) {
            uiLanguage.observe('csv_received', ids[0], captured, exportId);
          }
        } catch (e) {}
      });
    })
    .catch(function (error) {
      modalLifecycle.error(document.querySelector('.table-container'),
        (error && error.hint) || uiLanguage.t('admin.selected_download_failed'));
    })
    .finally(function () {
      sessionPendingRequests--;
      btnDownloadSelected.textContent = originalText;
      updateSelectedUI();
    });
});

/* Re-sync after HTMX swaps */
document.body.addEventListener('htmx:afterSwap', function () {
  updateSelectedUI();
  updateDownloadAllHref();
});

/* HTMX refusals retain rows; transport failures leave the outcome unknown. */
for (var eventName of ['htmx:responseError', 'htmx:sendError']) {
  document.body.addEventListener(eventName, function (event) {
    if (!event.detail.elt.closest('#sessions-tbody')) return;
    modalLifecycle.error(document.querySelector('.table-container'),
      event.detail.demoRecovery || (event.type === 'htmx:sendError'
        ? uiLanguage.t('admin.session_action_unknown')
        : uiLanguage.t('admin.session_action_failed')));
  });
}
