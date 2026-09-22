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
  let pollingAuthFailCount = 0;
  const MAX_POLLING_AUTH_FAILS = 3;

  function syncComposer() {
    const disabled = authExpiredHandled || chatClosed || messageSending;
    document.getElementById('teacher-input').disabled = disabled;
    document.querySelector('#teacher-form button[type="submit"]').disabled = disabled;
    const endBtn = document.getElementById('end-session-btn');
    endBtn.disabled = authExpiredHandled || messageSending || ['ending', 'analyzing'].includes(endBtn.dataset.state);
  }

  function setAnalysisState(state) {
    const btn = document.getElementById('end-session-btn');
    const busy = ['ending', 'analyzing'].includes(state);
    btn.dataset.state = state;
    btn.textContent = {
      active: '대화 종료 후 분석 보기', ending: '분석 준비 중...',
      analyzing: '분석 중...', 'ready-to-analyze': '분석 보기', done: '분석 다시 보기'
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
  // YouTube URL Conversion
  // ========================================
  function convertToEmbedUrl(url) {
    if (!url) return '';

    // YouTube live URL
    let match = url.match(/youtube\.com\/live\/([^?&]+)/);
    if (match) return `https://www.youtube.com/embed/${match[1]}`;

    // YouTube watch URL
    match = url.match(/youtube\.com\/watch\?v=([^&]+)/);
    if (match) return `https://www.youtube.com/embed/${match[1]}`;

    // YouTube short URL
    match = url.match(/youtu\.be\/([^?&]+)/);
    if (match) return `https://www.youtube.com/embed/${match[1]}`;

    // Already embed URL or other format
    return url;
  }

  // Initialize video player if video URL exists
  if (chatConfig.videoUrl) {
  const videoIframe = document.getElementById('video-iframe');
  if (videoIframe) {
    const originalUrl = chatConfig.videoUrl;
    const embedUrl = convertToEmbedUrl(originalUrl);
    videoIframe.src = embedUrl;
    console.log('Video player initialized:', embedUrl);
  }
  }

  // ========================================
  // Mobile Tab Switching
  // ========================================
  document.querySelectorAll('.mobile-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      // Update active tab
      document.querySelectorAll('.mobile-tab').forEach(t => {
        t.classList.remove('active');
      });
      tab.classList.add('active');

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
  document.querySelectorAll('.info-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      // Update active tab
      document.querySelectorAll('.info-tab').forEach(t => {
        t.classList.remove('active');
      });
      tab.classList.add('active');

      // Show/hide tab panes
      const tabId = tab.dataset.tab;
      document.querySelectorAll('.tab-pane').forEach(pane => {
        pane.classList.remove('active');
      });

      const targetPane = document.getElementById(`tab-${tabId}`);
      if (targetPane) {
        targetPane.classList.add('active');
      }
    });
  });

  // ========================================
  // Scenario Panel Collapse (Desktop Only)
  // ========================================
  const SCENARIO_PANEL_COLLAPSE_KEY = 'chat.scenarioPanel.collapsed';

  function isDesktopViewport() {
    return window.innerWidth > 768;
  }

  function pauseScenarioVideo() {
    const iframe = document.getElementById('video-iframe');
    if (!iframe) return;
    const currentSrc = iframe.getAttribute('src');
    if (currentSrc) {
      iframe.dataset.savedSrc = currentSrc;
      iframe.setAttribute('src', '');
    }
  }

  function resumeScenarioVideo() {
    const iframe = document.getElementById('video-iframe');
    if (!iframe) return;
    const savedSrc = iframe.dataset.savedSrc;
    if (savedSrc && !iframe.getAttribute('src')) {
      iframe.setAttribute('src', savedSrc);
      delete iframe.dataset.savedSrc;
    }
  }

  function applyScenarioPanelCollapsed(collapsed) {
    const panel = document.getElementById('scenario-panel');
    const toggle = document.getElementById('scenario-panel-toggle');
    if (!panel || !toggle) return;

    if (collapsed) {
      panel.setAttribute('data-collapsed', 'true');
      toggle.setAttribute('aria-expanded', 'false');
      toggle.setAttribute('aria-label', '정보 패널 펼치기');
      pauseScenarioVideo();
    } else {
      panel.removeAttribute('data-collapsed');
      toggle.setAttribute('aria-expanded', 'true');
      toggle.setAttribute('aria-label', '정보 패널 접기');
      resumeScenarioVideo();
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
      // Mobile: show chat by default, hide scenario
      scenarioPanel.classList.add('panel-hidden');
      chatPanel.classList.remove('panel-hidden');
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
      document.querySelector('[data-panel="chat"]').classList.add('active');
    }
  });

  // Initialize on load
  initializePanels();
  initializeScenarioPanelCollapse();

  // ========================================
  // Polling Functions
  // ========================================
  function enablePolling() {
    if (authExpiredHandled || chatClosed || messageSending) return;
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
    syncComposer();

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
    const key = getDraftStorageKey();
    const input = document.getElementById('teacher-input');
    if (!key || !input) return;

    const saved = localStorage.getItem(key);
    if (!saved) return;

    input.value = saved;
    input.style.height = '';
    input.style.height = input.scrollHeight + 'px';

    if (typeof showToast === 'function') {
      showToast('이전 입력 메시지를 복원했습니다.', 'info', 3500);
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

    console.warn('Session expired detected from:', source);
    restoreInputOnError();
    saveDraftMessage();
    lockChatUI('로그인이 만료되었습니다. 다시 로그인해주세요.', {
      showLoginButton: true
    });

    const loginBtn = document.getElementById('session-login-btn');
    if (loginBtn) {
      loginBtn.dataset.redirectUrl = redirectUrl;
    }

    if (typeof showToast === 'function') {
      showToast('세션이 만료되었습니다. 다시 로그인해 주세요.', 'warning', 6000);
    }
  }

  async function fetchWithAuthGuard(url, options) {
    const response = await fetch(url, {
      credentials: 'same-origin',
      ...options
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

    try {
      const response = await fetchWithAuthGuard(`/sessions/${window.currentSessionId}/close`, {
        method: 'POST',
        headers: getCsrfHeaders()
      });

      if (response.ok) {
        const data = await safeReadJson(response);
        console.log('Session closed:', data);
        clearDraftMessage();
        lockChatUI('이 대화는 종료되었습니다.');

        setTimeout(() => {
          window.location.href = url;
        }, 500);
      } else {
        const error = await safeReadJson(response);
        console.error('Failed to close session:', error);
        alert(`세션 종료 실패: ${error.detail || error.feedback || '알 수 없는 오류'}. 계속 이동하시겠습니까?`);
        window.location.href = url;
      }
    } catch (error) {
      if (error && error.code === 'AUTH_EXPIRED') {
        return;
      }
      console.error('Network error closing session:', error);
      alert('세션 종료 요청에 실패했습니다. 계속 이동하시겠습니까?');
      window.location.href = url;
    }
  }

  // ========================================
  // Modal Functions
  // ========================================
  window.closeAnalysisModal = function closeAnalysisModal() {
    const overlay = document.getElementById('analysis-modal-overlay');
    overlay.classList.add('hidden');
    overlay.classList.remove('flex');
    document.getElementById('analysis-modal-container').innerHTML = '';
  }

  document.getElementById('analysis-modal-overlay').addEventListener('click', function(e) {
    if (e.target.id === 'analysis-modal-overlay') {
      window.closeAnalysisModal();
    }
  });

  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      window.closeAnalysisModal();
    }
  });

  // ========================================
  // Button Event Handlers
  // ========================================
  document.getElementById('end-session-btn').addEventListener('click', async () => {
    if (!window.currentSessionId) {
      alert('활성 세션이 없습니다');
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
      const overlay = document.getElementById('analysis-modal-overlay');
      htmx.ajax('GET', `/sessions/${window.currentSessionId}/analysis_modal`,
        { target: '#analysis-modal-container', swap: 'innerHTML' })
        .then(() => {
          overlay.classList.remove('hidden');
          overlay.classList.add('flex');
        });
      return;
    }

    const skipEnd = (state === 'ready-to-analyze');

    // Active session — confirm end
    if (!skipEnd) {
      if (!confirm('이 세션을 종료하시겠습니까?')) {
        return;
      }
    }

    // Phase 1: End session (skip if already ended)
    if (!skipEnd) {
      setAnalysisState('ending');
      lockChatUI('세션 종료 중...');

      try {
        const endResponse = await fetchWithAuthGuard(`/sessions/${window.currentSessionId}/end`, {
          method: 'POST',
          headers: getCsrfHeaders()
        });

        if (!endResponse.ok) {
          const error = await safeReadJson(endResponse);
          alert(`세션 종료 실패: ${error.detail || error.feedback || '알 수 없는 오류'}`);
          setAnalysisState('active');
          window.location.reload();
          return;
        }

        clearDraftMessage();
        const bannerText = document.getElementById('session-status-text');
        if (bannerText) {
          bannerText.textContent = '이 대화는 종료되었습니다.';
        }

        const returnBtn = document.getElementById('return-to-scenarios-btn');
        if (returnBtn) returnBtn.disabled = true;

      } catch (error) {
        if (error && error.code === 'AUTH_EXPIRED') {
          return;
        }
        console.error('Failed to end session:', error);
        alert('세션 종료에 실패했습니다');
        setAnalysisState('active');
        window.location.reload();
        return;
      }
    }

    // Phase 2: Analyze
    setAnalysisState('analyzing');

    try {
      const analyzeResponse = await fetchWithAuthGuard(`/sessions/${window.currentSessionId}/analyze`, {
        method: 'POST',
        headers: getCsrfHeaders()
      });

      const analysisResult = await safeReadJson(analyzeResponse);
      if (analyzeResponse.ok && !analysisResult.retryable) {
        const overlay = document.getElementById('analysis-modal-overlay');
        htmx.ajax('GET', `/sessions/${window.currentSessionId}/analysis_modal`,
          { target: '#analysis-modal-container', swap: 'innerHTML' })
          .then(() => {
            overlay.classList.remove('hidden');
            overlay.classList.add('flex');

            setAnalysisState('done');
          })
          .catch((err) => {
            console.error('Analysis modal load failed', err);
            if (authExpiredHandled) {
              return;
            }
            alert('분석 결과를 불러오지 못했습니다. 새로고침 후 다시 시도해주세요.');
            setAnalysisState('ready-to-analyze');
          });
      } else {
        const error = analysisResult;
        alert(`분석 실패: ${error.detail || error.feedback || '알 수 없는 오류'}`);
        setAnalysisState('ready-to-analyze');
      }
    } catch (error) {
      if (error && error.code === 'AUTH_EXPIRED') {
        return;
      }
      console.error('Failed to analyze session:', error);
      alert('분석에 실패했습니다');
      setAnalysisState('ready-to-analyze');
    }
  });

  document.getElementById('return-to-scenarios-btn').addEventListener('click', async () => {
    if (!confirm('시나리오 목록으로 돌아가시겠습니까?')) {
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
          응답 생성 중...
        </div>
      </div>
    `;
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

      let errorMsg = 'Failed to send message. Please try again.';
      try {
        const errorData = JSON.parse(xhr.responseText);
        if (errorData.detail) {
          errorMsg = `Error: ${errorData.detail}`;

          if (errorData.detail.includes('already ended') || errorData.detail.includes('종료')) {
            lockChatUI('이 대화는 이미 종료되었습니다.');
            setAnalysisState('ready-to-analyze');
            errorMsg = '이 세션은 이미 종료되었습니다. 새 세션을 시작해주세요.';
          }
        }
      } catch (e) {
        // Response is not JSON
      }

      alert(errorMsg);
    } else {
      if (!isPolling) {
        console.error('HTMX error:', event.detail);
        alert('요청 처리 중 오류가 발생했습니다');
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
      alert('Network error. Please check your connection.');
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
      alert('요청 시간이 초과되었습니다. 다시 시도해주세요.');
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
      lockChatUI('이 대화는 종료되었습니다.');
      setAnalysisState('ready-to-analyze');
    } else {
      setAnalysisState('active');
      if (lastId > 0) enablePolling();
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeChat, { once: true });
  } else {
    initializeChat();
  }
})();
