import assert from 'node:assert/strict';
import {comparisonFixture, openComparison} from './s5_browser_fixtures.mjs';

export default async function checkExplicitPastEditor(page) {
  page.setDefaultTimeout(3000);
  const fixture = await comparisonFixture(page, ['completed','completed','completed']);
  await openComparison(page);
  await page.getByRole('button', {name:'비교 실행',exact:true}).click();
  await page.getByRole('article', {name:'패널 1',exact:true}).getByText('완료', {exact:true}).waitFor();
  fixture.current().source_snapshot.scenario_id = 2;
  await page.getByRole('button', {name:'비교 이력',exact:true}).click();
  await page.getByRole('button', {name:'과거 비교 상세',exact:true}).click();
  const detail = page.getByRole('region', {name:'비교 이력 상세',exact:true});
  await detail.waitFor();
  assert.equal(await detail.getByRole('button', {name:'이 모델 적용',exact:true}).count(), 0);
  assert.equal(await detail.getByRole('link', {name:'원본 시나리오 편집 폼 열기',exact:true}).getAttribute('href'), '/admin/scenarios/2/edit?comparison_id=comparison-1');
  assert.equal(await detail.getByRole('link', {name:'이 조건으로 새 초안 열기',exact:true}).getAttribute('href'), '/admin/scenarios/new?from_comparison=comparison-1');
  fixture.current().source_snapshot.scenario_id = null;
  await page.getByRole('button', {name:'과거 비교 상세',exact:true}).click();
  assert.equal(await detail.getByRole('button', {name:'이 모델 적용',exact:true}).count(), 0);
  assert(await detail.getByRole('link', {name:'이 조건으로 새 초안 열기',exact:true}).isVisible());
  assert.deepEqual(fixture.unexpected, []);
  return {pageErrors:[]};
}
