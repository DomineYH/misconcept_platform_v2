import assert from 'node:assert/strict';
import {comparisonFixture} from './s5_browser_fixtures.mjs';

export default async function checkUnsavedComparisonSnapshot(page) {
  page.setDefaultTimeout(3000);
  const base = new URL(page.url()).origin;
  const fixture = await comparisonFixture(page,['completed','completed','completed']);
  await page.context().addCookies([{name:'csrftoken',value:'comparison-csrf',url:base}]);
  await page.goto(`${base}/fixtures/s2/editor?new=1`);
  await page.getByLabel('제목',{exact:true}).fill('');
  await page.getByRole('button',{name:'2. 문제 상황',exact:true}).click();
  await page.getByLabel('공개 문제 상황').fill('저장하지 않은 문제 <script>window.s5xss=1</script>');
  await page.getByRole('button',{name:'모델 비교 열기',exact:true}).click();
  assert.match(await page.locator('#scenario-comparison').innerText(), /제목 없는 초안.*새 작성/);
  await page.getByLabel('학생 현재 질문').fill('입력을 그대로 보낼까요?');
  const submitted = page.waitForRequest(request => request.method() === 'POST' && new URL(request.url()).pathname === '/admin/comparisons');
  await page.getByRole('button',{name:'비교 실행',exact:true}).click();
  const request = await submitted;
  await page.getByRole('article',{name:'패널 1',exact:true}).getByText('완료',{exact:true}).waitFor();
  const snapshot = fixture.requests[0].source_snapshot;
  assert.equal(snapshot.scenario_id,null);
  assert.equal(snapshot.source_version,null);
  assert.equal(snapshot.title,'', 'placeholder is display-only');
  assert.equal(snapshot.config.problem.public_text,'저장하지 않은 문제 <script>window.s5xss=1</script>');
  assert.equal(snapshot.config.analysis.resolved_model_config,null,'student comparison needs no analysis model');
  assert.equal(request.headers()['x-csrf-token'],'comparison-csrf');
  assert.deepEqual(fixture.unexpected,[]);
  assert.equal(await page.evaluate(() => window.s5xss),undefined);
  await page.getByRole('button',{name:'비교 닫기',exact:true}).click();
  assert.equal(await page.getByLabel('공개 문제 상황').inputValue(),snapshot.config.problem.public_text);
  return {pageErrors:[]};
}
