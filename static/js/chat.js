(() => {
  const root = document.getElementById('chat-config');
  if (!root || root.dataset.initialized) return;
  root.dataset.initialized = 'true';
const chatConfig = JSON.parse(document.getElementById("chat-config").textContent);
// Enable HTMX debug logging for development
  console.log('Chat initialized. Session ID:', chatConfig.sessionId);
  console.log('Polling disabled until first message is sent');

  // Server provides session_id via template variable
  window.currentSessionId = chatConfig.sessionId;
  window.studentDisplayName = chatConfig.studentName;
  let authExpiredHandled = false;
  let messageSending = false;
  let chatClosed = Boolean(chatConfig.ended);
  let lastSentContent = '';
  let sessionClosing = false;
  let analysisUnresolved = false;
  uiLanguage.registerGuard(() => ({
    dirty: Boolean(document.getElementById('teacher-input').value || lastSentContent),
    busy: messageSending || sessionClosing || analysisUnresolved ||
      ['ending', 'analyzing'].includes(document.getElementById('end-session-btn').dataset.state)
  }));
  document.addEventListener('analysis:state', event => {
    if (event.detail.sessionId === String(chatConfig.sessionId)) analysisUnresolved = event.detail.busy;
  });
  let pollingAuthFailCount = 0;
  const MAX_POLLING_AUTH_FAILS = 3;

  function syncComposer() {
    const disabled = authExpiredHandled || chatClosed || messageSending;
    if (!document.getElementById('teacher-form').dataset.studentStream || authExpiredHandled || chatClosed) {
      document.getElementById('teacher-input').disabled = disabled;
      document.querySelector('#teacher-form button[type="submit"]').disabled = disabled;
    }
    const endBtn = document.getElementById('end-session-btn');
    endBtn.disabled = authExpiredHandled || messageSending || ['ending', 'analyzing'].includes(endBtn.dataset.state);
  }

  function setAnalysisState(state) {
    const btn = document.getElementById('end-session-btn');
    const busy = ['ending', 'analyzing'].includes(state);
    btn.dataset.state = state;
    btn.textContent = {
      active: uiLanguage.t('teacher.end_and_analyze'), ending: uiLanguage.t('teacher.analysis_preparing'),
      analyzing: uiLanguage.t('teacher.analyzing'), 'ready-to-analyze': uiLanguage.t('teacher.view_analysis'), done: uiLanguage.t('teacher.view_analysis_again')
    }[state];
    btn.setAttribute('aria-busy', String(busy));
    document.getElementById('return-to-scenarios-btn').disabled = authExpiredHandled || busy || state === 'active';
    syncComposer();
  }

  function finishSending() {
    messageSending = false;
    syncComposer();
    enablePolling();
  }

  // ========================================
  // Mobile Tab Switching
  // ========================================
  document.querySelectorAll('.mobile-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      // Update active tab
      document.querySelectorAll('.mobile-tab').forEach(t => {
        t.classList.remove('active');
        t.setAttribute('aria-selected', 'false');
        t.tabIndex = -1;
      });
      tab.classList.add('active');
      tab.setAttribute('aria-selected', 'true');
      tab.tabIndex = 0;

      // Show/hide panels
      const panelId = tab.dataset.panel;
      const scenarioPanel = document.getElementById('scenario-panel');
      const chatPanel = document.getElementById('chat-panel');

      if (panelId === 'scenario') {
        scenarioPanel.classList.remove('panel-hidden');
        chatPanel.classList.add('panel-hidden');
      } else {
        scenarioPanel.classList.add('panel-hidden');
        chatPanel.classList.remove('panel-hidden');
      }
    });
  });

  // ========================================
  // Info Tab Switching (Scenario Panel)
  // ========================================
  const infoTabKey = `chat-info-tab-${chatConfig.sessionId}`;
  document.querySelectorAll('.info-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      // Update active tab
      document.querySelectorAll('.info-tab').forEach(t => {
        t.classList.remove('active');
        t.setAttribute('aria-selected', 'false');
        t.tabIndex = -1;
      });
      tab.classList.add('active');
      tab.setAttribute('aria-selected', 'true');
      tab.tabIndex = 0;

      // Show/hide tab panes
      const tabId = tab.dataset.tab;
      document.querySelectorAll('.tab-pane').forEach(pane => {
        pane.classList.remove('active');
      });

      const targetPane = document.getElementById(`tab-${tabId}`);
      if (targetPane) {
        targetPane.classList.add('active');
      }
      try { sessionStorage.setItem(infoTabKey, tabId); } catch {}
    });
  });
  try {
    const selected = sessionStorage.getItem(infoTabKey);
    Array.from(document.querySelectorAll('.info-tab'))
      .find(tab => tab.dataset.tab === selected)?.click();
  } catch {}

  const messagesContainer = document.getElementById('messages-container');
  const emptyHint = document.getElementById('chat-empty-state');
  function syncEmptyHint() {
    if (emptyHint) emptyHint.hidden = chatClosed || Boolean(
      messagesContainer.querySelector('.message, .student-run-status, .mentor-run-status')
    );
  }
  if (emptyHint) {
    new MutationObserver(syncEmptyHint).observe(messagesContainer, {childList:true});
    syncEmptyHint();
  }

  document.querySelectorAll('.mobile-tabs, .info-tabs').forEach(list => {
    list.addEventListener('keydown', event => {
      const tabs = Array.from(list.querySelectorAll('[role="tab"]'));
      const index = tabs.indexOf(document.activeElement);
      if (index < 0 || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 :
        (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      tabs[next].click();
      tabs[next].focus();
    });
  });

  // ========================================
  // Scenario Panel Collapse (Desktop Only)
  // ========================================
  const SCENARIO_PANEL_COLLAPSE_KEY = 'chat.scenarioPanel.collapsed';

  function isDesktopViewport() {
    return window.innerWidth > 768;
  }

  function applyScenarioPanelCollapsed(collapsed) {
    const panel = document.getElementById('scenario-panel');
    const toggle = document.getElementById('scenario-panel-toggle');
    if (!panel || !toggle) return;

    if (collapsed) {
      panel.setAttribute('data-collapsed', 'true');
      toggle.setAttribute('aria-expanded', 'false');
      toggle.setAttribute('aria-label', uiLanguage.t('teacher.panel_expand'));
    } else {
      panel.removeAttribute('data-collapsed');
      toggle.setAttribute('aria-expanded', 'true');
      toggle.setAttribute('aria-label', uiLanguage.t('teacher.panel_collapse'));
    }
  }

  function getStoredScenarioPanelCollapsed() {
    try {
      return localStorage.getItem(SCENARIO_PANEL_COLLAPSE_KEY) === 'true';
    } catch (e) {
      return false;
    }
  }

  function setStoredScenarioPanelCollapsed(collapsed) {
    try {
      localStorage.setItem(SCENARIO_PANEL_COLLAPSE_KEY, collapsed ? 'true' : 'false');
    } catch (e) {
      // Storage unavailable (private mode etc.) — silently ignore.
    }
  }

  function toggleScenarioPanel() {
    if (!isDesktopViewport()) return;
    const panel = document.getElementById('scenario-panel');
    if (!panel) return;
    const nextCollapsed = panel.getAttribute('data-collapsed') !== 'true';
    applyScenarioPanelCollapsed(nextCollapsed);
    setStoredScenarioPanelCollapsed(nextCollapsed);
  }

  function initializeScenarioPanelCollapse() {
    const toggle = document.getElementById('scenario-panel-toggle');
    if (!toggle) return;

    toggle.addEventListener('click', toggleScenarioPanel);

    // Keyboard shortcut: Ctrl+B (or Cmd+B) to toggle. Skip when focus is in
    // an input/textarea to avoid conflicting with browser text editing.
    document.addEventListener('keydown', (event) => {
      if (!(event.ctrlKey || event.metaKey)) return;
      if (event.key !== 'b' && event.key !== 'B') return;
      const active = document.activeElement;
      const tag = active && active.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || (active && active.isContentEditable)) return;
      if (!isDesktopViewport()) return;
      event.preventDefault();
      toggleScenarioPanel();
    });

    syncScenarioPanelWithViewport();
  }

  function syncScenarioPanelWithViewport() {
    if (isDesktopViewport()) {
      applyScenarioPanelCollapsed(getStoredScenarioPanelCollapsed());
    } else {
      // On mobile, always force expanded state; mobile tabs drive visibility.
      applyScenarioPanelCollapsed(false);
    }
  }

  // Initialize panels based on screen size
  function initializePanels() {
    const scenarioPanel = document.getElementById('scenario-panel');
    const chatPanel = document.getElementById('chat-panel');

    if (window.innerWidth <= 768) {
      const showInfo = document.querySelector('.mobile-tab[data-panel="scenario"]').classList.contains('active');
      scenarioPanel.classList.toggle('panel-hidden', !showInfo);
      chatPanel.classList.toggle('panel-hidden', showInfo);
    } else {
      // Desktop: show both panels
      scenarioPanel.classList.remove('panel-hidden');
      chatPanel.classList.remove('panel-hidden');
    }
  }

  // Handle resize events
  window.addEventListener('resize', () => {
    initializePanels();
    syncScenarioPanelWithViewport();
    // Reset tab states on resize
    if (window.innerWidth > 768) {
      document.querySelectorAll('.mobile-tab').forEach(t => {
        t.classList.remove('active');
      });
      document.querySelectorAll('.mobile-tab').forEach(t => {
        const selected = t.dataset.panel === 'chat';
        t.classList.toggle('active', selected);
        t.setAttribute('aria-selected', String(selected));
        t.tabIndex = selected ? 0 : -1;
      });
    }
  });

  // Initialize on load
  initializePanels();
  initializeScenarioPanelCollapse();

  // ========================================
  // Polling Functions
  // ========================================
  function enablePolling() {
    if (authExpiredHandled || chatClosed || messageSending || document.getElementById('teacher-form').dataset.studentStream) return;
    const pollingFlag = document.getElementById('polling-enabled');
    if (pollingFlag && pollingFlag.value === 'true') {
      console.log('📡 Polling already enabled');
      return;
    }

    const messagesContainer = document.getElementById('messages-container');
    if (messagesContainer) {
      messagesContainer.setAttribute('hx-trigger', 'every 2s');
      htmx.process(messagesContainer);
      if (pollingFlag) pollingFlag.value = 'true';
      console.log('📡 Polling enabled - checking for updates every 2s');
    }
  }

  function disablePolling() {
    const messagesContainer = document.getElementById('messages-container');
    const pollingFlag = document.getElementById('polling-enabled');
    if (messagesContainer) {
      messagesContainer.setAttribute('hx-trigger', 'none');
      htmx.process(messagesContainer);
      if (pollingFlag) pollingFlag.value = 'false';
      console.log('🛑 Polling disabled');
    }
  }

  // ========================================
  // UI Lock Function
  // ========================================
  function lockChatUI(message, options = {}) {
    const showLoginButton = options.showLoginButton === true;

    chatClosed = true;
    syncEmptyHint();
    syncComposer();
    document.dispatchEvent(new CustomEvent('chat:locked', {detail:{authExpired:showLoginButton}}));

    const textarea = document.getElementById('teacher-input');
    const submitBtn = document.querySelector('#teacher-form button[type="submit"]');

    if (textarea) textarea.disabled = true;
    if (submitBtn) submitBtn.disabled = true;

    const banner = document.getElementById('session-status');
    const bannerText = document.getElementById('session-status-text');
    const loginBtn = document.getElementById('session-login-btn');
    if (banner) {
      if (bannerText && message) bannerText.textContent = message;
      if (loginBtn) loginBtn.style.display = showLoginButton ? 'inline-flex' : 'none';
      banner.style.display = 'block';
    }

    const endBtn = document.getElementById('end-session-btn');
    const returnBtn = document.getElementById('return-to-scenarios-btn');
    if (endBtn) endBtn.disabled = true;
    if (returnBtn) returnBtn.disabled = true;

    disablePolling();

    console.log('Chat UI locked');
  }

  // ========================================
  // CSRF Token Helper
  // ========================================
  function getCsrfHeaders() {
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
    var headers = { 'Content-Type': 'application/json' };
    if (match) {
      headers['x-csrf-token'] = match[1];
    }
    return headers;
  }

  function getDraftStorageKey() {
    if (!window.currentSessionId) return null;
    return `chat-draft-${window.currentSessionId}`;
  }

  function saveDraftMessage() {
    if (document.getElementById('teacher-form').dataset.studentStream) return;
    const key = getDraftStorageKey();
    const input = document.getElementById('teacher-input');
    if (!key || !input) return;

    const draft = input.value.trim();
    if (!draft) {
      localStorage.removeItem(key);
      return;
    }
    localStorage.setItem(key, draft);
  }

  function restoreDraftMessage() {
    if (document.getElementById('teacher-form').dataset.studentStream) return;
    const key = getDraftStorageKey();
    const input = document.getElementById('teacher-input');
    if (!key || !input) return;

    const saved = localStorage.getItem(key);
    if (!saved) return;

    input.value = saved;
    input.style.height = '';
    input.style.height = input.scrollHeight + 'px';

    if (typeof showToast === 'function') {
      showToast(uiLanguage.t('teacher.draft_restored'), 'info', 3500);
    }
  }

  function clearDraftMessage() {
    const key = getDraftStorageKey();
    if (key) localStorage.removeItem(key);
  }

  function extractRedirectUrl(rawTrigger) {
    if (!rawTrigger) return '/login';
    try {
      const parsed = JSON.parse(rawTrigger);
      if (parsed['auth-expired'] && parsed['auth-expired'].redirect_url) {
        return parsed['auth-expired'].redirect_url;
      }
    } catch (e) {
      // Header may be a plain event name string.
    }
    return '/login';
  }

  function isAuthExpiredResponse(response) {
    // Check for redirect to login (happens for non-HTMX requests)
    if (response.redirected && response.url && response.url.includes('/login')) {
      return true;
    }
    // Check HX-Trigger header for explicit auth-expired signal
    const triggerHeader = response.headers.get('HX-Trigger');
    if (triggerHeader && triggerHeader.includes('auth-expired')) {
      return true;
    }
    // Only treat 401 as auth-expired if response body contains AUTH_EXPIRED code
    // (bare 401 from other sources should not trigger auth-expired)
    return false;
  }

  function handleAuthExpired(source, redirectUrl = '/login') {
    if (authExpiredHandled) return;
    authExpiredHandled = true;
    window.closeAnalysisModal?.();

    console.warn('Session expired detected from:', source);
    restoreInputOnError();
    saveDraftMessage();
    lockChatUI(uiLanguage.t('teacher.auth_expired'), {
      showLoginButton: true
    });

    const loginBtn = document.getElementById('session-login-btn');
    if (loginBtn) {
      loginBtn.dataset.redirectUrl = redirectUrl;
    }

    if (typeof showToast === 'function') {
      showToast(uiLanguage.t('teacher.session_expired'), 'warning', 6000);
    }
  }

  async function fetchWithAuthGuard(url, options) {
    const response = await fetch(url, {
      credentials: 'same-origin',
      ...options,
      headers: uiLanguage.headers(url, options?.headers)
    });

    if (isAuthExpiredResponse(response)) {
      const redirectUrl = extractRedirectUrl(response.headers.get('HX-Trigger'));
      handleAuthExpired(`fetch:${url}`, redirectUrl);
      const authError = new Error('AUTH_EXPIRED');
      authError.code = 'AUTH_EXPIRED';
      throw authError;
    }

    return response;
  }

  async function safeReadJson(response) {
    const raw = await response.text();
    if (!raw) return {};
    try {
      return JSON.parse(raw);
    } catch (e) {
      return { detail: raw };
    }
  }

  // ========================================
  // Session Management
  // ========================================
  async function closeSessionAndGo(url) {
    if (!window.currentSessionId) {
      console.warn('No active session ID');
      window.location.href = url;
      return;
    }

    sessionClosing = true;
    try {
      const response = await fetchWithAuthGuard(`/sessions/${window.currentSessionId}/close`, {
        method: 'POST',
        headers: getCsrfHeaders()
      });

      if (response.ok) {
        const data = await safeReadJson(response);
        console.log('Session closed:', data);
        clearDraftMessage();
        document.dispatchEvent(new Event('chat:ended'));
        lockChatUI(uiLanguage.t('teacher.chat_ended'));

        setTimeout(() => {
          window.location.href = url;
        }, 500);
      } else {
        const error = await safeReadJson(response);
        console.error('Failed to close session:', error);
        alert(uiLanguage.t('teacher.close_failed_navigation'));
        window.location.href = url;
      }
    } catch (error) {
      if (error && error.code === 'AUTH_EXPIRED') {
        return;
      }
      console.error('Network error closing session:', error);
      alert(uiLanguage.t('teacher.close_failed_navigation'));
      window.location.href = url;
    } finally {
      sessionClosing = false;
    }
  }

  // ========================================
  // Modal Functions
  // ========================================
  const analysisOverlay = document.getElementById('analysis-modal-overlay');
  const analysisContainer = document.getElementById('analysis-modal-container');
  const analysisBackground = document.querySelectorAll('body > header, body > footer, .mobile-tabs, .split-layout');
  let modalLoad = 0;

  window.closeAnalysisModal = function closeAnalysisModal() {
    if (analysisOverlay.classList.contains('hidden')) return;
    modalLoad++;
    htmx.trigger(analysisContainer, 'htmx:abort');
    analysisOverlay.classList.add('hidden');
    analysisOverlay.classList.remove('flex');
    analysisContainer.replaceChildren();
    analysisBackground.forEach(el => { el.inert = false; });
    document.getElementById('end-session-btn').focus({preventScroll:true});
  };

  async function loadAnalysisModal() {
    const load = ++modalLoad;
    htmx.trigger(analysisContainer, 'htmx:abort');
    analysisOverlay.classList.remove('hidden');
    analysisOverlay.classList.add('flex');
    analysisBackground.forEach(el => { el.inert = true; });
    analysisContainer.innerHTML = '<div class="modal-header"><h2></h2><button type="button" class="close-modal-btn">×</button></div><div class="modal-body"><p role="status"></p></div><div class="modal-footer"></div>';
    analysisContainer.querySelector('h2').textContent = uiLanguage.t('teacher.analysis_results');
    analysisContainer.querySelector('button').setAttribute('aria-label', uiLanguage.t('teacher.close'));
    analysisContainer.querySelector('[role="status"]').textContent = uiLanguage.t('teacher.analysis_loading');
    analysisContainer.querySelector('button').onclick = window.closeAnalysisModal;
    analysisContainer.querySelector('button').focus();
    let failed = false;
    const checkResponse = event => { failed = event.detail.xhr.status >= 400; };
    analysisContainer.addEventListener('htmx:afterRequest', checkResponse);
    try {
      await htmx.ajax('GET', `/sessions/${window.currentSessionId}/analysis_modal`,
        {source:analysisContainer, target:'#analysis-modal-container', swap:'innerHTML'});
      if (failed) throw new Error('modal_load_failed');
      if (load !== modalLoad) {
        if (analysisOverlay.classList.contains('hidden')) analysisContainer.replaceChildren();
        return;
      }
      analysisContainer.querySelector('.close-modal-btn')?.focus({preventScroll:true});
    } catch {
      if (load !== modalLoad || authExpiredHandled) return;
      const notice = analysisContainer.querySelector('[role="status"]');
      notice.textContent = uiLanguage.t('teacher.analysis_modal_failed');
      const retry = document.createElement('button');
      retry.type = 'button';
      retry.className = 'btn-secondary';
      retry.textContent = uiLanguage.t('teacher.reload_results');
      retry.onclick = loadAnalysisModal;
      analysisContainer.querySelector('.modal-footer').append(retry);
    } finally {
      analysisContainer.removeEventListener('htmx:afterRequest', checkResponse);
    }
  }

  analysisOverlay.addEventListener('click', event => {
    if (event.target === analysisOverlay) window.closeAnalysisModal();
  });
  document.addEventListener('keydown', event => {
    if (analysisOverlay.classList.contains('hidden')) return;
    if (event.key === 'Escape') window.closeAnalysisModal();
    if (event.key !== 'Tab') return;
    const controls = Array.from(analysisContainer.querySelectorAll('button:not(:disabled), a[href], input:not(:disabled), [tabindex="0"]'))
      .filter(el => el.getClientRects().length);
    const first = controls[0], last = controls.at(-1);
    if (!first) { event.preventDefault(); analysisOverlay.focus(); }
    else if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });

  // ========================================
  // Button Event Handlers
  // ========================================
  document.getElementById('end-session-btn').addEventListener('click', async () => {
    if (!window.currentSessionId) {
      alert(uiLanguage.t('teacher.no_active_session'));
      return;
    }

    const btn = document.getElementById('end-session-btn');
    const state = btn.dataset.state;

    // Guard against double submission
    if (authExpiredHandled || messageSending || state === 'ending' || state === 'analyzing') {
      return;
    }

    // Already analyzed — re-open modal
    if (state === 'done') {
      void loadAnalysisModal();
      return;
    }

    const skipEnd = (state === 'ready-to-analyze');

    // Active session — confirm end
    if (!skipEnd) {
      if (!confirm(uiLanguage.t('teacher.confirm_end'))) {
        return;
      }
    }

    btn.dataset.analysisRequestId ||= crypto.randomUUID();
    btn.dataset.analysisRequestUrl ||= `/sessions/${window.currentSessionId}/${skipEnd ? 'analyze' : 'end'}`;
    analysisUnresolved = true;
    setAnalysisState(skipEnd ? 'analyzing' : 'ending');
    lockChatUI(uiLanguage.t('teacher.analysis_preparing'));

    try {
      const analyzeResponse = await fetchWithAuthGuard(btn.dataset.analysisRequestUrl, {
        method: 'POST',
        headers: {...getCsrfHeaders(), 'Content-Type': 'application/json'},
        body: JSON.stringify({request_id: btn.dataset.analysisRequestId})
      });

      const analysisResult = await safeReadJson(analyzeResponse);
      analysisUnresolved = analyzeResponse.status >= 500 || analyzeResponse.status === 202 ||
        analysisResult.latest_run?.status === 'running' || analysisResult.detail?.code === 'analysis_busy';
      if (analyzeResponse.ok) document.dispatchEvent(new Event('chat:ended'));
      if (analyzeResponse.ok || analysisResult.detail?.code === 'request_conflict') {
        delete btn.dataset.analysisRequestId;
        delete btn.dataset.analysisRequestUrl;
      }
      const activeRun = analyzeResponse.status === 409 && analysisResult.detail?.code === 'analysis_busy';
      if ((analyzeResponse.ok && (analyzeResponse.status === 202 || !analysisResult.retryable)) || (analyzeResponse.status === 422 && analysisResult.plan) || activeRun) {
        clearDraftMessage();
        const bannerText = document.getElementById('session-status-text');
        if (bannerText) bannerText.textContent = uiLanguage.t('teacher.chat_ended');
        setAnalysisState('done');
        void loadAnalysisModal();
      } else {
        alert(uiLanguage.t('teacher.analysis_failed_alert'));
        setAnalysisState('ready-to-analyze');
      }
    } catch (error) {
      if (error && error.code === 'AUTH_EXPIRED') {
        return;
      }
      console.error('Failed to analyze session:', error);
      alert(uiLanguage.t('teacher.analysis_failed_alert'));
      setAnalysisState('ready-to-analyze');
    }
  });

  document.getElementById('return-to-scenarios-btn').addEventListener('click', async () => {
    if (!confirm(uiLanguage.t('teacher.confirm_scenarios'))) {
      return;
    }

    await closeSessionAndGo('/scenarios');
  });

  // ========================================
  // Message Tracking
  // ========================================
  function getLastMessageId() {
    const messages = document.querySelectorAll('.message[data-message-id]');

    if (messages.length === 0) {
      console.debug('No messages found in DOM');
      return 0;
    }

    const ids = Array.from(messages)
      .map(m => {
        const id = parseInt(m.dataset.messageId);
        if (isNaN(id)) {
          console.error('Invalid message ID found:', m.dataset.messageId, m);
          return null;
        }
        return id;
      })
      .filter(id => id !== null);

    if (ids.length === 0) {
      console.error('No valid message IDs found! All IDs are NaN or null');
      return 0;
    }

    const maxId = Math.max(...ids);
    console.debug(`Found ${ids.length} valid message IDs. Max: ${maxId}`);
    return maxId;
  }

  // ========================================
  // Immediate Message Display & Typing Indicator
  // ========================================
  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  function removeTempElements() {
    document.querySelectorAll('.temp-message').forEach(
      el => el.remove()
    );
    const typing = document.getElementById('typing-indicator');
    if (typing) typing.remove();
  }

  function restoreInputOnError() {
    removeTempElements();
    const input = document.getElementById('teacher-input');
    if (input && lastSentContent) {
      input.value = lastSentContent;
      input.style.height = '';
      input.style.height = input.scrollHeight + 'px';
      lastSentContent = '';
    }
  }

  // Show teacher message immediately when form is submitted
  htmx.on('htmx:beforeRequest', (event) => {
    if (event.detail.elt.id !== 'teacher-form') return;

    const input = document.getElementById('teacher-input');
    const content = input.value.trim();
    if (!content || authExpiredHandled || chatClosed || messageSending) {
      event.preventDefault();
      return;
    }
    messageSending = true;
    syncComposer();

    lastSentContent = content;
    const container = document.getElementById(
      'messages-container'
    );

    // Create temporary teacher message
    const now = new Date();
    const timeStr =
      now.getHours().toString().padStart(2, '0') + ':' +
      now.getMinutes().toString().padStart(2, '0');

    const tempMsg = document.createElement('div');
    tempMsg.className = 'message message-teacher temp-message';
    tempMsg.innerHTML = `
      <div class="avatar teacher">T</div>
      <div class="message-content-wrapper">
        <div class="message-bubble">
          ${escapeHtml(content)}
        </div>
        <div class="message-meta">
          <span class="timestamp">${timeStr}</span>
        </div>
      </div>
    `;
    container.appendChild(tempMsg);

    // Create typing indicator
    const typingEl = document.createElement('div');
    typingEl.className = 'message message-student';
    typingEl.id = 'typing-indicator';
    typingEl.innerHTML = `
      <div class="avatar student">
        ${escapeHtml(window.studentDisplayName)}
      </div>
      <div class="message-content-wrapper">
        <div class="message-bubble typing-bubble">
          <div class="typing-dots">
            <span></span><span></span><span></span>
          </div>
          <span class="typing-label"></span>
        </div>
      </div>
    `;
    typingEl.querySelector('.typing-label').textContent = uiLanguage.t('teacher.generating');
    container.appendChild(typingEl);

    // Pause polling during POST to avoid race conditions
    disablePolling();
    console.log('Polling paused during message send');

    // Clear input immediately
    input.value = '';
    input.style.height = '';
    clearDraftMessage();

    // Scroll to bottom
    container.scrollTop = container.scrollHeight;
  });

  // POST and polling can arrive in either order. Keep each server ID once.
  htmx.on('htmx:beforeSwap', (event) => {
    if (event.detail.target.id !== 'messages-container' || !event.detail.shouldSwap) return;
    removeTempElements();
    const doc = new DOMParser().parseFromString(event.detail.serverResponse, 'text/html');
    const present = new Set(Array.from(document.querySelectorAll('#messages-container [data-message-id]'), el => el.dataset.messageId));
    doc.querySelectorAll('[data-message-id]').forEach(el => {
      if (present.has(el.dataset.messageId)) el.remove();
      else present.add(el.dataset.messageId);
    });
    event.detail.serverResponse = doc.body.innerHTML;
  });

  // ========================================
  // HTMX Event Handlers
  // ========================================
  htmx.on('htmx:afterSwap', (event) => {
    if (event.detail.target.id === 'messages-container') {
      pollingAuthFailCount = 0;
      console.log('New messages added via HTMX swap');

      const messages = document.querySelectorAll('.message[data-message-id]');
      console.debug(`Total messages in DOM: ${messages.length}`);

      messages.forEach((msg, idx) => {
        const msgId = msg.dataset.messageId;
        const role = msg.querySelector('.role-label')?.textContent || 'unknown';
        const content = msg.querySelector('.message-content')?.textContent?.substring(0, 50) || '';
        console.debug(`  Message ${idx}: ID=${msgId}, role=${role}, content="${content}..."`);
      });

      const container = document.getElementById('messages-container');
      Array.from(container.querySelectorAll('.message[data-message-id]'))
        .sort((a, b) => Number(a.dataset.messageId) - Number(b.dataset.messageId))
        .forEach(message => container.appendChild(message));
      const lastId = getLastMessageId();

      if (lastId > 0) {
        document.getElementById('last-message-id').value = lastId;
        console.log(`Updated last_message_id to: ${lastId}`);

        const requestPath = event.detail.pathInfo?.requestPath || '';
        if (!requestPath.includes('/messages/updates')) {
          enablePolling();
        }
      } else {
        console.warn('No valid message IDs found after swap!');
      }

      if (container) {
        container.scrollTop = container.scrollHeight;
        console.debug('Scrolled to bottom');
      } else {
        console.error('messages-container element not found! Cannot scroll');
      }
    }
  });

  document.body.addEventListener('htmx:afterRequest', (event) => {
    // Clear input after successful message send
    if (!authExpiredHandled && event.detail.successful && event.detail.elt.id === 'teacher-form') {
      document.getElementById('teacher-input').value = '';
      document.getElementById('teacher-input').style.height = '';
      clearDraftMessage();
      lastSentContent = '';
      finishSending();
    }

    if (event.detail.elt.id === 'teacher-form') {
      finishSending();
    }

    // Reset polling auth fail counter on any successful polling response
    const path = event.detail.pathInfo?.requestPath || '';
    if (path.includes('/messages/updates') && event.detail.successful) {
      pollingAuthFailCount = 0;
    }
  });

  // Enter key to send message (Shift+Enter for new line)
  document.getElementById('teacher-input').addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      const form = document.getElementById('teacher-form');
      const input = document.getElementById('teacher-input');
      if (input.value.trim() && !messageSending && !chatClosed && !authExpiredHandled) {
        htmx.trigger(form, 'submit');
      }
    }
  });

  document.body.addEventListener('htmx:responseError', (event) => {
    const xhr = event.detail.xhr;
    const path = event.detail.pathInfo.requestPath;
    const triggerHeader = xhr.getResponseHeader('HX-Trigger');
    const isPolling = path.includes('/messages/updates');
    const isAuthExpired = triggerHeader && triggerHeader.includes('auth-expired');

    if (isAuthExpired) {
      if (isPolling) {
        pollingAuthFailCount++;
        if (pollingAuthFailCount < MAX_POLLING_AUTH_FAILS) {
          console.warn(
            `Auth fail on polling (${pollingAuthFailCount}/${MAX_POLLING_AUTH_FAILS}), retrying...`
          );
          return;
        }
      }
      handleAuthExpired(`htmx:${path}`, extractRedirectUrl(triggerHeader));
      return;
    }

    if (path.endsWith('/analysis_modal')) return;

    // Non-auth errors: reset polling fail counter
    if (isPolling) {
      pollingAuthFailCount = 0;
    }

    // Clean up temp elements and restore input on error
    if (!isPolling) {
      restoreInputOnError();
      finishSending();
    }

    if (event.detail.elt.id === 'teacher-form') {
      console.error('Message send failed:', {
        status: xhr.status,
        statusText: xhr.statusText,
        responseText: xhr.responseText,
        path: path,
        fullEvent: event.detail
      });

      let errorMsg = uiLanguage.t('teacher.message_failed');
      try {
        const errorData = JSON.parse(xhr.responseText);
        if (errorData.detail) {
          if (errorData.detail?.code === 'session_ended' || errorData.code === 'session_ended') {
            lockChatUI(uiLanguage.t('teacher.already_ended'));
            setAnalysisState('ready-to-analyze');
            errorMsg = uiLanguage.t('teacher.start_new_session');
          }
        }
      } catch (e) {
        // Response is not JSON
      }

      alert(errorMsg);
    } else {
      if (!isPolling) {
        console.error('HTMX error:', event.detail);
        alert(uiLanguage.t('teacher.request_failed'));
      } else {
        console.warn('Polling error (may be temporary):', xhr.status);
      }
    }
  });

  document.body.addEventListener('htmx:sendError', (event) => {
    // Clean up temp elements on network error
    if (event.detail.elt.id === 'teacher-form') {
      restoreInputOnError();
      finishSending();
    }

    if (event.detail.elt.id === 'teacher-form') {
      console.error('Network error:', {
        error: event.detail.error,
        path: event.detail.pathInfo.requestPath,
        fullEvent: event.detail
      });
      alert(uiLanguage.t('teacher.network_error'));
    } else {
      console.warn('Network error on polling (may be temporary)');
    }
  });

  document.body.addEventListener('htmx:timeout', (event) => {
    console.warn('HTMX timeout:', event.detail);

    if (!event.detail.pathInfo.requestPath.includes(
      '/messages/updates'
    )) {
      restoreInputOnError();
      finishSending();
      alert(uiLanguage.t('teacher.request_timeout'));
    }
  });

  document.body.addEventListener('auth-expired', (event) => {
    // If this event came from the polling element, let
    // htmx:responseError handle it (with retry logic).
    // HTMX fires HX-Trigger events BEFORE htmx:responseError,
    // so without this guard the retry counter is bypassed.
    const target = event.target;
    if (target && target.id === 'messages-container') {
      return;
    }
    const detail = event.detail || {};
    const redirectUrl = detail.redirect_url || '/login';
    handleAuthExpired('hx-trigger-event', redirectUrl);
  });

  const sessionLoginBtn = document.getElementById('session-login-btn');
  if (sessionLoginBtn) {
    sessionLoginBtn.addEventListener('click', () => {
      const redirectUrl = sessionLoginBtn.dataset.redirectUrl || '/login';
      window.location.href = redirectUrl;
    });
  }

  window.addEventListener('beforeunload', () => {
    const textarea = document.getElementById('teacher-input');
    if (textarea && textarea.value.trim()) {
      saveDraftMessage();
    }
  });

  // ========================================
  // Initialization
  // ========================================
  function initializeChat() {
    restoreDraftMessage();
    const lastId = getLastMessageId();
    document.getElementById('last-message-id').value = lastId;
    if (chatClosed) {
      lockChatUI(uiLanguage.t('teacher.chat_ended'));
      setAnalysisState('ready-to-analyze');
    } else {
      setAnalysisState('active');
      if (lastId > 0) enablePolling();
    }
  }
  // Share existing auth and session controls with the student stream.
  window.chatUI = {
    sessionId: chatConfig.sessionId,
    studentName: chatConfig.studentName,
    mentorEnabled: chatConfig.mentorEnabled,
    mentorMode: chatConfig.mentorMode,
    mentorName: chatConfig.mentorName,
    fetch: fetchWithAuthGuard,
    headers: getCsrfHeaders,
    stopPolling: disablePolling,
    isLocked: () => authExpiredHandled || chatClosed,
    expire: () => handleAuthExpired('student-stream'),
    end: () => {
      lockChatUI(uiLanguage.t('teacher.chat_ended'));
      setAnalysisState('ready-to-analyze');
      document.dispatchEvent(new Event('chat:ended'));
    }
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeChat, { once: true });
  } else {
    initializeChat();
  }
})();
