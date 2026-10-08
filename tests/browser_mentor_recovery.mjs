// Mentor replay/recovery uses stored runs and never starts an automatic retry.
export default async function checkMentorRecovery(page) {
  const base = new URL(page.url()).origin;
  const assert = (ok, message) => { if (!ok) throw new Error(message); };
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => {
    const original = window.fetch;
    const fixture = window.mentorRecovery = {posts:[], reads:[], mode:'replay'};
    const snapshot = requestId => ({run_id:'mentor-1', turn_id:'turn-1', turn_index:1,
      request_id:requestId, operation:'mentor', teacher_message_id:10,
      status:fixture.mode === 'running' ? 'running' : 'completed',
      result_kind:'no_intervention', message:null});
    window.fetch = async (url, options = {}) => {
      if (url.endsWith('/turns/stream')) {
        const request = JSON.parse(options.body);
        fixture.studentRequest = request;
        const common = {turn_id:'turn-1', run_id:'student-1', turn_index:1};
        const events = [
          ['run.accepted', {...common, seq:0, request_id:request.request_id,
            teacher_message_id:10, operation:'student', status:'running'}],
          ['output.completed', {...common, seq:1, status:'completed',
            message:{id:11, role:'student', content:'Stored student'}}]
        ];
        if (fixture.mode === 'studentRecovery') events.pop();
        return new Response(events.map(([type, data]) => `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`).join(''),
          {headers:{'Content-Type':'text/event-stream'}});
      }
      if (url.endsWith('/mentor/stream')) {
        const request = JSON.parse(options.body);
        fixture.posts.push(request);
        if (['replay', 'resend', 'studentRecovery'].includes(fixture.mode)) {
          return new Response(JSON.stringify(snapshot(request.request_id)),
            {headers:{'Content-Type':'application/json'}});
        }
        if (fixture.mode === 'busy') return new Response(JSON.stringify({detail:{code:'mentor_busy'}}),
          {status:409, headers:{'Content-Type':'application/json'}});
        const accepted = {...snapshot(request.request_id), status:'running', seq:0};
        return new Response(fixture.mode === 'lost' || fixture.mode === 'missing' ? '' :
          `event: run.accepted\ndata: ${JSON.stringify(accepted)}\n\n`,
          {headers:{'Content-Type':'text/event-stream'}});
      }
      if (url.startsWith('/runs/') || url.includes('/runs?request_id=')) {
        fixture.reads.push(url);
        if (url === '/runs/student-1') return new Response(JSON.stringify({
          run_id:'student-1', turn_id:'turn-1', turn_index:1, operation:'student',
          request_id:fixture.studentRequest.request_id, teacher_message_id:10,
          status:'completed', result_kind:'message',
          message:{id:11, role:'student', content:'Stored student'}
        }), {headers:{'Content-Type':'application/json'}});
        if (fixture.mode === 'missing') return new Response('{}', {status:404, headers:{'Content-Type':'application/json'}});
        return new Response(JSON.stringify(snapshot(fixture.posts[0]?.request_id || 'restored-key')),
          {headers:{'Content-Type':'application/json'}});
      }
      return original(url, options);
    };
  });
  const load = async mode => {
    await page.goto(`${base}/chat?mentor=1`);
    await page.evaluate(mode => {
      sessionStorage.clear();
      mentorRecovery.mode = mode;
    }, mode);
  };
  const send = async () => {
    await page.locator('#teacher-input').fill('Teacher question');
    await page.locator('#teacher-input').press('Enter');
  };
  const slot = page.locator('.mentor-slot[data-turn-id="turn-1"]');
  await load('replay');
  await send();
  await slot.getByText('멘토 미개입', {exact:true}).waitFor({timeout:3000});
  assert(await page.evaluate(() => mentorRecovery.posts.length === 1 && mentorRecovery.reads.length === 0), 'JSON replay reuses the completed result');

  await load('studentRecovery');
  await send();
  await slot.getByText('멘토 미개입', {exact:true}).waitFor({timeout:3000});
  assert(await page.evaluate(() => mentorRecovery.posts.length === 1 &&
    JSON.stringify(mentorRecovery.reads) === '["/runs/student-1"]'),
    'a recovered durable student completion also starts its independent mentor');

  for (const mode of ['eof', 'lost']) {
    await load(mode);
    await send();
    await slot.getByText('멘토 미개입', {exact:true}).waitFor({timeout:3000});
    assert(await page.evaluate(mode => mentorRecovery.posts.length === 1 && mentorRecovery.reads.length === 1 &&
      mentorRecovery.reads[0].startsWith(mode === 'lost' ? '/sessions/1/runs?' : '/runs/'), mode),
      'EOF recovers by run ID or missed-acceptance request ID without another generation');
  }
  await load('missing');
  await send();
  await slot.getByRole('button', {name:'같은 요청 다시 전송', exact:true}).waitFor({timeout:3000});
  await page.evaluate(() => { mentorRecovery.mode = 'resend'; });
  await slot.getByRole('button', {name:'같은 요청 다시 전송', exact:true}).click();
  await slot.getByText('멘토 미개입', {exact:true}).waitFor();
  assert(await page.evaluate(() => mentorRecovery.posts.length === 2 &&
    mentorRecovery.posts[0].request_id === mentorRecovery.posts[1].request_id), 'uncertain acceptance is explicitly retransmitted with the same key');

  await load('busy');
  await send();
  await slot.getByText('이전 코칭 처리 중 — 이후 다시 요청 가능', {exact:true}).waitFor();
  assert(await slot.getByRole('button', {name:'멘토 다시 요청'}).isEnabled(), 'FastAPI detail envelope offers explicit busy recovery');

  await load('running');
  await page.clock.install();
  await send();
  await page.waitForFunction(() => mentorRecovery.reads.length === 1);
  for (const elapsed of [1000, 2000, 4000, 8000, 15000]) {
    await page.clock.runFor(elapsed);
    await page.waitForTimeout(20);
  }
  assert(await page.evaluate(() => mentorRecovery.reads.length === 6 && mentorRecovery.posts.length === 1), 'recovery stops after immediate + 1/3/7/15/30 second reads');
  await page.clock.runFor(60000);
  assert(await page.evaluate(() => mentorRecovery.reads.length === 6), 'no polling beyond 30 seconds');
  await slot.getByRole('button', {name:'상태 다시 확인', exact:true}).waitFor();
  assert(await page.locator('#teacher-input').isEnabled(), 'mentor recovery never locks the student composer');
  await page.clock.resume();

  // A refresh reconstructs a stored mentor request beside its persisted student.
  await page.goto(`${base}/chat?mentor=1&mentor_history=1`);
  await slot.getByText('멘토 미개입', {exact:true}).waitFor({timeout:3000});
  assert(await page.evaluate(() => mentorRecovery.posts.length === 0 && mentorRecovery.reads.length === 1), 'refresh looks up the pending mentor and does not regenerate');

  await page.evaluate(() => sessionStorage.clear());
  await page.goto(`${base}/chat`);
  await send();
  await page.waitForFunction(() => !document.querySelector('#teacher-input').disabled);
  assert(await page.evaluate(() => mentorRecovery.posts.length === 0), 'disabled mentor makes zero mentor/judgment requests');
  assert(errors.length === 0, `page errors: ${errors}`);
  return {checks:['JSON replay', 'recovered student completion starts mentor', 'EOF and missed acceptance recovery', 'same-key explicit resend', 'nested busy error',
    'bounded recovery without composer lock', 'refresh recovery without generation', 'disabled mentor makes zero calls'], pageErrors:errors};
}
