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
      if (!response.ok) throw new Error('Request failed');
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
      analysisModalBody.innerHTML = sanitizeHTML(html);
      mountAnalysisResults(analysisModalBody);
      var close = analysisModalBody.querySelector('.close-modal-btn');
      if (close && !analysisModal.contains(document.activeElement)) close.focus();
    })
    .catch(function () {
      if (requestId !== analysisModalRequest) return;
      var loading = analysisModalBody.querySelector('[role="status"]');
      if (loading) { loading.replaceChildren(); loading.removeAttribute('role'); }
      modalLifecycle.error(analysisModal, uiLanguage.t('teacher.analysis_load_failed'));
    }).finally(function () { sessionPendingRequests--; });
}

function closeAnalysisModal() {
  ++analysisModalRequest;
  analysisModal.classList.add('hidden');
  analysisModal.classList.remove('flex');
  analysisModalBody.innerHTML = '';
  modalLifecycle.close(analysisModal);
}

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
  var match = document.cookie.match(
    /(?:^|;\s*)csrftoken=([^;]*)/
  );
  return match ? match[1] : '';
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

btnDownloadSelected.addEventListener('click', function () {
  var ids = getCheckedIds();
  if (ids.length === 0) return;

  var originalText = btnDownloadSelected.textContent;
  btnDownloadSelected.disabled = true;
  btnDownloadSelected.textContent = uiLanguage.t('admin.downloading');

  var body = new URLSearchParams();
  ids.forEach(function (id) { body.append('session_ids', id); });

  sessionPendingRequests++;
  fetch('/admin/sessions/export-selected', {
    method: 'POST',
    headers: uiLanguage.headers('/admin/sessions/export-selected', {
      'Content-Type': 'application/x-www-form-urlencoded',
      'x-csrf-token': getCsrfToken(),
    }),
    credentials: 'same-origin',
    body: body,
  })
    .then(function (response) {
      if (!response.ok) {
        return response.text().then(function (t) {
          throw new Error(t || uiLanguage.t('admin.download_failed'));
        });
      }
      return response.blob().then(function (blob) {
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
      });
    })
    .catch(function () {
      modalLifecycle.error(document.querySelector('.table-container'),
        uiLanguage.t('admin.selected_download_failed'));
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
      event.type === 'htmx:sendError'
        ? uiLanguage.t('admin.session_action_unknown')
        : uiLanguage.t('admin.session_action_failed'));
  });
}
