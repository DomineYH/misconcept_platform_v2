import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

export default async function checkSnapshots(page, base) {
  const payload = JSON.parse(await readFile(new URL('./fixtures/s2_draft.json', import.meta.url), 'utf8'));
  const selection = {model_config_id:2, provider_connection_id:1, provider:'openai', model_id:'gpt-5.2',
    options:{max_output_tokens:1600, reasoning:{effort:'none'}, temperature:0.7}};
  payload.title = 'Frozen lesson title';
  payload.groups = [1];
  payload.action = 'publish';
  payload.config.problem = {public_text:'Public {x} <script>window.leaked=true</script>', learning_objective:'Frozen objective'};
  Object.assign(payload.config.student, {name:'Frozen student', public_profile:'Public introduction',
    internal_profile:'PRIVATE STUDENT SENTINEL', misconception:'PRIVATE MISCONCEPTION', behavior_instruction:'PRIVATE INSTRUCTION', resolved_model_config:selection});
  Object.assign(payload.config.analysis, {context:'PRIVATE ANALYSIS', expected_understanding:'PRIVATE ANSWER', instruction:'PRIVATE ANALYSIS INSTRUCTION', resolved_model_config:selection});
  const csrf = () => page.context().cookies(base).then(cookies => cookies.find(cookie => cookie.name === 'csrftoken').value);
  const post = async (path, body) => page.request.post(base + path, {data:body, headers:{'x-csrf-token':await csrf()}});
  const created = await post('/admin/scenarios', payload);
  assert.equal(created.status(), 201, await created.text());
  const sid = (await created.json()).id;
  const login = async username => {
    await page.goto(base + '/login');
    await page.locator('[name=username]').fill(username);
    await page.locator('[name=password]').fill('s2-browser-password');
    await Promise.all([page.waitForURL(url => url.pathname !== '/login'), page.locator('button[type=submit]').click()]);
  };
  await login('draft_teacher');
  const started = await post('/sessions', {scenario_id:sid});
  assert.equal(started.status(), 201);
  const sessionId = (await started.json()).id;
  await page.goto(`${base}/scenarios/${sid}`);
  for (const width of [1280, 390]) {
    await page.setViewportSize({width, height:900});
    await page.reload();
    assert.equal(await page.locator('.scenario-header h1').innerText(), 'Frozen lesson title');
    if (width === 390) await page.locator('[data-panel=scenario]').click();
    await page.locator('[data-tab=profile]').click();
    assert.equal((await page.locator('#tab-profile').innerText()).trim(), 'Public introduction');
    await page.locator('[data-tab=situation]').click();
    assert((await page.locator('#tab-situation').innerText()).includes('Frozen objective'));
    assert.equal(await page.locator('#tab-situation script').count(), 0);
    assert.equal(await page.evaluate(() => window.leaked), undefined);
    for (const privateText of ['PRIVATE STUDENT SENTINEL','PRIVATE MISCONCEPTION','PRIVATE ANSWER','max_output_tokens','resolved_model_config','config_snapshot_json']) {
      assert(!(await page.content()).includes(privateText), privateText);
    }
    const config = await page.locator('#chat-config').textContent().then(JSON.parse);
    assert.equal(config.sessionId, sessionId);
    assert.equal(config.mentorEnabled, false);
  }
  await login('draft_admin');
  payload.expected_version = 1;
  payload.title = 'Changed title';
  payload.config.student.name = 'Changed student';
  payload.config.problem.public_text = 'Changed problem';
  assert.equal((await post(`/admin/scenarios/${sid}/update`, payload)).status(), 200);
  await login('draft_teacher');
  await page.goto(`${base}/scenarios/${sid}`);
  assert.equal(await page.locator('.scenario-header h1').innerText(), 'Frozen lesson title');
  assert.equal((JSON.parse(await page.locator('#chat-config').textContent())).studentName, 'Frozen student');
  assert(!(await page.content()).includes('Changed problem'));
  console.log('PASS: native lesson snapshots remain private and frozen on desktop/mobile');
}
