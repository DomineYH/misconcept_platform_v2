import assert from 'node:assert/strict';

export default async function checkContextLimit(page) {
  page.setDefaultTimeout(5000);
  const base = new URL(page.url()).origin;
  await page.addInitScript(() => {
    const original = window.fetch;
    window.contextFixture = {posts:[], gets:0};
    window.fetch = async (url, options = {}) => {
      if (url.endsWith('/turns/stream')) {
        const request = JSON.parse(options.body);
        contextFixture.posts.push(request);
        if (request.content === '단축한 질문') return Response.json({
          request_id:request.request_id, run_id:'short-run', turn_id:'short-turn', turn_index:1,
          teacher_message_id:61, status:'completed', message:{id:62, role:'student', content:'단축 질문 답변'}
        });
        if (contextFixture.failStudent) {
          contextFixture.failStudent = false;
          const common = {run_id:'failed-run', turn_id:'failed-turn', turn_index:1};
          const events = [
            ['run.accepted', {...common, operation:'student', seq:0, request_id:request.request_id,
              teacher_message_id:51, status:'running'}],
            ['output.delta', {...common, seq:1, text:'기존 잠정 응답'}],
            ['run.failed', {...common, seq:2, status:'failed', code:'timeout_total',
              message:'학생 응답 생성에 실패했습니다.', retryable:true}]
          ];
          return new Response(events.map(([event, data]) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join(''),
            {headers:{'Content-Type':'text/event-stream'}});
        }
        return Response.json({detail:{code:'context_limit', message:'PRIVATE PROMPT',
          context_budget_json:{estimated_input_tokens:99999}}}, {status:422});
      }
      if (url.startsWith('/runs/') || url.includes('/runs?')) {
        contextFixture.gets++;
        contextFixture.lookup = url;
        if (url === '/runs/failed-run') return Response.json({
          run_id:'failed-run', turn_id:'failed-turn', turn_index:1, teacher_message_id:51,
          request_id:JSON.parse(sessionStorage.getItem('chat-run-1')).request_id,
          status:'failed', partial_text:'기존 잠정 응답', retryable:true
        });
        return Response.json({detail:'Not found'}, {status:404});
      }
      return original(url, options);
    };
  });
  await page.goto(`${base}/chat?stream`);
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  const input = page.locator('#teacher-input');
  await input.fill('너무 긴 새 질문🙂');
  await input.press('Enter');
  const notice = page.locator('.student-run-status');
  await notice.getByText('질문을 줄이거나 관리자에게 수업 설정 확인을 요청해주세요.', {exact:false}).waitFor({timeout:3000});
  assert.equal(await input.inputValue(), '너무 긴 새 질문🙂');
  assert(await input.isEnabled(), 'preflight rejection allows editing');
  assert(await input.evaluate(el => el === document.activeElement), 'draft keeps keyboard focus');
  assert.equal(await page.locator('.message-teacher').count(), 0, 'unaccepted question is not a conversation turn');
  assert.equal(await page.locator('[data-message-id]').count(), 0);
  assert.equal(await notice.getAttribute('role'), 'status');
  assert(!(await notice.innerText()).includes('PRIVATE'));
  assert(!(await notice.innerText()).includes('99999'));
  assert.equal(await page.evaluate(() => sessionStorage.getItem('chat-run-1')), null);
  assert.equal(await page.evaluate(() => contextFixture.gets), 0, 'preflight rejection has no lookup');
  await page.setViewportSize({width:390, height:844});
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= 390), 'guidance fits narrow screens');
  await page.reload();
  assert.equal(await input.inputValue(), '너무 긴 새 질문🙂', 'editable draft survives reload');
  assert.equal(await page.evaluate(() => contextFixture.gets), 0, 'rejection never polls a nonexistent run');
  await input.fill('단축한 질문');
  await input.press('Enter');
  await page.waitForFunction(() => contextFixture.posts.length === 1 && !document.querySelector('#teacher-input').disabled);
  assert.equal(await input.inputValue(), '');
  assert.equal(await page.locator('[data-message-id="62"] .message-bubble').innerText(), '단축 질문 답변');
  assert.equal(await page.locator('.message-teacher').count(), 1, 'shortened input can become a new completed turn');

  await page.evaluate(() => sessionStorage.clear());
  await page.goto(`${base}/chat?stream`);
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  await page.evaluate(() => { contextFixture.failStudent = true; });
  await input.fill('실패 턴의 원래 교사 질문');
  await input.press('Enter');
  const retry = page.getByRole('button', {name:'학생 응답 다시 받기', exact:true});
  await retry.waitFor({timeout:3000});
  const failed = await page.evaluate(() => JSON.parse(sessionStorage.getItem('chat-run-1')));
  await retry.click();
  await notice.getByText('질문을 줄이거나 관리자에게 수업 설정 확인을 요청해주세요.', {exact:false}).waitFor({timeout:3000});
  assert.equal(await page.locator('[data-message-id="51"] .message-bubble').innerText(), '실패 턴의 원래 교사 질문');
  assert.equal(await page.locator('.message-teacher').count(), 1, 'retry preserves the stored teacher without duplication');
  assert.equal(await input.inputValue(), '실패 턴의 원래 교사 질문');
  assert(await input.isDisabled(), 'a stored failed turn still uses same-turn retry');
  assert(await retry.isEnabled(), 'retry remains explicit after settings are checked');
  const after = await page.evaluate(() => JSON.parse(sessionStorage.getItem('chat-run-1')));
  assert.equal(after.run_id, failed.run_id, 'preflight rejection preserves the previous failed run');
  assert.equal(after.request_id, failed.request_id);
  assert.equal(after.partial_text, '기존 잠정 응답');
  assert((await notice.innerText()).includes('응답 미완료·대화 기록에 미포함'), 'retained partial answer remains marked incomplete');
  assert.equal(await page.evaluate(() => contextFixture.gets), 0, 'rejected retry has no recovery poll');
  assert.equal(await page.evaluate(() => contextFixture.posts[1].turn_id), 'failed-turn');
  await page.reload();
  await retry.waitFor({timeout:3000});
  assert.equal(await page.evaluate(() => contextFixture.lookup), '/runs/failed-run', 'reload recovers the existing failure');
  assert.equal(await page.locator('[data-message-id="51"]').count(), 1);
  return {checks:['context_limit preserves editable draft, focus, reload and safe status without polling',
    'rejected failed-turn retry preserves teacher, previous failed run and partial text']};
}
