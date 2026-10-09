export default async function checkMentorModes(page) {
  const base = new URL(page.url()).origin;
  const assert = (value, message) => { if (!value) throw new Error(message); };
  await page.addInitScript(() => {
    const original = window.fetch;
    window.modePosts = [];
    window.fetch = async (url, options = {}) => {
      if (url.endsWith('/turns/stream')) {
        const body = JSON.parse(options.body);
        const common = {turn_id:'completed-turn', run_id:'student-run', turn_index:1};
        const events = [
          ['run.accepted', {...common, seq:0, request_id:body.request_id, teacher_message_id:1, operation:'student', status:'running'}],
          ['output.completed', {...common, seq:1, status:'completed', message:{id:2, role:'student', content:'Answer'}}]
        ];
        return new Response(events.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join(''), {headers:{'Content-Type':'text/event-stream'}});
      }
      if (url.endsWith('/mentor/stream')) {
        const body = JSON.parse(options.body);
        modePosts.push({...body, target:url.split('/').at(-3)});
        if (window.modeReply) {
          const reply = window.modeReply;
          window.modeReply = null;
          return new Response(JSON.stringify({detail:{code:reply.code}}), {status:reply.status, headers:{'Content-Type':'application/json'}});
        }
        const data = {run_id:'mentor-run', turn_id:'completed-turn', status:'completed',
          result_kind:body.trigger === 'auto' ? 'no_intervention' : 'message',
          message:body.trigger === 'manual' ? {id:3, role:'tutor', content:'Manual help'} : null};
        return new Response(JSON.stringify(data), {headers:{'Content-Type':'application/json'}});
      }
      return original(url, options);
    };
  });
  const send = async () => {
    await page.locator('#teacher-input').fill('Question');
    await page.locator('#teacher-input').press('Enter');
    await page.locator('.message-student[data-turn-id="completed-turn"]').waitFor();
  };
  await page.goto(`${base}/chat?stream&mentor_manual=1`);
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  assert(await page.locator('#request-mentor').isDisabled(), 'manual help needs a completed pair');
  await send();
  await page.waitForTimeout(100);
  assert(await page.evaluate(() => modePosts.length === 0), 'manual mode has no automatic request');
  await page.locator('#request-mentor').click();
  await page.getByText('Manual help', {exact:true}).waitFor();
  assert(await page.evaluate(() => modePosts.length === 1 && modePosts[0].trigger === 'manual'), 'manual help goes straight to explicit trigger');
  assert(await page.locator('#request-mentor').isDisabled(), 'completed coaching cannot regenerate');
  await page.goto(`${base}/chat?stream&mentor=1`);
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  await send();
  await page.locator('.mentor-slot').getByText('멘토 미개입', {exact:true}).waitFor();
  assert(await page.evaluate(() => modePosts.length === 1 && modePosts[0].trigger === 'auto'), 'auto mode sends auto trigger');
  await page.evaluate(() => {
    const partial = document.createElement('div');
    partial.className = 'message-student';
    partial.dataset.turnId = 'unfinished-turn';
    document.getElementById('messages-container').append(partial);
  });
  await page.locator('#request-mentor').click();
  await page.getByText('Manual help', {exact:true}).waitFor();
  assert(await page.evaluate(() => modePosts[1].target === 'completed-turn'), 'help targets the latest completed student instead of a partial row');
  assert(await page.evaluate(() => modePosts.length === 2 && modePosts[1].trigger === 'manual' && modePosts[0].request_id !== modePosts[1].request_id), 'negative auto check permits fresh manual help');
  await page.goto(`${base}/chat?stream&mentor_manual=1`);
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  await send();
  await page.evaluate(() => { window.modeReply = {status:429, code:'call_limit_reached'}; });
  await page.locator('#request-mentor').click();
  await page.locator('.mentor-slot').getByText('AI 호출이 많습니다. 잠시 후 멘토를 다시 요청해주세요.', {exact:true}).waitFor();
  assert(await page.locator('#teacher-input').isEnabled(), 'admission failure keeps student input enabled');
  await page.locator('.mentor-slot').getByRole('button', {name:'멘토 다시 요청', exact:true}).click();
  await page.getByText('Manual help', {exact:true}).waitFor();
  assert(await page.evaluate(() => modePosts.length === 2 && modePosts.every(body => body.trigger === 'manual') && modePosts[0].request_id !== modePosts[1].request_id), 'explicit retry uses a fresh manual request');
  await page.goto(`${base}/chat?stream&mentor=1`);
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  await page.evaluate(() => { window.modeReply = {status:409, code:'mentor_limit'}; });
  await send();
  await page.locator('#mentor-help-status').getByText('최근 완료 턴의 개입 상한에 도달했습니다.', {exact:true}).waitFor();
  assert(await page.locator('#request-mentor').isDisabled(), 'rolling cap disables help with safe reason');
  assert(await page.locator('#teacher-input').isEnabled(), 'rolling cap leaves next student input enabled');
  await page.evaluate(() => sessionStorage.clear());
  await page.goto(`${base}/chat?mentor_manual=1&mentor_history=1&mentor_coached=1`);
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  assert(await page.locator('#request-mentor').isDisabled(), 'reopened completed coaching disables help without session storage');
  assert(await page.evaluate(() => modePosts.length === 0), 'reopened coaching does not generate');
  await page.goto(`${base}/chat?stream`);
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  await send();
  assert(await page.locator('#request-mentor').count() === 0, 'off mode has no help control');
  assert(await page.locator('.greeting-message').count() === 0, 'off mode hides welcome');
  assert(await page.evaluate(() => modePosts.length === 0), 'off mode makes no mentor request');
  return {checks:['manual/off/auto controls', 'explicit triggers', 'manual after negative auto', 'coaching deduplication']};
}
