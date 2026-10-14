import assert from 'node:assert/strict';
import {comparisonFixture, openComparison} from './s5_browser_fixtures.mjs';

export default async function checkRecovery(page) {
  page.setDefaultTimeout(15000);
  const fixture = await comparisonFixture(page, ['running','running','running']);
  let reads = 0;
  page.on('request', request => {
    if (request.method() === 'GET' && new URL(request.url()).pathname === '/admin/comparisons/comparison-1') reads++;
  });
  await openComparison(page);
  await page.getByRole('button',{name:'비교 실행',exact:true}).click();
  await page.getByText('복구 조회가 끝났습니다. 상태 새로고침 또는 중지를 사용하세요.',{exact:true}).waitFor();
  assert(reads >= 9 && reads <= 11, `bounded one-second recovery reads: ${reads}`);
  const previousReads = reads;
  await page.waitForTimeout(1200);
  assert.equal(reads, previousReads, 'recovery polling stops after ten seconds');
  assert.equal(fixture.requests.length, 1, 'recovery never creates another attempt');
  for (const panel of fixture.current().panels) panel.status = 'interrupted';
  await page.reload();
  await page.getByRole('article',{name:'패널 1',exact:true}).getByText('중단',{exact:true}).waitFor();
  assert.equal(reads, previousReads + 1, 'reload recovers saved state with GET');
  assert.equal(await page.getByLabel('학생 현재 질문').inputValue(),'같은 전체에서 비교하면?');
  assert.equal(fixture.requests.length, 1);
  await page.getByRole('article',{name:'패널 2',exact:true}).getByRole('button',{name:'패널 재시도',exact:true}).click();
  await page.getByRole('article',{name:'패널 2',exact:true}).getByText('완료',{exact:true}).waitFor();
  // Other panels are terminal, so a successful retry causes no recovery polling.
  await page.waitForTimeout(1200);
  assert.equal(reads, previousReads + 1);
  assert.equal(fixture.requests.length, 2);
  await page.route('**/admin/comparisons/comparison-1', route => route.fulfill({status:404,json:{detail:{code:'comparison_not_found'}}}));
  await page.getByRole('button',{name:'상태 새로고침',exact:true}).click();
  await page.getByText('비교를 찾거나 열 수 없습니다. 보존기간은 생성 후 30일입니다.',{exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'패널 재시도',exact:true}).count(),0);
  assert(await page.getByRole('button',{name:'비교 실행',exact:true}).isEnabled());
  assert.deepEqual(fixture.unexpected, []);
  return {pageErrors:[]};
}
