// Recovery uses the real UI and fake fetch responses; the clock proves the 30s bound.
export default async function checkStudentRecovery(page) {
  const base = page.url().split('/').slice(0, 3).join('/');
  const assert = (value, message) => { if (!value) throw new Error(message); };
  const errors = [];
  let messagePolls = 0;
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/sessions/*/messages/updates*', route => {
    messagePolls++;
    return route.fulfill({status:204});
  });
  await page.clock.install();
  await page.clock.pauseAt(new Date(Date.now() + 1000));
  await page.addInitScript(() => {
    const originalFetch = window.fetch;
    const restored = JSON.parse(sessionStorage.getItem('recovery-snapshot') || 'null');
    window.recoveryFixture = {posts:[], gets:[], snapshot:{
      run_id:'recover-run', turn_id:'recover-turn', turn_index:1, operation:'student',
      session_id:1, teacher_message_id:51, status:'running', result_kind:null,
      message:null, partial_text:null, error_code:null, retryable:false
    }};
    if (restored) recoveryFixture.snapshot = restored;
    window.fetch = async (url, options = {}) => {
      const fixture = window.recoveryFixture;
      if (url.endsWith('/turns/stream')) {
        fixture.posts.push(JSON.parse(options.body));
        fixture.snapshot.request_id = fixture.posts.at(-1).request_id;
        const accepted = {...fixture.snapshot, status:'running', seq:0};
        return new Response(new ReadableStream({start(controller) {
          if (!fixture.missingAccepted) controller.enqueue(new TextEncoder().encode(`event: run.accepted\ndata: ${JSON.stringify(accepted)}\n\n`));
          if (fixture.terminal) {
            const common = {run_id:accepted.run_id, turn_id:accepted.turn_id};
            controller.enqueue(new TextEncoder().encode(
              `event: output.delta\ndata: ${JSON.stringify({...common, seq:1, text:'모바일 잠정 본문'})}\n\n` +
              `event: run.${fixture.terminal}\ndata: ${JSON.stringify({...common, seq:2, status:fixture.terminal, code:'disconnected', message:'학생 응답이 중단되었습니다.', retryable:true})}\n\n` +
              `event: output.completed\ndata: ${JSON.stringify({...common, seq:3, status:'completed', turn_index:3, message:{id:72, role:'student', content:'무시해야 할 늦은 종료'}})}\n\n`
            ));
          }
          controller.close();
        }}), {headers:{'Content-Type':'text/event-stream'}});
      }
      if (url.startsWith('/runs/') || url.includes('/runs?')) {
        fixture.gets.push({url, at:Date.now()});
        if (fixture.holdLookup) await new Promise(resolve => { fixture.releaseLookup = resolve; });
        return new Response(JSON.stringify(fixture.snapshot), {
          status:fixture.lookupStatus || 200, headers:{'Content-Type':'application/json'}
        });
      }
      return originalFetch(url, options);
    };
  });
  await page.goto(`${base}/chat?stream`);
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  const input = page.locator('#teacher-input');
  await input.fill('연결이 끊긴 질문');
  await input.press('Enter');
  await page.waitForFunction(() => recoveryFixture.gets.length === 1 && document.querySelector('.student-run-status').textContent.includes('저장 상태 확인 중'));
  for (const [step, delay] of [1000, 2000, 4000, 8000, 15000].entries()) {
    await page.clock.runFor(delay);
    await page.waitForFunction(count => recoveryFixture.gets.length === count && document.querySelector('.student-run-status').textContent.includes(count === 6 ? '상태 다시 확인' : '저장 상태 확인 중'), step + 2, {timeout:2000});
  }
  const times = await page.evaluate(() => recoveryFixture.gets.map(item => item.at - recoveryFixture.gets[0].at));
  assert(JSON.stringify(times) === '[0,1000,3000,7000,15000,30000]', `recovery queries at elapsed 0,1,3,7,15,30 seconds: ${times}`);
  await page.clock.runFor(60000);
  assert(await page.evaluate(() => recoveryFixture.gets.length === 6 && recoveryFixture.posts.length === 1), 'bounded recovery stops without automatic generation');
  assert(await input.isDisabled(), 'running lookup keeps the new-question lock');
  assert(await page.getByRole('button', {name:'학생 응답 다시 받기', exact:true}).isDisabled(), 'unknown running state cannot regenerate');
  await page.evaluate(() => Object.assign(recoveryFixture.snapshot, {
    status:'interrupted', partial_text:'중단된 잠정 본문', error_code:'server_restarted', retryable:true
  }));
  await page.getByRole('button', {name:'상태 다시 확인', exact:true}).click();
  await page.waitForFunction(() => document.querySelector('.message-student .message-bubble').textContent === '중단된 잠정 본문');
  assert(await page.getByRole('button', {name:'학생 응답 다시 받기', exact:true}).isEnabled(), 'interrupted run offers explicit retry');
  assert(await page.evaluate(() => recoveryFixture.gets.every(item => item.url === '/runs/recover-run')), 'accepted recovery uses run ID');

  await page.evaluate(() => {
    const snapshot = {...recoveryFixture.snapshot, status:'completed', retryable:false,
      message:{id:52, role:'student', content:'새로고침 후 저장된 답변', created_at:'2026-10-08T00:00:00Z'}};
    sessionStorage.setItem('recovery-snapshot', JSON.stringify(snapshot));
    document.querySelector('#teacher-input').value = '새로고침에도 보존할 다음 초안';
  });
  await page.reload();
  await page.waitForFunction(() => document.querySelector('[data-message-id="52"]'), null, {timeout:3000});
  assert(await page.evaluate(() => recoveryFixture.posts.length === 0 && recoveryFixture.gets.length === 1), 'reload looks up the saved run without generation');
  assert(await input.inputValue() === '새로고침에도 보존할 다음 초안', 'reload preserves the next draft');
  assert(await page.locator('[data-message-id="51"]').count() === 1, 'reload restores the teacher slot');
  assert(await page.locator('[data-message-id="52"] .message-bubble').innerText() === '새로고침 후 저장된 답변', 'reload restores server-final text');

  await page.evaluate(() => {
    recoveryFixture.missingAccepted = true;
    recoveryFixture.lookupStatus = 404;
    Object.assign(recoveryFixture.snapshot, {
      run_id:'resend-run', turn_id:'resend-turn', turn_index:2, teacher_message_id:61,
      message:{id:62, role:'student', content:'동일 요청 재전송 완료', created_at:'2026-10-08T00:00:00Z'}
    });
  });
  await input.fill('수락 여부를 모르는 질문');
  await input.press('Enter');
  const resend = page.getByRole('button', {name:'같은 요청 다시 전송', exact:true});
  await resend.waitFor({state:'visible', timeout:3000});
  assert(await page.evaluate(() => recoveryFixture.posts.length === 1), '404 never triggers automatic resend');
  assert(await input.isDisabled(), 'missing run still blocks a different question');
  await page.evaluate(() => { recoveryFixture.missingAccepted = false; recoveryFixture.lookupStatus = 200; });
  await resend.click();
  await page.waitForFunction(() => document.querySelector('[data-message-id="62"]'), null, {timeout:3000});
  assert(await page.evaluate(() => JSON.stringify(recoveryFixture.posts[0]) === JSON.stringify(recoveryFixture.posts[1])), 'explicit 404 resend reuses the exact request ID and input');
  assert(await page.locator('[data-message-id="61"]').count() === 1, 'resend reconciles the existing optimistic teacher');

  await input.fill('아직 전송하지 않은 탭 초안');
  await page.reload();
  assert(await input.inputValue() === '아직 전송하지 않은 탭 초안', 'unsent draft survives reload in the same tab');
  assert(await page.evaluate(() => recoveryFixture.posts.length === 0 && recoveryFixture.gets.length === 0), 'an unsent draft does not query or generate');

  await page.setViewportSize({width:390, height:844});
  await page.evaluate(() => {
    recoveryFixture.terminal = 'interrupted';
    Object.assign(recoveryFixture.snapshot, {
      run_id:'mobile-run', turn_id:'mobile-turn', turn_index:3, teacher_message_id:71,
      status:'running', message:null, partial_text:null, retryable:false
    });
  });
  await input.fill('모바일 중단 질문');
  await input.press('Enter');
  const retry = page.getByRole('button', {name:'학생 응답 다시 받기', exact:true}).last();
  await retry.waitFor({state:'visible', timeout:3000});
  assert(await retry.isEnabled(), 'mobile interruption exposes retry');
  assert(await page.getByRole('button', {name:'대화 종료', exact:true}).last().isVisible(), 'mobile recovery exposes end without switching panels');
  assert(await page.locator('[data-message-id="72"]').count() === 0, 'terminal interruption excludes a later conflicting completion');
  assert(await input.isDisabled(), 'mobile interrupted turn cannot send a new question');
  await page.evaluate(() => Object.assign(recoveryFixture.snapshot, {
    status:'cancelled', partial_text:'모바일 잠정 본문', error_code:'session_ended', retryable:false
  }));
  await page.getByRole('button', {name:'상태 다시 확인', exact:true}).last().click();
  await page.waitForFunction(() => document.querySelector('#end-session-btn').dataset.state === 'ready-to-analyze');
  assert(await retry.isDisabled(), 'cancelled run cannot retry');
  assert(await input.isDisabled(), 'cancelled session remains locked');
  const count = await page.evaluate(() => recoveryFixture.gets.length);
  await page.clock.runFor(60000);
  assert(await page.evaluate(() => recoveryFixture.gets.length) === count, 'terminal cancellation stops recovery queries');

  await page.goto(`${base}/chat`);
  await page.evaluate(() => sessionStorage.clear());
  await page.goto(`${base}/chat?stream`);
  await page.clock.resume();
  await page.evaluate(() => { recoveryFixture.holdLookup = true; });
  await input.fill('페이지 이탈 중인 질문');
  await input.press('Enter');
  await page.waitForFunction(() => recoveryFixture.releaseLookup);
  await page.evaluate(() => {
    window.dispatchEvent(new PageTransitionEvent('pagehide'));
    recoveryFixture.releaseLookup();
  });
  await page.clock.runFor(60000);
  assert(await page.evaluate(() => recoveryFixture.gets.length === 1), 'leaving during a held lookup cannot restart recovery');
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true})));
  await page.waitForFunction(() => recoveryFixture.gets.length === 2);
  await page.clock.runFor(31000);
  await page.evaluate(() => recoveryFixture.releaseLookup());
  await page.clock.runFor(60000);
  assert(await page.evaluate(() => recoveryFixture.gets.length === 2), 'a lookup delayed past 30s cannot start catch-up queries');
  assert(messagePolls === 0, 'stream and recovery never poll messages');
  assert(errors.length === 0, errors.join('; '));
  return {checks:['bounded running recovery, explicit status refresh, interruption, run-ID lookup, zero automatic generation', 'reload restores tab request, teacher slot, final answer and next draft', 'lookup 404 offers only explicit identical-request resend', 'mobile recovery controls, streamed interruption, terminal exclusivity and cancellation'], pageErrors:errors};
}
