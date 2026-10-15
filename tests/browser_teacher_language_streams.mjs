import assert from 'node:assert/strict';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.context().addCookies([{name:'ui_lang', value:'en', url:base}]);
  await page.addInitScript(() => {
    const original = window.fetch;
    const fixture = window.teacherStreams = {requests:[], students:[], mentors:[], controllers:{}};
    window.fetch = async (url, options = {}) => {
      if (url.endsWith('/turns/stream') || url.endsWith('/mentor/stream')) {
        const mentor = url.endsWith('/mentor/stream');
        const body = JSON.parse(options.body);
        const list = mentor ? fixture.mentors : fixture.students;
        const index = mentor ? Number(url.split('/').at(-3).split('-')[1]) : list.length + 1;
        const id = `${mentor ? 'mentor' : 'student'}-${index}`;
        fixture.requests.push({url, language:new Headers(options.headers).get('x-ui-language')});
        list.push({body, index, id});
        return new Response(new ReadableStream({start(controller) {
          fixture.controllers[id] = controller;
          controller.enqueue(new TextEncoder().encode(`event: run.accepted\ndata: ${JSON.stringify({
            request_id:body.request_id, run_id:id, turn_id:`turn-${index}`, turn_index:index,
            teacher_message_id:index * 10, status:'running', operation:mentor ? 'mentor' : 'student'
          })}\n\n`));
        }}), {headers:{'Content-Type':'text/event-stream'}});
      }
      return original(url, options);
    };
    fixture.complete = (operation, index) => {
      const data = {run_id:`${operation}-${index}`, turn_id:`turn-${index}`, turn_index:index,
        status:'completed', result_kind:'message',
        message:{id:index * 10 + (operation === 'student' ? 1 : 2), role:operation === 'student' ? 'student' : 'tutor', content:`원본 ${operation} ${index}`}};
      const controller = fixture.controllers[`${operation}-${index}`];
      controller.enqueue(new TextEncoder().encode(`event: output.completed\ndata: ${JSON.stringify(data)}\n\n`));
      controller.close();
    };
  });
  await page.setViewportSize({width:1280,height:900});
  await page.goto(`${base}/chat?stream&mentor=1`);
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  const input = page.locator('#teacher-input');
  const chooseKo = page.locator('[data-ui-language="ko"]');
  async function blocked() {
    await chooseKo.click();
    assert.equal(await page.locator('html').getAttribute('lang'), 'en');
    assert.equal(await page.locator('#language-notice').innerText(), 'Wait until the task finishes before switching languages.');
  }
  // Another tab changes the shared cookie; this document keeps its language.
  const other = await page.context().newPage();
  await other.goto(`${base}/login`);
  await Promise.all([other.waitForEvent('load'), other.locator('[data-ui-language="ko"]').click()]);
  await other.close();
  await input.fill('원본 교사 질문 1');
  await input.press('Enter');
  await page.waitForFunction(() => teacherStreams.students.length === 1);
  await blocked();
  assert.equal(await page.locator('.student-run-status').innerText(), 'Student generating · provisional response');
  await page.evaluate(() => teacherStreams.complete('student', 1));
  await page.waitForFunction(() => teacherStreams.mentors.length === 1);
  assert.equal(await page.locator('.mentor-run-status').innerText(), 'Mentor processing');
  await blocked();
  await input.fill('원본 교사 질문 2');
  await input.press('Enter');
  await page.waitForFunction(() => teacherStreams.students.length === 2);
  await page.evaluate(() => teacherStreams.complete('mentor', 1));
  await blocked();
  await page.evaluate(() => teacherStreams.complete('student', 2));
  await page.waitForFunction(() => teacherStreams.mentors.length === 2);
  await blocked();
  await page.evaluate(() => teacherStreams.complete('mentor', 2));
  await page.waitForFunction(() => document.querySelectorAll('.mentor-slot[data-status="completed"]').length === 2);
  assert.deepEqual(await page.evaluate(() => teacherStreams.requests.map(item => item.language)), ['en','en','en','en']);
  assert.equal(await page.locator('.message-tutor').count(), 0);
  assert(await page.getByText('원본 mentor 1', {exact:true}).isVisible());
  await Promise.all([page.waitForEvent('load'), chooseKo.click()]);
  assert.equal(await page.locator('html').getAttribute('lang'), 'ko');
  await Promise.all([page.waitForEvent('load'), page.locator('[data-ui-language="en"]').click()]);
  await page.evaluate(() => sessionStorage.clear());
  await page.goto(`${base}/chat?stream`);
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  let snapshot;
  const lookupLanguages = [];
  await page.route('**/runs/student-1', route => {
    lookupLanguages.push(route.request().headers()['x-ui-language']);
    return route.fulfill({status:snapshot ? 200 : 404, json:snapshot || {detail:'Unknown run'}});
  });
  await input.fill('실패 뒤에도 보존할 원본');
  await input.press('Enter');
  await page.waitForFunction(() => teacherStreams.students.length === 1);
  await page.evaluate(() => teacherStreams.controllers['student-1'].close());
  await page.getByText('Request acceptance is unknown. You may explicitly resend the same request.', {exact:true}).waitFor();
  await blocked();
  snapshot = {
    request_id:await page.evaluate(() => teacherStreams.students[0].body.request_id),
    run_id:'student-1', turn_id:'turn-1', turn_index:1, teacher_message_id:10,
    status:'failed', retryable:true, error_code:'storage_error', partial_text:'원본 잠정 응답',
  };
  await page.getByRole('button', {name:'Check status again', exact:true}).click();
  await page.getByText('Could not save the student response. Incomplete response · excluded from conversation history', {exact:true}).waitFor();
  assert.equal(await input.inputValue(), '실패 뒤에도 보존할 원본');
  await chooseKo.click();
  assert.equal(await page.locator('#language-notice').innerText(), 'Save or clear your input before switching languages.');
  assert.deepEqual(lookupLanguages, ['en','en']);
  await page.getByRole('button', {name:'Retry student response', exact:true}).click();
  await page.waitForFunction(() => teacherStreams.students.length === 2);
  await page.evaluate(() => teacherStreams.complete('student', 2));
  await page.waitForFunction(() => !document.querySelector('#teacher-input').disabled);
  await input.fill('');
  // Confirmed completion and cleared input release protection.
  await Promise.all([page.waitForEvent('load'), chooseKo.click()]);
  assert.equal(await page.locator('html').getAttribute('lang'), 'ko');
  return {pageErrors:[]};
}
