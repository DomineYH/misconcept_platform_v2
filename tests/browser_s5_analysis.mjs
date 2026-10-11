import assert from 'node:assert/strict';
import {comparisonFixture, openComparison, hidden} from './s5_browser_fixtures.mjs';

export default async function checkAnalysisComparison(page) {
  page.setDefaultTimeout(3000);
  const fixture = await comparisonFixture(page, ['running','running','running']);
  await openComparison(page);
  await page.getByLabel('비교 대상').selectOption('analysis');
  await page.getByRole('button', {name:'비교 실행',exact:true}).click();
  const panels = [1,2,3].map(i => page.getByRole('article', {name:`패널 ${i}`,exact:true}));
  await panels[0].getByText('진행 중', {exact:true}).waitFor();
  assert(!(await page.locator('#scenario-comparison').innerText()).includes('스트리밍'));
  assert(!(await panels[0].innerText()).includes('검증된 비교 분석'));
  fixture.finish(['ok','degraded','failed']);
  await page.getByRole('button', {name:'상태 새로고침',exact:true}).click();
  await panels[0].getByRole('heading', {name:'검증된 비교 분석: 정상 분석',exact:true}).waitFor();
  assert(await panels[1].getByRole('heading', {name:'검증된 비교 분석: 부분 분석',exact:true}).isVisible());
  assert(!(await panels[2].innerText()).includes('검증된 비교 분석'));
  assert.match(await panels[1].innerText(), /분류 누락/);
  assert.match(await panels[0].innerText(), /첫 본문 N\/A/);
  assert.match(await page.locator('#scenario-comparison').innerText(), /교육 품질 승인을 대신하지/);
  const link = panels[0].getByRole('link', {name:/원문 \d+:/}).first();
  await link.click();
  assert(await panels[0].evaluate(el => el.contains(document.activeElement)));
  assert.match(await panels[0].innerText(), /원래 대화/);
  assert(!(await page.locator('#scenario-comparison').innerText()).includes(hidden));
  assert.deepEqual(fixture.unexpected, []);
  return {pageErrors:[]};
}
