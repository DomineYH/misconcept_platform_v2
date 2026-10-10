import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';

export default async function checkScenarioPublicPreview(page) {
  const base = new URL(page.url()).origin;
  const literal = '{{7*7}} {student.name} {"n":1} <script>window.xss=1</script><img src=x onerror="window.xss=1">';
  await page.goto(`${base}/fixtures/s2/editor`);
  const expected = JSON.parse(execFileSync('uv', ['run', '--frozen', 'python', '-c', `
import json
from src.api.schemas.scenario_config import ScenarioConfig
from src.services.lesson_snapshots import ScenarioContext, public_lesson
from tests.s2_screen_fixtures import editor_fixture
source = editor_fixture()
context = ScenarioContext(**{key: source[key] for key in ('title', 'subject', 'target_grade')})
config = ScenarioConfig.model_validate(source['config'])
expected = {}
for mode in ('manual', 'auto', 'off'):
    config.mentor.mode = mode
    expected[mode] = public_lesson(context, config)
print(json.dumps(expected))
`], {env:{...process.env, TESTING:'true'}, encoding:'utf8'}));
  await page.getByRole('button', {name:'4. 멘토', exact:true}).click();
  for (const mode of ['manual', 'auto', 'off']) {
    await page.getByLabel('멘토 사용 모드').selectOption(mode);
    const rendered = await page.locator('#public-preview [data-preview]').evaluateAll(elements => Object.fromEntries(elements.map(el => [el.dataset.preview, el.textContent])));
    assert.deepEqual(rendered, expected[mode], `${mode}: editor preview has exactly the public_lesson fields and values`);
  }
  await page.getByLabel('멘토 사용 모드').selectOption('manual');
  await page.getByRole('button', {name:'2. 문제 상황', exact:true}).click();
  await page.getByLabel('공개 문제 상황').fill(literal);
  assert.equal(await page.locator('[data-preview="problem_situation"]').innerText(), literal);
  await page.getByRole('button', {name:'3. 학생봇', exact:true}).click();
  await page.getByLabel('공개 학생 소개 (선택)').fill(literal);
  await page.getByLabel('내부 학생 프로필 (선택)').fill('INTERNAL_PRIVATE_PROFILE');
  await page.getByLabel('목표 오개념').fill('INTERNAL_PRIVATE_MISCONCEPTION');
  await page.getByLabel('학생봇 행동 지시').fill('INTERNAL_PRIVATE_INSTRUCTION');
  const preview = await page.locator('#public-preview').innerText();
  for (const privateValue of ['INTERNAL_PRIVATE', 'INTERNAL_MENTOR_SENTINEL', 'INTERNAL_RUBRIC_SENTINEL', 'gpt-5-mini']) assert(!preview.includes(privateValue));
  assert.equal(await page.locator('#public-preview script, #public-preview img, #public-preview iframe').count(), 0);
  assert.equal(await page.evaluate(() => window.xss), undefined);
  let submitted;
  await page.route('**/admin/scenarios/1/update', route => {
    submitted = route.request().postDataJSON();
    return route.fulfill({status:422, json:{detail:[{path:'analysis.context', code:'required', message:'에러 <img src=x onerror="window.xss=1">'}]}});
  });
  await page.getByRole('button', {name:'게시', exact:true}).click();
  await page.locator('#editor-errors').waitFor();
  assert.equal(submitted.config.problem.public_text, literal, 'no template interpretation');
  assert.equal(submitted.config.student.public_profile, literal);
  assert.equal(await page.locator('#editor-errors img').count(), 0);
  assert.equal(await page.evaluate(() => window.xss), undefined);
  assert(!(await page.content()).includes('variant='), 'no production mock selector');
  return {pageErrors:[]};
}
