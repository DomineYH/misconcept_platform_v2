import assert from 'node:assert/strict';
import {access, writeFile} from 'node:fs/promises';
import {chromium} from 'playwright';

const base = `http://127.0.0.1:${process.argv[2]}`;
const browser = await chromium.launch({headless:true});
const page = await browser.newPage({viewport:{width:390, height:900}});
const errors = [], requests = [];
page.setDefaultTimeout(15000);
page.on('pageerror', error => errors.push(error.message));
page.on('dialog', dialog => dialog.accept());
page.on('request', request => {
  if (request.url().endsWith('/mentor/stream') && request.method() === 'POST') requests.push({url:request.url(), body:request.postDataJSON()});
});
await page.addInitScript(() => {
  window.mentorWire = [];
  const originalFetch = window.fetch;
  window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    if (String(args[0]).endsWith('/mentor/stream') && response.headers.get('content-type')?.includes('text/event-stream')) {
      void response.clone().text().then(text => window.mentorWire.push(text)).catch(() => {});
    }
    return response;
  };
});
// Application requests use real localhost HTTP; only the upstream SDK is mocked.
await page.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());

try {
  await page.goto(`${base}/login`);
  await page.locator('[name=username]').fill('live_owner');
  await page.locator('[name=password]').fill('BROWSER-PASSWORD-SENTINEL');
  await Promise.all([page.waitForURL(url => url.pathname !== '/login'), page.locator('button[type=submit]').click()]);
  await page.goto(`${base}/scenarios/1`);
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  const input = page.locator('#teacher-input');
  const send = async text => {
    await input.fill(text);
    await input.press('Enter');
    await page.waitForFunction(() => !document.querySelector('#teacher-input').disabled);
  };
  await send('First original target');
  const first = page.locator('.mentor-slot').first();
  await first.locator('[role=status]').filter({hasText:'멘토 처리 중'}).waitFor();
  const turn = await first.getAttribute('data-turn-id');
  assert(await first.locator('.message-bubble').isHidden());
  assert(await page.locator('#end-session-btn').isEnabled());
  await send('Second independent student');
  assert.equal(await page.locator('.message-student[data-turn-id]').count(), 2);
  assert(await first.locator('.message-bubble').isHidden(), 'no mentor result before committed completion');
  await input.fill('Draft must retain focus');
  await input.evaluate(el => { el.focus(); el.setSelectionRange(2, 5); });
  const scroll = await page.evaluate(() => {
    const container = document.querySelector('#messages-container');
    container.style.scrollBehavior = 'auto';
    container.scrollTop = 0;
    return container.scrollTop;
  });
  await writeFile(process.argv[3], 'release');
  await first.getByText('Live late coaching', {exact:true}).waitFor();
  assert.equal(await first.getAttribute('data-turn-id'), turn);
  assert(await page.evaluate(({turn, scroll}) => {
    const slot = document.querySelector(`.mentor-slot[data-turn-id="${turn}"]`);
    const next = document.querySelectorAll('.message-teacher[data-turn-id]')[1];
    const input = document.querySelector('#teacher-input');
    return Boolean(slot.compareDocumentPosition(next) & Node.DOCUMENT_POSITION_FOLLOWING) &&
      input === document.activeElement && input.selectionStart === 2 && input.selectionEnd === 5 &&
      document.querySelector('#messages-container').scrollTop === scroll;
  }, {turn, scroll}), 'original target, focus, selection and scroll preserved');
  const completedRequest = requests.find(item => item.url.includes(turn));
  const replay = await page.evaluate(async ({url, body}) => {
    const token = decodeURIComponent(document.cookie.split('; ').find(item => item.startsWith('csrftoken=')).slice(10));
    const response = await fetch(url, {method:'POST', headers:{'content-type':'application/json', 'x-csrf-token':token}, body:JSON.stringify(body)});
    return response.json();
  }, completedRequest);
  assert.equal(replay.result_kind, 'message');
  assert.equal(replay.turn_id, turn);
  const status = await (await page.request.get(`${base}/runs/${replay.run_id}`)).json();
  assert.equal(status.message.content, 'Live late coaching');
  for (const path of ['/sessions/1/export.csv', '/sessions/1/messages/updates', '/sessions/1', '/scenarios/1']) {
    const response = await page.request.get(`${base}${path}`);
    assert.equal(response.status(), 200, path);
    assert(!(await response.text()).includes('PRIVATE-LIVE-REASON'), path);
  }
  assert(!JSON.stringify([status, replay]).includes('reason_summary'));
  await page.waitForFunction(() => window.mentorWire.length > 0);
  const sse = await page.evaluate(() => window.mentorWire[0]);
  assert.deepEqual([...sse.matchAll(/^event: (.+)$/gm)].map(match => match[1]), ['run.accepted', 'output.completed']);
  assert(!sse.includes('PRIVATE-LIVE-REASON') && !sse.includes('should_intervene'));
  assert(!(await page.content()).includes('PRIVATE-LIVE-REASON'));
  await page.locator('#request-mentor').click();
  await page.locator('.mentor-slot').last().locator('[role=status]').filter({hasText:'멘토 처리 중'}).waitFor();
  // Wait for the upstream call before ending the session.
  const startedPath = `${process.argv[3]}.started`;
  const deadline = Date.now() + 15000;
  while (true) {
    try { await access(startedPath); break; } catch {
      assert(Date.now() < deadline, 'second upstream mentor started');
      await new Promise(resolve => setTimeout(resolve, 20));
    }
  }
  await page.setViewportSize({width:1280, height:900});
  await page.locator('#end-session-btn').click();
  await page.waitForFunction(() => document.querySelector('#end-session-btn').dataset.state !== 'ending' && document.querySelector('#teacher-input').disabled);
  await page.locator('.mentor-slot').last().locator('.message-bubble').waitFor({state:'hidden'});
  assert.equal(await page.getByText('Live late coaching', {exact:true}).count(), 1);
  assert.deepEqual(errors, []);
  console.log('PASS actual S3 teacher browser mentor lifecycle');
} finally {
  await browser.close();
}
