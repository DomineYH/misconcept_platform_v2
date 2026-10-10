// Real Jinja/chat UI, with student and mentor SSE intercepted at fetch.
export default async function checkMentorStream(page) {
  const base = page.url().split('/').slice(0, 3).join('/');
  const assert = (value, message) => { if (!value) throw new Error(message); };
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => dialog.accept());
  await page.route('**/sessions/*/end', route => route.fulfill({status:200, contentType:'application/json', body:'{"ended":true}'}));
  await page.route('**/sessions/*/analyze', route => route.fulfill({status:200, contentType:'application/json', body:'{"feedback_status":"failed","retryable":true,"error":"analysis_failed"}'}));
  await page.context().addCookies([{name:'csrftoken', value:'mentor-csrf', url:base}]);
  await page.addInitScript(() => {
    const originalFetch = window.fetch;
    window.mentorFixture = {students:[], mentors:[], controllers:{}};
    window.fetch = async (url, options = {}) => {
      const fixture = window.mentorFixture;
      if (url.endsWith('/turns/stream')) {
        const body = JSON.parse(options.body);
        const index = fixture.students.push(body);
        const common = {turn_id:`turn-${index}`, run_id:`student-${index}`, turn_index:index};
        return new Response(new ReadableStream({start(controller) {
          const events = [
            ['run.accepted', {...common, seq:0, request_id:body.request_id, teacher_message_id:index * 10, operation:'student', status:'running'}],
            ['output.completed', {...common, seq:1, status:'completed', message:{id:index * 10 + 1, role:'student', content:`학생 답변 ${index}`}}]
          ];
          for (const [event, data] of events) controller.enqueue(new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`));
          controller.close();
        }}), {headers:{'Content-Type':'text/event-stream'}});
      }
      if (url.endsWith('/mentor/stream')) {
        const turn = url.split('/').at(-3);
        fixture.mentors.push({turn, body:JSON.parse(options.body), headers:options.headers});
        if (fixture.reply) {
          const reply = fixture.reply;
          fixture.reply = null;
          return new Response(JSON.stringify(reply.body), {status:reply.status, headers:{'Content-Type':'application/json'}});
        }
        return new Response(new ReadableStream({start(controller) {
          fixture.controllers[turn] = controller;
          options.signal?.addEventListener('abort', () => {
            fixture.aborted = (fixture.aborted || 0) + 1;
            controller.error(new DOMException('Fixture aborted', 'AbortError'));
          }, {once:true});
        }}), {headers:{'Content-Type':'text/event-stream'}});
      }
      return originalFetch(url, options);
    };
    window.emitMentor = (turn, event, extra = {}) => {
      const request = mentorFixture.mentors.findLast(item => item.turn === turn);
      const index = Number(turn.split('-').at(-1));
      const data = {turn_id:turn, run_id:`mentor-${request.body.request_id}`, request_id:request.body.request_id,
        turn_index:index, teacher_message_id:index * 10,
        operation:'mentor', status:'running', seq:0, ...extra};
      const bytes = new TextEncoder().encode(`: ping\r\n\r\nevent: ${event}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`);
      for (const byte of bytes) mentorFixture.controllers[turn].enqueue(Uint8Array.of(byte));
    };
  });
  await page.setViewportSize({width:1280, height:900});
  await page.goto(`${base}/chat?stream&mentor=1`);
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  await page.evaluate(async () => {
    const {mountMentorStream} = await import('/static/js/mentor-stream.js');
    mountMentorStream(window.chatUI);
    mountMentorStream(window.chatUI);
  });
  const input = page.locator('#teacher-input');
  const send = async content => {
    await input.fill(content);
    await input.press('Enter');
    await page.waitForFunction(() => !document.querySelector('#teacher-input').disabled);
  };
  const slot = turn => page.locator(`.mentor-slot[data-turn-id="${turn}"]`);
  await send('첫 질문');
  await slot('turn-1').waitFor({timeout:3000});
  assert((await slot('turn-1').innerText()).includes('멘토 처리 중'), 'mentor has a pending slot next to the completed turn');
  assert(await input.isEnabled(), 'student completion unlocks input before mentor completion');
  assert(await page.locator('#end-session-btn').isEnabled(), 'mentor does not block session end');
  await page.evaluate(() => emitMentor('turn-1', 'run.accepted'));
  await send('멘토를 기다리지 않는 다음 질문');
  await slot('turn-2').waitFor({timeout:3000});
  await input.fill('작성 중인 다음 초안');
  await input.evaluate(el => { el.focus(); el.setSelectionRange(2, 5); });
  await page.evaluate(() => {
    const container = document.querySelector('#messages-container');
    container.style.scrollBehavior = 'auto';
    container.style.height = '130px';
    container.style.flex = 'none';
    container.scrollTop = 0;
    window.beforeMentorScroll = container.scrollTop;
    emitMentor('turn-1', 'output.completed', {status:'completed', seq:1, result_kind:'message',
      message:{id:99, role:'tutor', content:'늦은 멘토🙂 <img src=x onerror="window.mentorXss=1">'}});
  });
  await slot('turn-1').getByText('늦은 멘토🙂', {exact:false}).waitFor();
  assert(await page.evaluate(() => {
    const first = document.querySelector('.mentor-slot[data-turn-id="turn-1"]');
    const next = document.querySelector('.message-teacher[data-turn-id="turn-2"]');
    const input = document.querySelector('#teacher-input');
    return Boolean(first.compareDocumentPosition(next) & Node.DOCUMENT_POSITION_FOLLOWING) &&
      input === document.activeElement && input.selectionStart === 2 && input.selectionEnd === 5 &&
      document.querySelector('#messages-container').scrollTop === beforeMentorScroll;
  }), 'late coaching belongs to the original turn and preserves focus, selection and scroll');
  assert(await slot('turn-1').locator('img').count() === 0, 'mentor body is safe text');
  assert(await slot('turn-1').getByRole('button').count() === 0, 'completed coaching cannot regenerate');
  assert(await page.evaluate(() => mentorFixture.mentors.length === 2 && mentorFixture.mentors[0].headers['x-csrf-token'] === 'mentor-csrf'), 'one mentor request per completion with CSRF');
  await page.evaluate(() => {
    emitMentor('turn-2', 'run.accepted');
    emitMentor('turn-2', 'output.delta', {seq:1, text:'노출되면 안 되는 멘토 delta'});
    emitMentor('turn-2', 'output.completed', {seq:2, status:'completed', result_kind:'no_intervention', message:null});
  });
  await slot('turn-2').getByText('멘토 미개입', {exact:true}).waitFor();
  assert(await slot('turn-2').getByRole('button').count() === 0, 'normal no-intervention cannot regenerate');
  assert(!(await slot('turn-2').innerText()).includes('delta'), 'mentor only renders final body');

  await send('실패할 멘토 요청');
  await slot('turn-3').waitFor();
  await page.evaluate(() => {
    emitMentor('turn-3', 'run.accepted');
    emitMentor('turn-3', 'run.failed', {seq:1, status:'failed', code:'mentor_failed', message:'멘토 코칭 생성 실패', retryable:true});
    emitMentor('turn-3', 'output.completed', {seq:2, status:'completed', result_kind:'message', message:{id:100, role:'tutor', content:'실패 뒤 무시할 종료 프레임'}});
  });
  const retry = slot('turn-3').getByRole('button', {name:'멘토 다시 요청', exact:true});
  await retry.waitFor({timeout:3000});
  assert(await slot('turn-3').locator('[data-message-id="100"]').count() === 0 && !(await slot('turn-3').innerText()).includes('무시할'), 'failed run never accepts a later completion');
  await retry.click();
  await page.waitForFunction(() => mentorFixture.mentors.length === 4);
  assert(await page.evaluate(() => {
    const requests = mentorFixture.mentors.filter(item => item.turn === 'turn-3');
    return requests.length === 2 && requests[0].body.request_id !== requests[1].body.request_id;
  }), 'explicit retry uses a new request ID for the same turn');
  await page.evaluate(() => {
    emitMentor('turn-3', 'run.accepted');
    emitMentor('turn-3', 'run.interrupted', {seq:1, status:'interrupted', code:'disconnected', message:'멘토 코칭 중단', retryable:true});
  });
  await retry.waitFor();
  assert(await input.isEnabled(), 'failed/interrupted mentor leaves the composer available');

  await page.evaluate(() => { mentorFixture.reply = {status:409, body:{code:'mentor_busy'}}; });
  await send('이전 코칭 처리 중인 턴');
  await slot('turn-4').getByText('이전 코칭 처리 중 — 이후 다시 요청 가능', {exact:true}).waitFor({timeout:3000});
  assert(await slot('turn-4').getByRole('button', {name:'멘토 다시 요청'}).isEnabled(), 'busy offers a same-turn explicit retry');
  await page.waitForTimeout(100);
  assert(await page.evaluate(() => mentorFixture.mentors.length === 5), 'busy has no automatic queue or retry');
  await page.evaluate(() => { mentorFixture.reply = {status:409, body:{code:'mentor_turn_obsolete'}}; });
  await slot('turn-4').getByRole('button', {name:'멘토 다시 요청'}).click();
  await slot('turn-4').getByText('더 최신 학생 턴이 완료되어 이 턴은 더 이상 재요청할 수 없습니다.', {exact:true}).waitFor();
  assert(await slot('turn-4').getByRole('button').count() === 0, 'obsolete has an explanation without retry');

  await page.setViewportSize({width:390, height:844});
  await send('모바일에서 늦은 코칭');
  await slot('turn-5').waitFor();
  await page.evaluate(() => emitMentor('turn-5', 'run.accepted'));
  await send('모바일 다음 학생 질문');
  await slot('turn-6').waitFor();
  await input.fill('모바일 작성 중');
  await input.evaluate(el => { el.focus(); el.setSelectionRange(1, 4); });
  await page.evaluate(() => {
    const container = document.querySelector('#messages-container');
    container.scrollTop = 30;
    window.mobileMentorScroll = container.scrollTop;
    emitMentor('turn-5', 'output.completed', {seq:1, status:'completed', result_kind:'message',
      message:{id:101, role:'tutor', content:'모바일에서도 원래 턴에 붙는 코칭'}});
  });
  await slot('turn-5').getByText('모바일에서도 원래 턴에 붙는 코칭').waitFor();
  assert(await page.evaluate(() => {
    const input = document.querySelector('#teacher-input');
    return input === document.activeElement && input.selectionStart === 1 && input.selectionEnd === 4 &&
      document.querySelector('#messages-container').scrollTop === mobileMentorScroll &&
      document.documentElement.scrollWidth <= 390;
  }), 'mobile late coaching preserves draft selection and scroll without overflow');
  await page.locator('.mobile-tab[data-panel="scenario"]').click();
  await page.locator('#end-session-btn').click();
  await page.waitForFunction(() => document.querySelector('#end-session-btn').dataset.state === 'ready-to-analyze');
  await page.locator('.mobile-tab[data-panel="chat"]').click();
  await slot('turn-6').getByText('대화가 종료되어 멘토를 다시 요청할 수 없습니다.', {exact:true}).waitFor({timeout:3000});
  assert(await slot('turn-3').getByRole('button').count() === 0 && await slot('turn-6').getByRole('button').count() === 0, 'ending disables previous failure retry and active mentor retry');
  assert(await input.isDisabled(), 'ending locks the composer while a mentor was running');
  assert(await page.evaluate(() => mentorFixture.aborted === 1 && mentorFixture.mentors.length === 8), 'end aborts the one active mentor without another request');

  await page.goto(`${base}/chat?stream&mentor_legacy=1`);
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  await page.evaluate(async () => {
    const {mountMentorStream} = await import('/static/js/mentor-stream.js');
    mountMentorStream(window.chatUI);
  });
  assert(await page.evaluate(() => JSON.stringify(Array.from(document.querySelectorAll('[data-message-id]'), el => Number(el.dataset.messageId))) === '[7,8,9,10]'), 'legacy NULL-turn messages keep their original order');
  assert(await page.locator('.mentor-slot').count() === 0 && await page.locator('[data-message-id][data-turn-id]').count() === 0, 'legacy messages never get a guessed turn connection');
  assert(await page.locator('.message-tutor .message-sender').innerText() === '멘토', 'legacy stored tutor role uses mentor UI terminology');
  assert(await page.evaluate(() => mentorFixture.mentors.length === 0), 'legacy history never starts new mentor generation');
  assert(errors.length === 0, `page errors: ${errors}`);
  return {checks:['late coaching targets its turn', 'next student and end stay available', 'focus/selection/scroll retained', 'safe rendering, CSRF and duplicate mounting', 'no-intervention has no retry or delta', 'failed/interrupted explicit same-turn retry', 'busy explicit retry and obsolete explanation', 'mobile simultaneous student and late mentor', 'session end aborts mentor and removes retries', 'legacy NULL turns retain order and role'], pageErrors:errors};
}
