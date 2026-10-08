// Run with a Playwright Page against the isolated tests/browser_server.py.
export default async function checkStudentStream(page) {
  const base = page.url().split('/').slice(0, 3).join('/');
  const assert = (value, message) => { if (!value) throw new Error(message); };
  const errors = [];
  let legacyPosts = 0;
  let pollRequests = 0;
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (request.url().includes('/messages/updates')) pollRequests++;
  });
  await page.route('**/sessions/*/messages', route => {
    legacyPosts++;
    return route.fulfill({status:500});
  });
  await page.context().addCookies([{name:'csrftoken', value:'browser-test-token', url:base}]);
  await page.addInitScript(() => {
    const originalFetch = window.fetch;
    window.streamFixture = {posts:[], gets:[], controllers:[]};
    window.fetch = async (url, options = {}) => {
      const fixture = window.streamFixture;
      if (url.endsWith('/turns/stream')) {
        fixture.posts.push({body:JSON.parse(options.body), headers:options.headers});
        if (fixture.mode === 'auth') {
          return new Response('{"code":"AUTH_EXPIRED"}', {status:401, headers:{'Content-Type':'application/json'}});
        }
        if (fixture.mode === 'network') {
          fixture.snapshot.request_id = JSON.parse(options.body).request_id;
          throw new TypeError('Fixture connection lost');
        }
        if (fixture.mode === 'json') {
          fixture.snapshot.request_id = JSON.parse(options.body).request_id;
          return new Response(JSON.stringify(fixture.snapshot), {headers:{'Content-Type':'application/json'}});
        }
        return new Response(new ReadableStream({start(controller) {
          fixture.controllers.push(controller);
        }}), {headers:{'Content-Type':'text/event-stream'}});
      }
      if (url.startsWith('/runs/') || url.includes('/runs?')) {
        fixture.gets.push(url);
        return new Response(JSON.stringify(fixture.snapshot), {
          status:fixture.lookupStatus || (fixture.snapshot ? 200 : 404), headers:{'Content-Type':'application/json'}
        });
      }
      return originalFetch(url, options);
    };
    window.emitStudentEvent = (event, data) => {
      const multiline = JSON.stringify(data).replace(',', ',\r\ndata: ');
      const bytes = new TextEncoder().encode(`: ping\r\n\r\nevent: ${event}\r\ndata: ${multiline}\r\n\r\n`);
      for (const byte of bytes) window.streamFixture.controllers.at(-1).enqueue(Uint8Array.of(byte));
    };
  });
  await page.setViewportSize({width:1280, height:900});
  await page.goto(`${base}/chat`);
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream === 'true', null, {timeout:3000});
  await page.evaluate(async () => {
    const {mountStudentStream} = await import('/static/js/student-stream.js');
    mountStudentStream(window.chatUI);
  });
  const input = page.locator('#teacher-input');
  await input.fill('왜 그런가요?');
  await input.press('Shift+Enter');
  assert((await input.inputValue()).endsWith('\n'), 'Shift+Enter keeps newline');
  await input.fill('왜 그런가요?');
  await input.press('Enter');
  await page.waitForFunction(() => window.streamFixture.posts.length === 1);
  assert(await input.isDisabled(), 'sending locks the composer');
  assert(await page.locator('[data-request-id].message-teacher').count() === 1, 'one optimistic teacher');
  assert(await page.locator('.student-run-status').innerText() === '전송 중', 'sending differs from generation');
  await page.evaluate(() => emitStudentEvent('run.accepted', {
    run_id:'run-1', turn_id:'turn-1', seq:0, request_id:streamFixture.posts[0].body.request_id,
    teacher_message_id:11, turn_index:1, operation:'student', status:'running'
  }));
  await page.locator('[data-message-id="11"]').waitFor();
  await page.evaluate(() => emitStudentEvent('output.delta', {
    run_id:'run-1', turn_id:'turn-1', seq:1, text:'학생🙂<img src=x onerror="window.xss=1">'
  }));
  await page.waitForFunction(() => document.querySelector('.message-student .message-bubble')?.textContent.includes('학생🙂'));
  assert(await input.isDisabled(), 'delta is provisional');
  assert(await page.locator('.message-student img').count() === 0, 'delta renders as safe text');
  await page.evaluate(() => {
    emitStudentEvent('output.completed', {
      run_id:'run-1', turn_id:'turn-1', seq:2, turn_index:1, status:'completed',
      message:{id:12, role:'student', content:'저장된 최종 답변', created_at:'2026-10-08T00:00:00Z'}
    });
    streamFixture.controllers.at(-1).close();
  });
  await page.waitForFunction(() => !document.querySelector('#teacher-input').disabled);
  assert(await page.locator('[data-message-id="12"] .message-bubble').innerText() === '저장된 최종 답변', 'completed replaces the whole body');
  assert(await input.evaluate(el => el === document.activeElement), 'completion restores input focus');
  assert(await page.locator('.message-teacher').count() === 1 && await page.locator('.message-student').count() === 1, 'no duplicate messages');
  assert(await page.locator('#polling-enabled').inputValue() === 'false', 'stream does not enable polling');
  assert(pollRequests === 0, 'normal completed student sends zero message polls');
  assert(await page.evaluate(() => streamFixture.gets.length === 0), 'normal completion sends zero recovery polls');
  assert(await page.evaluate(() => streamFixture.posts[0].headers['x-csrf-token']) === 'browser-test-token', 'fetch retains CSRF');

  await input.fill('실패할 질문');
  await input.press('Enter');
  await page.waitForFunction(() => streamFixture.posts.length === 2);
  await page.evaluate(() => {
    emitStudentEvent('run.accepted', {
      run_id:'run-2', turn_id:'turn-2', seq:0, request_id:streamFixture.posts[1].body.request_id,
      teacher_message_id:21, turn_index:2, operation:'student', status:'running'
    });
    emitStudentEvent('output.delta', {run_id:'run-2', turn_id:'turn-2', seq:1, text:'부분 답변'});
    document.querySelector('#teacher-input').value = '다음 초안';
    emitStudentEvent('run.failed', {
      run_id:'run-2', turn_id:'turn-2', seq:2, status:'failed', code:'provider_error',
      message:'학생 응답을 받지 못했습니다.', retryable:true
    });
    streamFixture.controllers.at(-1).close();
  });
  const retry = page.getByRole('button', {name:'학생 응답 다시 받기', exact:true});
  await retry.waitFor({state:'visible', timeout:3000});
  assert(await input.isDisabled(), 'unresolved turn blocks a new question');
  assert(await input.inputValue() === '다음 초안', 'failure preserves the next draft');
  assert(!(await page.locator('#end-session-btn').isDisabled()), 'failure still allows end');
  assert((await page.locator('.student-run-status').last().innerText()).includes('응답 미완료·대화 기록에 미포함'), 'partial output clearly excluded');
  await retry.click();
  await page.waitForFunction(() => streamFixture.posts.length === 3);
  assert(await page.evaluate(() => {
    const [first, retry] = streamFixture.posts.slice(1).map(item => item.body);
    return retry.request_id !== first.request_id && retry.turn_id === 'turn-2' && retry.content === '실패할 질문';
  }), 'explicit retry has a new request ID, same turn and exact saved content');
  assert(await page.locator('[data-message-id="21"]').count() === 1, 'retry preserves one teacher row');
  assert(await page.locator('.message-student').last().locator('.message-bubble').innerText() === '', 'retry clears provisional text');
  await page.evaluate(() => {
    emitStudentEvent('run.accepted', {
      run_id:'run-3', turn_id:'turn-2', seq:0, request_id:streamFixture.posts[2].body.request_id,
      teacher_message_id:21, turn_index:2, operation:'student', status:'running'
    });
    emitStudentEvent('output.completed', {
      run_id:'run-3', turn_id:'turn-2', seq:1, turn_index:2, status:'completed',
      message:{id:22, role:'student', content:'재시도 성공', created_at:'2026-10-08T00:00:00Z'}
    });
    streamFixture.controllers.at(-1).close();
  });
  await page.waitForFunction(() => !document.querySelector('#teacher-input').disabled);
  assert(await input.inputValue() === '다음 초안', 'successful retry keeps next draft');
  assert(await page.locator('[data-message-id="21"]').count() === 1, 'accepted retry is still one teacher row');

  await page.setViewportSize({width:390, height:844});
  await page.locator('.mobile-tab[data-panel="scenario"]').click();
  assert(await page.locator('#scenario-panel').isVisible(), 'mobile scenario panel');
  await page.locator('.mobile-tab[data-panel="chat"]').click();
  assert(await input.isVisible(), 'mobile streaming composer');
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= 390), 'mobile stream has no horizontal overflow');

  await input.fill('완료 프레임을 놓친 질문');
  await input.press('Enter');
  await page.waitForFunction(() => streamFixture.posts.length === 4);
  await page.evaluate(() => {
    streamFixture.snapshot = {
      run_id:'run-4', turn_id:'turn-3', turn_index:3, operation:'student', session_id:1,
      request_id:streamFixture.posts[3].body.request_id, teacher_message_id:31,
      status:'completed', result_kind:'message', partial_text:null, error_code:null, retryable:false,
      message:{id:32, role:'student', content:'조회로 복원한 최종 본문', created_at:'2026-10-08T00:00:00Z'}
    };
    streamFixture.controllers.at(-1).close();
  });
  await page.waitForFunction(() => !document.querySelector('#teacher-input').disabled, null, {timeout:3000});
  assert(await page.locator('[data-message-id="31"]').count() === 1, 'status lookup reconciles a missed acceptance');
  assert(await page.locator('[data-message-id="32"] .message-bubble').innerText() === '조회로 복원한 최종 본문', 'EOF restores a persisted completion');
  assert(await input.evaluate(el => el === document.activeElement), 'mobile recovery restores input focus');
  assert(await page.evaluate(() => streamFixture.gets.length === 1 && streamFixture.gets[0].includes('/runs?request_id=') && streamFixture.posts.length === 4), 'EOF queries by request ID without regeneration');

  await page.evaluate(() => {
    streamFixture.mode = 'json';
    streamFixture.snapshot = {
      run_id:'run-5', turn_id:'turn-4', turn_index:4, operation:'student', session_id:1,
      teacher_message_id:41, status:'completed', result_kind:'message',
      partial_text:null, error_code:null, retryable:false,
      message:{id:42, role:'student', content:'기존 실행의 저장된 답변', created_at:'2026-10-08T00:00:00Z'}
    };
  });
  await input.fill('동일 POST의 기존 실행');
  await input.press('Enter');
  await page.waitForFunction(() => document.querySelector('[data-message-id="42"]'), null, {timeout:3000});
  assert(await page.evaluate(() => streamFixture.gets.length === 1), 'JSON replay needs no extra lookup');
  assert(await page.locator('[data-message-id="41"]').count() === 1, 'JSON replay reconciles teacher ID');
  assert(await page.locator('[data-message-id="42"] .message-bubble').innerText() === '기존 실행의 저장된 답변', 'JSON replay uses stored message');

  await page.evaluate(() => {
    streamFixture.mode = 'network';
    Object.assign(streamFixture.snapshot, {
      run_id:'run-6', turn_id:'turn-5', turn_index:5, teacher_message_id:51,
      message:{id:52, role:'student', content:'연결 오류 뒤 저장된 답변', created_at:'2026-10-08T00:00:00Z'}
    });
  });
  await input.fill('fetch 연결 오류 질문');
  await input.press('Enter');
  await page.waitForFunction(() => document.querySelector('[data-message-id="52"]'), null, {timeout:3000});
  assert(await page.evaluate(() => streamFixture.posts.length === 6 && streamFixture.gets.length === 2), 'fetch error looks up saved status without regeneration');

  await page.evaluate(() => { streamFixture.mode = 'auth'; });
  await input.fill('로그인 후 이어 쓸 초안');
  await input.press('Enter');
  await page.locator('#session-login-btn').waitFor({state:'visible', timeout:3000});
  assert(await input.inputValue() === '로그인 후 이어 쓸 초안', 'auth expiry restores in-flight draft');
  assert(await page.evaluate(() => {
    const pending = JSON.parse(sessionStorage.getItem('chat-run-1'));
    return pending.content === '로그인 후 이어 쓸 초안' && pending.request_id === streamFixture.posts.at(-1).body.request_id;
  }), 'auth expiry preserves the exact request and draft in tab storage');
  assert(await page.locator('#polling-enabled').inputValue() === 'false', 'auth expiry does not restart polling');
  await page.route('**/login', route => route.fulfill({contentType:'text/html', body:'<h1>Login fixture</h1>'}));
  await page.locator('#session-login-btn').click();
  await page.waitForURL('**/login');
  await page.goto(`${base}/chat`);
  await page.getByRole('button', {name:'같은 요청 다시 전송', exact:true}).waitFor({state:'visible', timeout:3000});
  assert(await input.inputValue() === '로그인 후 이어 쓸 초안', 'login return restores the exact tab draft');
  assert(await page.evaluate(() => streamFixture.posts.length === 0 && streamFixture.gets.length === 1 && streamFixture.gets[0].includes('/runs?request_id=')), 'login return checks ownership-protected status without automatic generation');
  assert(legacyPosts === 0, 'mounted stream form never also sends a legacy POST');
  assert(pollRequests === 0, 'stream and recovery send zero legacy message polls');
  assert(errors.length === 0, errors.join('; '));
  return {checks:['incremental UTF-8 text, optimistic reconciliation, completion, keyboard, focus, CSRF, no polling', 'failure, partial exclusion, draft preservation and same-turn retry', 'nonterminal EOF, missing accepted frame, stored completion lookup without regeneration', 'same POST returns existing-run JSON', 'bare API 401 restores draft and preserves request in sessionStorage'], pageErrors:errors};
}
