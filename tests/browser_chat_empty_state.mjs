import assert from 'node:assert/strict';
import {colorMath} from './uiux_color_math.mjs';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.addInitScript(() => {
    const original = window.fetch;
    const fixture = window.emptyStreamFixture = {posts:0};
    window.fetch = async (url, options = {}) => {
      if (!url.endsWith('/turns/stream')) return original(url, options);
      fixture.posts++;
      fixture.request = JSON.parse(options.body);
      if (fixture.contextLimit) return Response.json({detail:{code:'context_limit'}}, {status:422});
      return new Response(new ReadableStream({start(controller) {
        fixture.controller = controller;
        fixture.emit('run.accepted', {request_id:fixture.request.request_id,
          teacher_message_id:1, turn_index:1, status:'running'});
      }}), {headers:{'Content-Type':'text/event-stream'}});
    };
    fixture.emit = (type, data) => fixture.controller.enqueue(new TextEncoder().encode(
      `event: ${type}\ndata: ${JSON.stringify({run_id:'empty-run', turn_id:'empty-turn', ...data})}\n\n`
    ));
  });
  const hint = page.locator('#chat-empty-state');
  const input = page.locator('#teacher-input');
  const selectedTab = async id => {
    assert.equal(await page.locator('.info-tab.active').getAttribute('id'), `info-tab-${id}`);
    assert.equal(await page.locator('.info-tab[aria-selected="true"]').getAttribute('id'), `info-tab-${id}`);
    assert.equal(await page.locator('.info-tab[tabindex="0"]').getAttribute('id'), `info-tab-${id}`);
    assert.equal(await page.locator('.tab-pane.active').getAttribute('id'), `tab-${id}`);
  };
  for (const language of ['ko','en']) {
    await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
    for (const theme of ['light','dark']) {
      await page.emulateMedia({colorScheme:theme});
      for (const width of [390,1440]) {
        await page.setViewportSize({width,height:900});
        const name = '공개 학생 이름 '.repeat(8).trim();
        await page.goto(`${base}/chat?chat_student=${encodeURIComponent(name)}`);
        await hint.waitFor({state:'visible'});
        assert.equal(await page.locator('#chat-title').innerText(), language === 'ko'
          ? `${name}와의 대화` : `Conversation with ${name}`);
        assert.equal(await hint.innerText(), language === 'ko'
          ? '문제 상황을 확인하고 학생에게 첫 질문을 보내세요.'
          : 'Read the problem statement and send the student your first question.');
        assert.equal(await hint.evaluate(el => Boolean(el.closest('[aria-live], [role="status"], [role="alert"]'))), false);
        const contrast = await hint.evaluate(new Function('el', `
          const {rgba,background,contrast} = (${colorMath})();
          return contrast(rgba(getComputedStyle(el).color),background(el));
        `));
        assert(contrast >= 4.5, `${language}/${theme}/${width}: readable hint`);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        const heading = await page.locator('#chat-title').boundingBox();
        assert(heading.x >= 0 && heading.x + heading.width <= width, 'heading wraps within chat panel');
        await selectedTab('situation');
        assert.equal(await page.locator('#mobile-tab-chat').getAttribute('aria-selected'), 'true');
      }
    }
  }
  await page.context().addCookies([{name:'ui_lang',value:'ko',url:base}]);
  await page.setViewportSize({width:1440,height:900});
  for (const [query, visible] of [
    ['mentor_manual=1', false],
    ['mentor_manual=1&chat_greeting=%20', true],
    ['mentor_manual=1&chat_greeting=none&mentor_history=1', false],
    ['chat_greeting=none&mentor_history=1', false],
    ['ended=1', false],
  ]) {
    await page.goto(`${base}/chat?${query}`);
    assert.equal(await hint.isVisible(), visible, query);
  }
  await page.goto(`${base}/chat?chat_student=none`);
  assert.equal(await page.locator('#chat-title').innerText(), '학생봇와의 대화');
  await page.goto(`${base}/chat?chat_student=%20`);
  assert.equal(await page.locator('#chat-title').innerText(), '학생봇와의 대화');
  await page.goto(`${base}/chat?chat_student=${encodeURIComponent('<script>public name</script>')}`);
  assert.equal(await page.locator('#chat-title').innerText(), '<script>public name</script>와의 대화');
  assert.equal(await page.locator('#chat-title script').count(), 0);

  await page.locator('#info-tab-situation').focus();
  for (const [key, id] of [['Home','guide'], ['ArrowRight','profile'], ['End','situation'], ['ArrowLeft','profile']]) {
    await page.keyboard.press(key);
    await selectedTab(id);
    assert.equal(await page.locator(`#info-tab-${id}`).evaluate(el => el === document.activeElement), true);
  }
  await Promise.all([page.waitForEvent('load'), page.locator('[data-ui-language="en"]').click()]);
  await selectedTab('profile');
  assert.equal(await page.locator('html').getAttribute('lang'), 'en');
  await input.fill('Preserved draft');
  await input.focus();
  await page.evaluate(() => document.querySelector('[data-ui-language="ko"]').click());
  assert.equal(await input.inputValue(), 'Preserved draft');
  assert.equal(await input.evaluate(el => el === document.activeElement), true);
  await selectedTab('profile');
  assert.equal(await page.locator('html').getAttribute('lang'), 'en', 'draft still guards language changes');

  await input.press('Enter');
  await page.locator('.student-run-status').waitFor();
  assert.equal(await hint.isVisible(), false, 'first optimistic message hides guidance');
  await page.evaluate(() => emptyStreamFixture.emit('output.delta', {text:'Provisional answer'}));
  await page.getByText('Provisional answer', {exact:true}).waitFor();
  assert.equal(await hint.isVisible(), false, 'stream delta never re-announces guidance');
  await selectedTab('profile');
  await page.evaluate(() => {
    emptyStreamFixture.emit('run.failed', {status:'failed', code:'provider_error', retryable:true});
    emptyStreamFixture.controller.close();
  });
  await page.getByRole('button', {name:'Retry student response',exact:true}).waitFor();
  assert.equal(await hint.isVisible(), false, 'failure takes precedence');
  assert.equal(await input.inputValue(), 'Preserved draft');
  let snapshot;
  await page.route('**/runs/empty-run', route => route.fulfill({json:snapshot}));
  snapshot = {
    request_id:await page.evaluate(() => emptyStreamFixture.request.request_id),
    run_id:'empty-run', turn_id:'empty-turn', turn_index:1, teacher_message_id:1,
    status:'completed', result_kind:'message',
    message:{id:2,role:'student',content:'Recovered saved answer'},
  };
  await page.reload();
  await page.getByText('Recovered saved answer', {exact:true}).waitFor();
  assert.equal(await hint.isVisible(), false, 'restored run suppresses initial guidance');
  await selectedTab('profile');

  await page.evaluate(() => sessionStorage.clear());
  await page.goto(`${base}/chat`);
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  await selectedTab('situation');
  await page.evaluate(() => { emptyStreamFixture.contextLimit = true; });
  await input.fill('Question rejected before acceptance');
  await input.press('Enter');
  await page.waitForFunction(() => document.querySelectorAll('.message').length === 0 && document.querySelector('.student-run-status'));
  assert.equal(await hint.isVisible(), false, 'standalone context failure takes precedence without messages');
  assert.equal(await input.inputValue(), 'Question rejected before acceptance');
  assert.equal(await input.evaluate(el => el === document.activeElement), true);

  await page.evaluate(() => sessionStorage.clear());
  await page.goto(`${base}/chat?uiux122_long=1`);
  await input.fill('Draft while reading history');
  await input.focus();
  const scroll = await page.locator('#messages-container').evaluate(el => {
    el.style.scrollBehavior = 'auto';
    el.scrollTop = 100;
    return el.scrollTop;
  });
  await page.locator('#info-tab-profile').evaluate(el => el.click());
  assert.equal(await input.inputValue(), 'Draft while reading history');
  assert.equal(await input.evaluate(el => el === document.activeElement), true);
  assert.equal(await page.locator('#messages-container').evaluate(el => el.scrollTop), scroll);
  await selectedTab('profile');
  return {checks:['localized heading and empty guidance at both widths/themes/languages',
    'visible greeting/history and run statuses take precedence', 'keyboard and language reload preserve selected information tab',
    'draft, focus and history scroll survive information-tab updates'], pageErrors:[]};
}
