import assert from 'node:assert/strict';
import {chromium} from 'playwright';

const base = `http://127.0.0.1:${process.argv[2]}`;
const password = 'BROWSER-PASSWORD-SENTINEL';
const key = 'sk-live-db-key';
const browser = await chromium.launch({headless: true});
const errors = [], probeRequests = [];
const admin = await browser.newContext();
const teacher = await browser.newContext();
const page = await admin.newPage();
const lesson = await teacher.newPage();

async function login(target, username) {
  await target.goto(`${base}/login`);
  await target.locator('[name=username]').fill(username);
  await target.locator('[name=password]').fill(password);
  await Promise.all([
    target.waitForURL(url => url.pathname !== '/login'),
    target.locator('button[type=submit]').click()
  ]);
}

async function post(target, path, body) {
  return target.evaluate(async ({path, body}) => {
    const token = document.cookie.split('; ').find(item => item.startsWith('csrftoken=')).split('=').slice(1).join('=');
    const response = await fetch(path, {method:'POST', headers:{'content-type':'application/json', 'x-csrf-token':decodeURIComponent(token)}, body:JSON.stringify(body)});
    return {status:response.status, body:await response.json()};
  }, {path, body});
}

async function noSecrets(target) {
  const content = await target.content();
  const storage = await target.evaluate(() => JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)]));
  for (const secret of [key, password]) assert(!(`${content}${storage}${target.url()}`).includes(secret));
}

try {
  for (const target of [page, lesson]) {
    target.setDefaultTimeout(15000);
    target.on('pageerror', error => errors.push(error.message));
    // All application requests stay on the fixture's real localhost server.
    await target.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
  }
  page.on('request', request => {
    if (/\/models\/1\/probes$/.test(request.url()) && request.method() === 'POST') probeRequests.push(request.postDataJSON());
  });
  await login(page, 'live_admin');
  await page.goto(`${base}/admin/ai`);
  await page.locator('[data-model="1"]').waitFor();
  await noSecrets(page);
  for (const provider of ['openai', 'anthropic', 'google']) {
    await page.locator(`[data-provider=${provider}]`).getByRole('button', {name:provider === 'openai' ? '키 교체' : '키 저장', exact:true}).click();
    await page.getByLabel('새 API 키', {exact:true}).fill(key);
    await page.getByLabel('현재 비밀번호', {exact:true}).fill(password);
    await page.getByRole('button', {name:'변경 제출', exact:true}).click();
    await page.getByRole('status').filter({hasText:'완료'}).waitFor();
    assert.equal(await page.getByLabel('새 API 키', {exact:true}).inputValue(), '');
    assert.equal(await page.getByLabel('현재 비밀번호', {exact:true}).inputValue(), '');
    await noSecrets(page);
  }
  let state = await (await page.request.get(`${base}/admin/ai/state`)).json();
  assert(state.providers.every(provider => provider.key_registered));
  assert.equal(state.models[0].verification_state.student.status, 'stale');
  await login(lesson, 'live_owner');
  assert.equal((await lesson.request.get(`${base}/admin/ai/state`)).status(), 403);
  await lesson.goto(`${base}/scenarios/1`);
  const blocked = await post(lesson, '/sessions/1/turns/stream', {request_id:crypto.randomUUID(), content:'Before role validation'});
  assert.equal(blocked.status, 503);
  assert.equal(blocked.body.detail.code, 'configuration_unavailable');

  const model = page.locator('[data-model="1"]');
  await model.getByRole('button', {name:'학생봇 시험', exact:true}).focus();
  await page.keyboard.press('Enter');
  await page.getByRole('button', {name:'시험 시작', exact:true}).click();
  await page.getByRole('status').filter({hasText:'시험을 시작'}).waitFor();
  for (let attempt = 0; attempt < 30; attempt++) {
    state = await (await page.request.get(`${base}/admin/ai/state`)).json();
    if (state.models[0].verification_state.student.status === 'succeeded') break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.equal(state.models[0].verification_state.student.status, 'succeeded');
  assert.equal(probeRequests.length, 1);
  const replay = await post(page, '/admin/ai/models/1/probes', probeRequests[0]);
  assert.equal(replay.status, 202);
  await page.reload();
  state = await (await page.request.get(`${base}/admin/ai/state`)).json();
  assert.equal(state.models[0].verification_state.student.status, 'succeeded');
  assert.equal(state.models[0].verification_state.mentor.status, 'unverified');
  const settings = state.settings;
  assert.equal((await post(page, '/admin/ai/settings/update', {
    expected_version:settings.settings_version, defaults:{student:1, mentor:null, analysis:null},
    limits:settings.limits, timeouts:settings.timeouts
  })).status, 200);
  await lesson.reload();
  const response = await lesson.evaluate(async () => {
    const token = decodeURIComponent(document.cookie.split('; ').find(item => item.startsWith('csrftoken=')).slice(10));
    const response = await fetch('/sessions/1/turns/stream', {method:'POST', headers:{'content-type':'application/json', 'x-csrf-token':token}, body:JSON.stringify({request_id:crypto.randomUUID(), content:'Browser question'})});
    return {status:response.status, text:await response.text()};
  });
  assert.equal(response.status, 200);
  assert(response.text.includes('event: output.completed') && response.text.includes('Saved body'));
  await lesson.reload();
  assert((await lesson.locator('body').innerText()).includes('Saved body'));
  const csv = await lesson.request.get(`${base}/sessions/1/export.csv`);
  assert.equal(csv.status(), 200);
  assert((await csv.text()).includes('Saved body'));
  const usage = await page.request.get(`${base}/admin/api-usage`);
  assert.equal(usage.status(), 200);
  const html = await usage.text();
  assert(html.includes('id="unpriced-attempts">3</dd>'));
  for (const secret of [key, password]) assert(!(html + await csv.text()).includes(secret));
  for (const width of [1280, 390]) {
    await page.setViewportSize({width, height:900});
    await page.goto(`${base}/admin/ai`);
    await page.locator('[data-model="1"]').waitFor();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await noSecrets(page);
  }
  await noSecrets(lesson);
  assert.deepEqual(errors, []);
  console.log('PASS actual admin/teacher browser requests and secret isolation');
} finally {
  await browser.close();
}
