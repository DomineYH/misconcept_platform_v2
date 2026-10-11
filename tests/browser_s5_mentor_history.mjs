import assert from 'node:assert/strict';
import {comparisonFixture, openComparison} from './s5_browser_fixtures.mjs';

export default async function checkMentorHistoryAttempts(page) {
  page.setDefaultTimeout(3000);
  const fixture = await comparisonFixture(page, ['completed','completed','completed']);
  await openComparison(page);
  await page.getByRole('button',{name:'완료 쌍 추가',exact:true}).click();
  await page.getByLabel('이력 1 교사').fill('왜?');
  await page.getByLabel('이력 1 학생').fill('분모가 커요.');
  await page.getByLabel('비교 대상').selectOption('mentor');
  await page.getByRole('button',{name:'비교 실행',exact:true}).click();
  await page.getByRole('article',{name:'패널 1',exact:true}).getByText('멘토 미개입',{exact:true}).waitFor();
  fixture.current().panels[0].attempts = [{status:'completed', result:{should_intervene:true,coaching:'이전 코칭 <img src=x onerror="window.s5xss=1">'},timing:{total_ms:150},usage:{cost:null}}];
  await page.getByRole('button',{name:'비교 이력',exact:true}).click();
  await page.getByRole('button',{name:'과거 비교 상세',exact:true}).click();
  const detail = page.getByRole('region',{name:'비교 이력 상세',exact:true});
  await detail.locator('summary').getByText('이전 시도 1 · 완료',{exact:true}).click();
  await detail.getByText('이전 코칭 <img src=x onerror="window.s5xss=1">',{exact:true}).waitFor();
  assert.equal(await detail.locator('img').count(),0);
  assert.equal(await page.evaluate(() => window.s5xss),undefined);
  return {pageErrors:[]};
}
