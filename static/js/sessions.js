/* Sessions page: filters, modals, bulk download */

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
  return fetch(url, { credentials: 'same-origin' })
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

function showAnalysisModal(sessionId) {
  analysisModalBody.innerHTML =
    '<div style="text-align:center;padding:2rem;">' +
    '<span class="spinner spinner-md"></span>' +
    ' 분석 결과 로딩 중...</div>';
  analysisModal.classList.remove('hidden');
  analysisModal.classList.add('flex');

  fetchWithRetry(
    '/admin/sessions/' + sessionId + '/analysis_modal'
  )
    .then(function (html) {
      analysisModalBody.innerHTML = sanitizeHTML(html);
    })
    .catch(function () {
      analysisModalBody.innerHTML =
        '<div style="text-align:center;padding:2rem;' +
        'color:var(--color-danger);">' +
        '분석 결과를 불러올 수 없습니다.</div>';
    });
}

function closeAnalysisModal() {
  analysisModal.classList.add('hidden');
  analysisModal.classList.remove('flex');
  analysisModalBody.innerHTML = '';
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
  selectedCountEl.textContent = count + '개 선택됨';
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
  btnDownloadSelected.textContent = '다운로드 중...';

  var body = new URLSearchParams();
  ids.forEach(function (id) { body.append('session_ids', id); });

  fetch('/admin/sessions/export-selected', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      'x-csrf-token': getCsrfToken(),
    },
    credentials: 'same-origin',
    body: body,
  })
    .then(function (response) {
      if (!response.ok) {
        return response.text().then(function (t) {
          throw new Error(t || '다운로드 실패');
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
    .catch(function (err) {
      alert('다운로드 실패: ' + err.message);
    })
    .finally(function () {
      btnDownloadSelected.textContent = originalText;
      updateSelectedUI();
    });
});

/* Re-sync after HTMX swaps */
document.body.addEventListener('htmx:afterSwap', function () {
  updateSelectedUI();
  updateDownloadAllHref();
});

/* Close modals on Escape */
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') {
    if (!analysisModal.classList.contains('hidden')) {
      closeAnalysisModal();
    }
  }
});
