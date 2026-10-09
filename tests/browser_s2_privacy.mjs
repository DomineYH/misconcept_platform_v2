import assert from 'node:assert/strict';

export default async function checkScenarioPublicPreview(page) {
  const base = new URL(page.url()).origin;
  const literal = '{{7*7}} {student.name} {"n":1} <script>window.xss=1</script><img src=x onerror="window.xss=1">';
  await page.goto(`${base}/fixtures/s2/editor`);
  await page.getByRole('button', {name:'2. 문제 상황', exact:true}).click();
  await page.getByLabel('공개 문제 상황').fill(literal);
  assert.equal(await page.locator('[data-preview="problem.public_text"]').innerText(), literal);
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
