import assert from 'node:assert/strict';

export default async function checkLegacyScenarioReview(page) {
  const base = new URL(page.url()).origin;
  await page.goto(`${base}/fixtures/s2/editor?legacy=blocked`);
  await page.getByRole('heading', {name:'기존 시나리오 변환 검토', exact:true}).waitFor({timeout:3000});
  assert((await page.locator('#conversion-review').innerText()).includes('원문'));
  assert((await page.locator('#conversion-review').innerText()).includes('변환값'));
  assert((await page.locator('#conversion-review').innerText()).includes('INTERNAL_STUDENT_SENTINEL'));
  assert.equal(await page.getByLabel('공개 학생 소개 (선택)').inputValue(), '', 'legacy internal profile is not published automatically');
  await page.getByLabel('이 버전의 변환 경고를 검토했습니다.').check();
  assert(await page.getByRole('button', {name:'게시', exact:true}).isDisabled(), 'blocking essentials cannot be waived');
  await page.getByRole('button', {name:'공개 문제 상황 보완 필요', exact:true}).click();
  assert(await page.getByLabel('공개 문제 상황').evaluate(el => el === document.activeElement));
  await page.goto(`${base}/fixtures/s2/editor?legacy=review`);
  assert(await page.getByRole('button', {name:'게시', exact:true}).isDisabled());
  await page.getByLabel('이 버전의 변환 경고를 검토했습니다.').check();
  assert(await page.getByRole('button', {name:'게시', exact:true}).isEnabled());
  const pending = page.waitForRequest('**/admin/scenarios/1/update');
  await page.route('**/admin/scenarios/1/update', route => route.fulfill({status:422, json:{detail:[{path:'student.resolved_model_config', code:'model_unavailable', message:'모델 사용 가능 상태를 확인하세요.'}]}}));
  await page.getByRole('button', {name:'게시', exact:true}).click();
  const request = (await pending).postDataJSON();
  assert.equal(request.expected_version, 1);
  assert.equal(request.acknowledge_review, true);
  await page.getByRole('button', {name:'모델 사용 가능 상태를 확인하세요.', exact:true}).click();
  assert(await page.getByLabel('학생봇 제공자 / 모델').evaluate(el => el === document.activeElement));
  assert(!(await page.locator('#public-preview').innerText()).includes('INTERNAL_STUDENT_SENTINEL'));
  return {pageErrors:[]};
}
