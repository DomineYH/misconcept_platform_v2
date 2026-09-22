/* Issue #28 / #33 — Analysis detail panel toggle and tab navigation */

function getAnalysisCsrfHeaders() {
  var match = document.cookie.match(
    /(?:^|;\s*)csrftoken=([^;]*)/
  );
  return match ? { 'x-csrf-token': match[1] } : {};
}

function activateAnalysisTab(panel, tabId) {
  var tabs = panel.querySelectorAll('[role="tab"]');
  tabs.forEach(function (t) {
    var selected = t.id === tabId;
    t.setAttribute('aria-selected', String(selected));
    t.setAttribute('tabindex', selected ? '0' : '-1');
    var target = panel.querySelector('#' + t.getAttribute('aria-controls'));
    if (target) target.hidden = !selected;
  });
  var active = panel.querySelector('#' + tabId);
  if (active) active.focus({ preventScroll: true });
}

function bindAnalysisTabs(panel) {
  if (!panel || panel.dataset.tabsBound === '1') return;
  var tabs = Array.prototype.slice.call(
    panel.querySelectorAll('[role="tab"]')
  );
  if (tabs.length === 0) return;

  tabs.forEach(function (tab, idx) {
    tab.addEventListener('click', function () {
      activateAnalysisTab(panel, tab.id);
    });
    tab.addEventListener('keydown', function (e) {
      var newIdx = idx;
      if (e.key === 'ArrowRight') newIdx = (idx + 1) % tabs.length;
      else if (e.key === 'ArrowLeft') newIdx = (idx - 1 + tabs.length) % tabs.length;
      else if (e.key === 'Home') newIdx = 0;
      else if (e.key === 'End') newIdx = tabs.length - 1;
      else return;
      e.preventDefault();
      activateAnalysisTab(panel, tabs[newIdx].id);
    });
  });
  panel.dataset.tabsBound = '1';
}

function toggleAnalysisDetail(btn) {
  var expanded = btn.getAttribute('aria-expanded') === 'true';
  var panel = document.getElementById(btn.getAttribute('aria-controls'));
  if (!panel) return;
  btn.setAttribute('aria-expanded', String(!expanded));
  panel.hidden = expanded;
  btn.textContent = expanded ? '상세 분석' : '상세 분석 접기';

  if (!expanded) {
    bindAnalysisTabs(panel);

    var sessionId = btn.getAttribute('data-session-id');
    if (!sessionId) {
      var wedge = btn.closest('.analysis-wedge');
      sessionId = wedge ? wedge.getAttribute('data-session-id') : null;
    }
    if (sessionId) {
      fetch('/sessions/' + sessionId + '/analysis/detail-opened', {
        method: 'POST',
        headers: getAnalysisCsrfHeaders(),
        credentials: 'same-origin',
      }).catch(function () { /* silent */ });
    }
  }
}

document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') {
    var panel = document.getElementById('analysis-detail-panel');
    if (panel && !panel.hidden) {
      var toggle = document.querySelector(
        '[aria-controls="analysis-detail-panel"]'
      );
      if (toggle) {
        toggleAnalysisDetail(toggle);
        toggle.focus();
      }
    }
  }
});
