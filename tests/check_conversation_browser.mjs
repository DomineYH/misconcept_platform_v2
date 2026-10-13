import assert from 'node:assert/strict';
import {chromium} from 'playwright';

const base = `http://127.0.0.1:${process.argv[2]}`;
const browser = await chromium.launch({headless:true});
try {
  const page = await browser.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`${base}/login`);
  await page.locator('#username').fill('comparison_admin');
  await page.locator('#password').fill('comparison-password');
  await page.getByRole('button', {name:'로그인',exact:true}).click();
  await page.goto(`${base}/admin/scenarios`);
  await page.locator('.page-header-admin').getByRole('link', {name:'전체 모델 비교',exact:true}).click();
  assert.equal(page.url(), `${base}/admin/scenarios?compare=1`);
  await page.getByRole('link', {name:'모델 비교',exact:true}).last().click();
  await page.getByLabel('비교 모델 수', {exact:true}).selectOption('2');
  for (const panel of ['1','2']) {
    await page.getByLabel(`패널 ${panel} 공통 모델`, {exact:true}).selectOption('1');
    await page.getByLabel(`패널 ${panel} 사후 분석 최대 출력 토큰`, {exact:true}).fill('8192');
  }
  await page.getByRole('button', {name:'비교 시작',exact:true}).click();
  await page.getByLabel('공통 교사 질문', {exact:true}).fill('Why compare fractions?');
  await page.getByRole('button', {name:'질문 보내기',exact:true}).click();
  await page.getByRole('button', {name:'학생 응답 채택',exact:true}).first().waitFor();
  await page.getByRole('button', {name:'학생 응답 채택',exact:true}).first().click();
  await page.getByRole('button', {name:'멘토 도움',exact:true}).click();
  await page.getByText('Live coaching <img src=x onerror="window.s5xss=1">', {exact:true}).first().waitFor();
  await page.getByLabel('공통 교사 질문', {exact:true}).fill('And now?');
  await page.getByRole('button', {name:'질문 보내기',exact:true}).click();
  await page.getByRole('button', {name:'학생 응답 채택',exact:true}).last().waitFor();
  await page.getByRole('button', {name:'학생 응답 채택',exact:true}).last().click();
  await page.getByRole('button', {name:'비교 종료 및 분석',exact:true}).click();
  await page.getByText('정상 분석', {exact:true}).first().waitFor();
  const url = page.url();
  assert(url.includes('conversation_id='));
  await page.reload();
  await page.getByText('정상 분석', {exact:true}).first().waitFor();
  assert.equal(await page.locator('#conversation-dialogue [data-pair]').count(), 2);
  assert.equal(await page.evaluate(() => window.s5xss), undefined);
  assert(!await page.locator('#conversation-rounds').innerText().then(text => text.includes('PRIVATE MENTOR REASON')));
  await page.setViewportSize({width:390,height:844});
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  await page.getByRole('button', {name:'이 모델 적용',exact:true}).last().click();
  await page.getByText('비교 모델과 역할별 옵션을 적용했습니다. 별도 저장 또는 게시가 필요합니다.', {exact:true}).waitFor();
  await page.getByLabel('제목', {exact:true}).fill('Saved by explicit action');
  await page.getByRole('button', {name:'초안으로 저장',exact:true}).click();
  await page.getByText('초안을 저장했습니다.', {exact:true}).waitFor();
  assert.deepEqual(errors, []);
  console.log('PASS: saved conversation browser workflow');
} finally { await browser.close(); }
