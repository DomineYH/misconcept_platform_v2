import assert from 'node:assert/strict';

export default async function checkMentorOutcomes(page) {
  page.setDefaultTimeout(5000);
  const base = new URL(page.url()).origin;
  page.on('dialog', dialog => dialog.accept());
  await page.route('**/sessions/*/end', route => route.fulfill({json:{feedback_status:'failed', retryable:true, error:'analysis_failed'}}));
  await page.route('**/sessions/*/analyze', route => route.fulfill({json:{feedback_status:'failed', retryable:true, error:'analysis_failed'}}));
  await page.addInitScript(() => {
    const original = window.fetch;
    window.outcomeFixture = {posts:[], reads:0, outcome:'preflight'};
    window.fetch = async (url, options = {}) => {
      if (url.endsWith('/turns/stream')) {
        const request = JSON.parse(options.body);
        const common = {turn_id:'target-turn', run_id:'student-run', turn_index:1};
        const events = [
          ['run.accepted', {...common, seq:0, request_id:request.request_id,
            teacher_message_id:10, operation:'student', status:'running'}],
          ['output.completed', {...common, seq:1, status:'completed',
            message:{id:11, role:'student', content:'학생 답변'}}]
        ];
        return new Response(events.map(([type, data]) => `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`).join(''),
          {headers:{'Content-Type':'text/event-stream'}});
      }
      if (url.endsWith('/mentor/stream')) {
        const request = JSON.parse(options.body);
        outcomeFixture.posts.push(request);
        if (outcomeFixture.outcome === 'waiting') {
          return new Response(new ReadableStream({start(controller) {
            const common = {run_id:'late-run', turn_id:'target-turn'};
            controller.enqueue(new TextEncoder().encode(`event: run.accepted\ndata: ${JSON.stringify({
              ...common, seq:0, status:'running', operation:'mentor', request_id:request.request_id
            })}\n\n`));
            // An upstream can finish even after receiving cancellation.
            options.signal.addEventListener('abort', () => { outcomeFixture.aborted = true; });
            outcomeFixture.completeAfterEnd = () => {
              controller.enqueue(new TextEncoder().encode(`event: output.completed\ndata: ${JSON.stringify({
                ...common, seq:1, status:'completed', result_kind:'message',
                message:{id:99, role:'tutor', content:'종료 뒤 폐기할 코칭'}
              })}\n\n`));
              controller.close();
            };
          }}), {headers:{'Content-Type':'text/event-stream'}});
        }
        if (outcomeFixture.outcome === 'preflight') return Response.json({detail:{code:'context_limit',
          message:'PRIVATE REASON <img src=x>', context_budget_json:{reserved_output_tokens:99999}}}, {status:422});
        if (outcomeFixture.outcome !== 'coaching') {
          const common = {run_id:'mentor-run', turn_id:'target-turn'};
          const failed = {...common, seq:2, status:'failed', code:outcomeFixture.outcome,
            message:'PRIVATE REASON <img src=x>', reason_summary:'PRIVATE REASON', retryable:true};
          if (outcomeFixture.outcome === 'snapshot') return Response.json({...failed,
            code:undefined, error_code:'context_limit'});
          const events = [
            ['run.accepted', {...common, seq:0, status:'running', operation:'mentor', request_id:request.request_id}],
            ['output.delta', {...common, seq:1, text:'{"reason_summary":"PRIVATE REASON"}'}],
            ['run.failed', failed]
          ];
          return new Response(events.map(([type, data]) => `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`).join(''),
            {headers:{'Content-Type':'text/event-stream'}});
        }
        return Response.json({run_id:'mentor-run', turn_id:'target-turn', status:'completed', result_kind:'message',
          message:{id:12, role:'tutor', content:'명시적으로 다시 요청한 코칭'}});
      }
      if (url.startsWith('/runs/') || url.includes('/runs?')) {
        outcomeFixture.reads++;
        return Response.json({}, {status:404});
      }
      return original(url, options);
    };
  });
  await page.goto(`${base}/chat?mentor=1`);
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  const input = page.locator('#teacher-input');
  await input.fill('멘토 문맥 한도 안내');
  await input.press('Enter');
  const slot = page.locator('.mentor-slot');
  await slot.getByText('질문을 줄이거나 관리자에게 수업 설정 확인을 요청해주세요.', {exact:false}).waitFor({timeout:3000});
  assert(await input.isEnabled(), 'mentor preflight never locks the student composer');
  assert.equal(await page.evaluate(() => outcomeFixture.reads), 0, 'preflight never polls an unaccepted mentor');
  assert.equal(await slot.locator('.mentor-run-status').getAttribute('role'), 'status');
  assert(!(await slot.innerText()).includes('PRIVATE'));
  assert(!(await slot.innerText()).includes('99999'));
  assert.equal(await slot.getByRole('button').count(), 0, 'preflight 422 offers no retry control');
  const posts = await page.evaluate(() => outcomeFixture.posts);
  assert.equal(posts.length, 1);
  assert.equal(posts[0].trigger, 'auto');
  await page.clock.install();
  for (const [code, guidance] of Object.entries({
    invalid_output:'멘토 응답 형식이 올바르지 않습니다.',
    timeout_connect:'멘토 응답 대기 시간이 초과되었습니다.',
    timeout_first_output:'멘토 응답 대기 시간이 초과되었습니다.',
    timeout_total:'멘토 응답 대기 시간이 초과되었습니다.',
    refused:'멘토 응답이 거절되었습니다.',
    context_limit:'질문을 줄이거나 관리자에게 수업 설정 확인을 요청해주세요.',
    snapshot:'질문을 줄이거나 관리자에게 수업 설정 확인을 요청해주세요.'
  })) {
    await page.evaluate(() => sessionStorage.clear());
    await page.goto(`${base}/chat?mentor=1`);
    await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
    await page.evaluate(code => { outcomeFixture.outcome = code; }, code);
    await input.fill(`실패 안내 ${code}`);
    await input.press('Enter');
    await slot.getByText(guidance, {exact:false}).waitFor({timeout:3000});
    assert.equal(await slot.getAttribute('data-status'), 'failed');
    assert(await slot.locator('.message-bubble').isHidden(), 'failure is neither coaching nor no-intervention');
    assert(!(await slot.innerText()).includes('미개입'));
    assert(!(await page.locator('body').innerText()).includes('PRIVATE REASON'));
    assert.equal(await slot.locator('img').count(), 0);
    assert(await input.isEnabled());
    await page.clock.runFor(60000);
    assert.equal(await page.evaluate(() => outcomeFixture.posts.length), 1, `${code}: no automatic retry`);
    await page.evaluate(() => { outcomeFixture.outcome = 'coaching'; });
    await slot.getByRole('button', {name:'멘토 다시 요청', exact:true}).focus();
    await page.keyboard.press('Enter');
    await slot.getByText('명시적으로 다시 요청한 코칭', {exact:true}).waitFor();
    const requests = await page.evaluate(() => outcomeFixture.posts);
    assert.equal(requests.length, 2);
    assert.equal(requests[0].trigger, 'auto');
    assert.equal(requests[1].trigger, 'manual');
    assert.notEqual(requests[0].request_id, requests[1].request_id);
  }
  await page.evaluate(() => sessionStorage.clear());
  await page.goto(`${base}/chat?mentor=1`);
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  await page.evaluate(() => { outcomeFixture.outcome = 'waiting'; });
  await input.fill('종료 race');
  await input.press('Enter');
  await page.waitForFunction(() => outcomeFixture.completeAfterEnd);
  assert(await input.isEnabled(), 'accepted mentor does not block student completion');
  await page.locator('#end-session-btn').click();
  await page.waitForFunction(() => document.querySelector('#end-session-btn').dataset.state === 'ready-to-analyze');
  assert(await page.evaluate(() => outcomeFixture.aborted), 'ending signals cancellation');
  await page.evaluate(() => outcomeFixture.completeAfterEnd());
  await page.clock.runFor(1000);
  assert.equal(await page.locator('[data-message-id="99"]').count(), 0, 'post-end result is discarded');
  assert(!(await slot.innerText()).includes('종료 뒤 폐기할 코칭'));
  assert.equal(await slot.getAttribute('data-status'), 'cancelled');
  assert.equal(await slot.getByRole('button').count(), 0);
  return {checks:['mentor preflight context_limit guidance without a retry control',
    'invalid output, timeouts, refusal and context failures stay distinct from coaching/no-intervention',
    'SSE and JSON failures hide raw output and only retry through keyboard action',
    'late completion after session end is discarded even when upstream ignores cancellation']};
}
