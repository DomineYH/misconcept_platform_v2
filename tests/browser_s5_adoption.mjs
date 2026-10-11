import assert from 'node:assert/strict';
import {comparisonFixture, openComparison} from './s5_browser_fixtures.mjs';

export default async function checkAdoptionLineage(page) {
  page.setDefaultTimeout(3000);
  const fixture = await comparisonFixture(page, ['completed','completed','failed']);
  await openComparison(page);
  await page.getByRole('button', {name:'비교 실행',exact:true}).click();
  const first = page.getByRole('article', {name:'패널 1',exact:true});
  await first.getByText('완료', {exact:true}).waitFor();
  const frozen = structuredClone(fixture.current());
  await first.getByRole('button', {name:'학생 응답 채택',exact:true}).click();
  await first.getByRole('button', {name:'학생 응답 채택',exact:true}).click();
  const second = page.getByRole('article', {name:'패널 2',exact:true});
  await second.getByRole('button', {name:'학생 응답 채택',exact:true}).click();
  assert.equal(await page.locator('#comparison-history-input fieldset').count(), 1);
  assert.equal(await page.getByLabel('이력 1 학생').inputValue(), frozen.panels[1].text);
  assert.deepEqual(fixture.current(), frozen, 'adoption only edits the next canonical history');
  await page.getByLabel('비교 대상').selectOption('mentor');
  await page.getByRole('button', {name:'비교 실행',exact:true}).click();
  await first.getByText('멘토 미개입', {exact:true}).waitFor();
  assert.equal(fixture.requests[1].parent_run_id, frozen.comparison_run_id);
  assert.deepEqual(fixture.requests[1].canonical_context, [
    {role:'teacher',content:'같은 전체에서 비교하면?'},
    {role:'student',content:frozen.panels[1].text},
  ]);
  assert.equal(await page.getByRole('button', {name:'학생 응답 채택',exact:true}).count(), 0);
  assert.deepEqual(fixture.unexpected, []);
  return {pageErrors:[]};
}
