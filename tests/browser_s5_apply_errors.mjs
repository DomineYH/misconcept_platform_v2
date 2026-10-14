import assert from 'node:assert/strict';
import {comparisonFixture, openComparison} from './s5_browser_fixtures.mjs';

export default async function checkApplyErrors(page) {
  page.setDefaultTimeout(3000);
  const fixture = await comparisonFixture(page, ['completed','completed','failed']);
  await openComparison(page);
  await page.getByRole('button', {name:'비교 실행',exact:true}).click();
  const apply = page.getByRole('article', {name:'패널 1',exact:true}).getByRole('button', {name:'이 모델 적용',exact:true});
  for (const [status,message] of [[409,'비교가 진행 중입니다. 종료 후 적용하세요.'],[404,'비교 또는 패널을 찾거나 열 수 없습니다. 이력을 다시 확인하세요.'],[422,'현재 역할 검증·연결·옵션을 확인한 뒤 적용하세요.']]) {
    await page.route('**/admin/comparisons/comparison-1/panels/1/model', route => route.fulfill({status,json:{detail:{code:'PRIVATE_ERROR'}}}), {times:1});
    await apply.click();
    await page.getByText(message, {exact:true}).waitFor();
  }
  await page.getByRole('button', {name:'학생 응답 채택',exact:true}).first().click();
  await page.getByLabel('비교 대상').selectOption('mentor');
  await page.route('**/admin/comparisons', route => route.fulfill({status:404,json:{detail:{code:'scenario_not_found'}}}), {times:1});
  await page.getByRole('button', {name:'비교 실행',exact:true}).click();
  await page.getByText('원본 시나리오를 찾을 수 없습니다. 새 초안에서 비교하세요.', {exact:true}).waitFor();
  await page.getByRole('button', {name:'비교 실행',exact:true}).click();
  await page.getByText('비교가 종료되었습니다. 패널별 결과를 확인하세요.', {exact:true}).waitFor();
  assert.equal(fixture.requests.at(-1).parent_run_id,'comparison-1');
  await page.getByLabel('비교 대상').selectOption('student');
  await page.getByLabel('학생 현재 질문').fill('부모가 사라졌나요?');
  await page.route('**/admin/comparisons', route => route.fulfill({status:404,json:{detail:{code:'comparison_not_found'}}}), {times:1});
  await page.getByRole('button', {name:'비교 실행',exact:true}).click();
  await page.getByText('비교를 찾거나 열 수 없습니다. 보존기간은 생성 후 30일입니다.', {exact:true}).waitFor();
  await page.getByRole('button', {name:'비교 실행',exact:true}).click();
  await page.getByText('비교가 종료되었습니다. 패널별 결과를 확인하세요.', {exact:true}).waitFor();
  assert.equal(fixture.requests.at(-1).parent_run_id,null);
  return {pageErrors:[]};
}
