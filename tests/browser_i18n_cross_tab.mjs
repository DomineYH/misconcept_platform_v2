import assert from 'node:assert/strict';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.setViewportSize({width:1280,height:900});
  await page.addInitScript(() => {
    const original = window.fetch;
    window.fetch = async (url, options = {}) => {
      if (!String(url).endsWith('/turns/stream')) return original(url, options);
      const body = JSON.parse(options.body);
      window.streamLanguage = new Headers(options.headers).get('X-UI-Language');
      return new Response(new ReadableStream({start(controller) {
        window.acceptanceStream = controller;
        controller.enqueue(new TextEncoder().encode(`event: run.accepted\ndata: ${JSON.stringify({
          request_id:body.request_id,run_id:'acceptance',turn_id:'turn-1',turn_index:1,
          teacher_message_id:10,status:'running'
        })}\n\n`));
      }}), {headers:{'Content-Type':'text/event-stream'}});
    };
  });
  const other = await page.context().newPage();
  try {
    for (const language of ['ko','en']) {
      const next = language === 'ko' ? 'en' : 'ko';
      for (const [path, selector] of [
        ['/login','#username'], ['/chat?stream','#teacher-input'],
        ['/fixtures/uiux/admin/groups','#new-name'],
      ]) {
        await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
        await page.goto(base + path);
        if (path.includes('groups')) await page.locator('#open-create-modal').click();
        if (path.startsWith('/chat')) await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
        await page.locator(selector).fill('원문 입력');
        if (path.startsWith('/chat')) {
          await page.locator(selector).press('Enter');
          await page.locator('.student-run-status').waitFor();
          await page.waitForFunction(() => window.acceptanceStream);
        }
        await other.goto(`${base}/login`);
        await Promise.all([other.waitForEvent('load'), other.locator(`[data-ui-language="${next}"]`).click()]);
        assert.equal(await page.locator('html').getAttribute('lang'), language);
        if (path.startsWith('/chat')) {
          assert.equal(await page.evaluate(() => window.streamLanguage), language);
          assert.equal(await page.locator('.message-teacher .message-bubble').last().innerText(), '원문 입력');
          await page.evaluate(() => acceptanceStream.enqueue(new TextEncoder().encode(
            'event: output.delta\ndata: {"run_id":"acceptance","text":"원문 AI 출력"}\n\n'
          )));
          await page.getByText('원문 AI 출력',{exact:true}).waitFor();
          assert.equal(await page.locator('.student-run-status').innerText(), language === 'ko' ? '학생 생성 중 · 잠정 응답' : 'Student generating · provisional response');
        } else assert.equal(await page.locator(selector).inputValue(), '원문 입력');
        await page.locator(`[data-ui-language="${next}"]`).evaluate(button => button.click());
        assert.equal(await page.locator('#language-notice').innerText(), path.startsWith('/chat')
          ? language === 'ko' ? '작업이 끝난 뒤 전환하세요' : 'Wait until the task finishes before switching languages.'
          : language === 'ko' ? '저장하거나 입력을 비운 뒤 전환하세요' : 'Save or clear your input before switching languages.');
        const fragment = await page.evaluate(async () => {
          const target = document.createElement('div');
          document.body.append(target);
          await htmx.ajax('GET','/about/content?empty=1',{target,swap:'innerHTML'});
          const text = target.textContent;
          target.remove();
          return text;
        });
        assert.match(fragment, language === 'ko' ? /등록된 정보가 없습니다/ : /No information has been added/);
        // The shared About fetch fails after the cookie changed; its app notice stays local.
        if (path.includes('groups')) await page.keyboard.press('Escape');
        await page.route('**/about/content', route => {
          assert.equal(route.request().headers()['x-ui-language'], language);
          return route.fulfill({status:503,body:'PRIVATE ERROR'});
        });
        await page.locator('#open-about-modal').click();
        await page.locator('#about-load-error').waitFor({state:'visible'});
        assert.equal(await page.locator('#about-load-error').innerText(), language === 'ko'
          ? '소개를 불러올 수 없습니다. 소개를 다시 눌러 주세요.'
          : 'Could not load About. Select About to try again.');
        await page.unroute('**/about/content');
        if (path.startsWith('/chat')) {
          await page.evaluate(() => {
            acceptanceStream.enqueue(new TextEncoder().encode('event: output.completed\ndata: ' + JSON.stringify({
              run_id:'acceptance',turn_id:'turn-1',turn_index:1,status:'completed',
              message:{id:11,role:'student',content:'원문 AI 출력'}
            }) + '\n\n'));
            acceptanceStream.close();
          });
          await page.waitForFunction(() => !document.querySelector('#teacher-input').disabled);
          assert.equal(await page.locator('.student-run-status').innerText(), language === 'ko' ? '저장 완료' : 'Saved');
        }
        if (path.includes('groups')) await page.locator('#open-create-modal').click();
        await page.locator(selector).fill('');
        if (path.includes('groups')) await page.keyboard.press('Escape');
        await page.reload();
        assert.equal(await page.locator('html').getAttribute('lang'), next, 'safe reload adopts the sibling preference');
      }
    }
  } finally { await other.close(); }
  return {pageErrors:[]};
}
