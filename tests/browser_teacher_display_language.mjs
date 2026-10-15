import assert from 'node:assert/strict';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.setViewportSize({width:1280, height:900});
  await page.context().addCookies([{name:'ui_lang', value:'en', url:base}]);
  await page.goto(`${base}/chat?stream&mentor=1`);
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  assert.equal(await page.locator('#end-session-btn').innerText(), 'End conversation and view analysis');
  const input = page.locator('#teacher-input');
  await input.fill('원본 작성 중');
  await page.locator('[data-ui-language="ko"]').click();
  assert.equal(await page.locator('html').getAttribute('lang'), 'en');
  assert.equal(await input.inputValue(), '원본 작성 중');
  assert.equal(await page.locator('#language-notice').innerText(), 'Save or clear your input before switching languages.');
  await input.fill('');
  await Promise.all([
    page.waitForEvent('load'),
    page.locator('[data-ui-language="ko"]').click(),
  ]);
  assert.equal(await page.locator('html').getAttribute('lang'), 'ko');
  return {pageErrors: []};
}
