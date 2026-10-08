import assert from 'node:assert/strict';
import {state} from './browser_ai_connections.mjs';

export default async function checkAISettings(page) {
  page.setDefaultTimeout(8000);
  const base = new URL(page.url()).origin;
  const requests = [];
  const data = state();
  await page.route('**/admin/ai/state', route => route.fulfill({json:data}));
  await page.route('**/admin/ai/settings/update', async route => {
    requests.push(route.request().postDataJSON());
    await route.fulfill({json:{status:'saved'}});
  });
  for (const width of [1280, 390]) {
    await page.setViewportSize({width, height:900});
    await page.goto(`${base}/admin/ai`);
    await page.getByRole('button', {name:'작성 기본값·호출 설정 수정'}).click();
    const student = page.getByLabel('학생봇 작성 기본 모델', {exact:true});
    assert.equal(await student.inputValue(), '2', 'unavailable reference remains selected');
    assert(await student.locator('option[value="2"]').isDisabled(), 'unavailable default marked');
    assert.equal(await page.getByLabel('멘토 작성 기본 모델', {exact:true}).locator('option[value="1"]').count(), 0, 'different role success is not eligible');
    await student.selectOption('1');
    assert.equal(await page.getByLabel('현재 비밀번호').count(), 0, 'operational settings do not require reauthentication');
    await page.getByLabel('학생봇 첫 본문 제한(초)', {exact:true}).fill('181');
    await page.getByRole('button', {name:'설정 저장', exact:true}).click();
    await page.getByRole('alert').filter({hasText:'첫 본문'}).waitFor();
    assert.equal(requests.length, width === 1280 ? 0 : 1, 'invalid deadline rejected before submission');
    await page.getByLabel('학생봇 첫 본문 제한(초)', {exact:true}).fill('60');
    await page.getByLabel('전체 호출 한도', {exact:true}).fill('10');
    await page.getByRole('button', {name:'설정 저장', exact:true}).click();
    await page.getByRole('status').filter({hasText:'완료'}).waitFor();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'settings fit viewport');
  }
  assert.equal(requests.length, 2);
  assert.equal(requests[0].expected_version, 1);
  assert.deepEqual(requests[0].defaults, {student:1, mentor:null, analysis:null});
  assert.equal(requests[0].limits.total, 10);
  assert.equal(requests[0].timeouts.connect, 5);
  return {pageErrors:[]};
}
