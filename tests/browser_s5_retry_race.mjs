import assert from 'node:assert/strict';
import {comparisonFixture, openComparison} from './s5_browser_fixtures.mjs';

export default async function checkLateStop(page) {
  page.setDefaultTimeout(3000);
  const fixture = await comparisonFixture(page, ['completed','running','completed']);
  await openComparison(page);
  await page.getByRole('button',{name:'비교 실행',exact:true}).click();
  const p2 = page.getByRole('article',{name:'패널 2',exact:true});
  await p2.getByText('스트리밍',{exact:true}).waitFor();
  let release;
  const held = new Promise(resolve => { release = resolve; });
  let stopping;
  const entered = new Promise(resolve => { stopping = resolve; });
  await page.route('**/admin/comparisons/comparison-1/panels/2/stop', async route => {
    fixture.current().panels[1].status = 'cancelled';
    const stopped = structuredClone(fixture.current());
    stopping();
    await held;
    await route.fulfill({json:stopped});
  });
  await p2.getByRole('button',{name:'패널 중지',exact:true}).click();
  await entered;
  await page.getByRole('button',{name:'상태 새로고침',exact:true}).click();
  await p2.getByText('취소',{exact:true}).waitFor();
  await p2.getByRole('button',{name:'패널 재시도',exact:true}).click();
  await p2.getByText('완료',{exact:true}).waitFor();
  const rendered = await p2.innerText();
  const response = page.waitForResponse('**/admin/comparisons/comparison-1/panels/2/stop');
  release();
  await response;
  await page.waitForTimeout(100);
  assert.equal(await p2.innerText(), rendered, 'late old stop response cannot overwrite a completed retry');
  await p2.getByRole('button',{name:'학생 응답 채택',exact:true}).click();
  assert.equal(await page.getByLabel('이력 1 학생').inputValue(), fixture.current().panels[1].text, 'adoption uses the completed latest retry');
  assert.equal(fixture.requests.length, 2);
  return {pageErrors:[]};
}
