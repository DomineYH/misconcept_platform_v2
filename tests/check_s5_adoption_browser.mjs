import assert from 'node:assert/strict';
import {chromium} from 'playwright';

const base = `http://127.0.0.1:${process.argv[2]}`;
const scenarioId = Number(process.argv[3]);
const browser = await chromium.launch({headless:true});
const context = await browser.newContext();
const page = await context.newPage();
const errors = [];
page.on('pageerror', error => errors.push(error.message));
await page.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
const readScenario = async () => (await page.request.get(`${base}/admin/scenarios/${scenarioId}`)).json();
const readComparison = async id => (await page.request.get(`${base}/admin/comparisons/${id}`)).json();
const originalPath = `${base}/admin/scenarios/${scenarioId}/edit`;
try {
  await page.goto(`${base}/login`);
  await page.locator('[name=username]').fill('comparison_admin');
  await page.locator('[name=password]').fill('comparison-password');
  await Promise.all([page.waitForURL(url => url.pathname !== '/login'), page.locator('button[type=submit]').click()]);
  await page.goto(originalPath);
  const original = await readScenario();
  await page.getByLabel('제목', {exact:true}).fill('Adoption applied');
  await page.getByRole('button', {name:'모델 비교 열기',exact:true}).click();
  await page.getByRole('button', {name:'완료 쌍 추가',exact:true}).click();
  await page.getByLabel('이력 1 교사').fill('Earlier question');
  await page.getByLabel('이력 1 학생').fill('Earlier answer');
  await page.getByLabel('학생 현재 질문').fill('First adoption question');
  await page.getByLabel('패널 1 최대 출력 토큰').fill('1700');
  const panel = page.getByRole('article', {name:'패널 1',exact:true});
  const start = page.getByRole('button', {name:'비교 실행',exact:true});
  await start.click();
  await panel.getByText('완료', {exact:true}).waitFor();
  const parentId = new URL(page.url()).searchParams.get('comparison_id');
  const frozen = await readComparison(parentId);
  await panel.getByRole('button', {name:'학생 응답 채택',exact:true}).click();
  await panel.getByRole('button', {name:'학생 응답 채택',exact:true}).click();
  assert.equal(await page.locator('#comparison-history-input fieldset').count(), 2);
  assert.equal(await page.getByLabel('이력 2 학생').inputValue(), frozen.panels[0].text);
  await page.getByLabel('학생 현재 질문').fill('Next adoption question');
  await page.getByLabel('패널 1 최대 출력 토큰').fill('1701');
  await Promise.all([
    page.waitForURL(url => url.searchParams.get('comparison_id') !== parentId),
    start.click(),
  ]);
  await panel.getByText('완료', {exact:true}).waitFor();
  const childId = new URL(page.url()).searchParams.get('comparison_id');
  assert.notEqual(childId, parentId);
  const child = await readComparison(childId);
  assert.equal(child.parent_run_id, parentId);
  assert.deepEqual(child.canonical_context.map(({role,content}) => ({role,content})), [
    {role:'teacher',content:'Earlier question'}, {role:'student',content:'Earlier answer'},
    {role:'teacher',content:'First adoption question'}, {role:'student',content:frozen.panels[0].text},
  ]);
  assert.deepEqual(await readComparison(parentId), frozen);
  await panel.getByRole('button', {name:'이 모델 적용',exact:true}).click();
  await page.getByText('대상 역할의 모델·옵션을 미저장 폼에 적용했습니다. 별도 저장 또는 게시가 필요합니다.', {exact:true}).waitFor();
  assert.equal(await page.getByLabel('학생봇 최대 출력 토큰').inputValue(), '1701');
  assert(await page.getByLabel('학생봇 제공자 / 모델').evaluate(el => document.activeElement === el));
  assert.deepEqual(await readScenario(), original, 'adoption and application never save');
  await page.getByRole('button', {name:'초안으로 저장',exact:true}).click();
  await page.getByText('초안을 저장했습니다.', {exact:true}).waitFor();
  const saved = await readScenario();
  assert.equal(saved.title, 'Adoption applied');
  assert.equal(saved.config_version, original.config_version + 1);
  assert.deepEqual(saved.config.student.resolved_model_config, child.panels[0].model_config);
  assert.deepEqual(saved.config.mentor, original.config.mentor);
  assert.deepEqual(saved.config.analysis, original.config.analysis);
  const competing = await page.context().newPage();
  await competing.goto(originalPath);
  await competing.getByLabel('제목', {exact:true}).fill('Concurrent winner');
  await competing.getByRole('button', {name:'초안으로 저장',exact:true}).click();
  await competing.getByText('초안을 저장했습니다.', {exact:true}).waitFor();
  await page.getByLabel('학생봇 최대 출력 토큰').fill('1702');
  await page.getByRole('button', {name:'초안으로 저장',exact:true}).click();
  await page.locator('#editor-conflict').waitFor();
  assert.equal(await page.getByLabel('학생봇 최대 출력 토큰').inputValue(), '1702');
  assert.equal((await readScenario()).config.student.resolved_model_config.options.max_output_tokens, 1701);
  // An explicit current original form preserves the latest scenario, while results stay frozen.
  const past = await page.context().newPage();
  await past.goto(`${originalPath}?comparison_id=${parentId}`);
  await past.getByRole('article', {name:'패널 1',exact:true}).getByText('완료', {exact:true}).waitFor();
  assert.equal(await past.locator('#title').inputValue(), 'Concurrent winner');
  await past.getByRole('article', {name:'패널 1',exact:true}).getByRole('button', {name:'이 모델 적용',exact:true}).click();
  await past.getByText('대상 역할의 모델·옵션을 미저장 폼에 적용했습니다. 별도 저장 또는 게시가 필요합니다.', {exact:true}).waitFor();
  assert.equal(await past.getByLabel('학생봇 최대 출력 토큰').inputValue(), '1700');
  assert.equal((await readScenario()).config.student.resolved_model_config.options.max_output_tokens, 1701);
  const draft = await page.context().newPage();
  await draft.goto(`${base}/admin/scenarios/new?from_comparison=${parentId}`);
  await draft.getByRole('article', {name:'패널 1',exact:true}).getByText('완료', {exact:true}).waitFor();
  assert.equal(await draft.locator('#title').inputValue(), 'Adoption applied');
  assert.equal(await draft.locator('[id="student.resolved_model_config"]').inputValue(), '');
  await draft.getByRole('article', {name:'패널 1',exact:true}).getByRole('button', {name:'이 모델 적용',exact:true}).click();
  await draft.getByText('대상 역할의 모델·옵션을 미저장 폼에 적용했습니다. 별도 저장 또는 게시가 필요합니다.', {exact:true}).waitFor();
  assert.equal(await draft.getByLabel('학생봇 최대 출력 토큰').inputValue(), '1700');
  assert.deepEqual(errors, []);
  console.log('PASS: adoption child comparison, live model-copy, separate save and dirty 409');
} finally { await browser.close(); }
