import assert from 'node:assert/strict';

export default async function checkCommonModel(page) {
  await page.goto(new URL('/fixtures/s2/editor', page.url()).href);
  const common = page.getByLabel('시나리오 공통 모델', {exact:true});
  await common.waitFor({timeout:3000});
  await common.selectOption('2');
  let submitted;
  await page.route('**/admin/scenarios/1/update', route => {
    submitted = route.request().postDataJSON();
    return route.fulfill({status:409, json:{detail:{current_version:2}}});
  });
  await page.getByLabel('제목', {exact:true}).fill('Keep this title');
  await page.getByRole('button', {name:'3. 학생봇', exact:true}).click();
  await page.getByLabel('학생봇 최대 출력 토큰').fill('2049');
  await page.getByRole('button', {name:'초안으로 저장', exact:true}).click();
  await page.locator('#editor-conflict').waitFor();
  assert.equal(submitted.title, 'Keep this title');
  assert.equal(submitted.expected_version, 1);
  const active = ['student','analysis', ...(submitted.config.mentor.mode === 'off' ? [] : ['mentor'])];
  assert(active.every(role => submitted.config[role].resolved_model_config.model_config_id === 2));
  assert.equal(submitted.config.student.resolved_model_config.options.max_output_tokens, 2049);
  assert.equal(submitted.config.analysis.resolved_model_config.options.max_output_tokens, 2048);
  return {pageErrors:[]};
}
