import assert from 'node:assert/strict';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.route('**/admin/groups', route => route.request().method() === 'POST'
    ? route.fulfill({status:422,json:{detail:[{}]}}) : route.continue());
  for (const [language, expected] of [['en','Check your input.'], ['ko','입력을 확인하세요.']]) {
    await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
    await page.goto(`${base}/fixtures/uiux/admin/groups`);
    await page.locator('#open-create-modal').click();
    await page.locator('#new-name').fill('원문 그룹');
    await page.locator('#create-form button[type=submit]').click();
    await page.locator('.modal-error').waitFor();
    assert.equal(await page.locator('.modal-error').innerText(), expected);
    assert.equal(await page.locator('#new-name').inputValue(), '원문 그룹');
  }
  for (const [language, labels, modeLabel, levels] of [
    ['ko',['학생봇','멘토','사후 분석'],'멘토 사용 모드: 도움 버튼으로 확인',['지정하지 않음','높음','낮음']],
    ['en',['Student','Mentor','Post-session analysis'],'Mentor mode: Use the help button',['Unspecified','High','Low']],
  ]) {
    await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
    await page.goto(`${base}/fixtures/s2/editor`);
    await page.waitForFunction(() => document.querySelector('#execution-summary').textContent);
    const summary = await page.locator('#execution-summary').innerText();
    labels.forEach(label => assert(summary.includes(`${label}: openai / gpt-5-mini`), summary));
    assert(summary.includes(modeLabel),summary);
    const options = page.locator('select[data-field="analysis.rubric.0.level"] option');
    assert.deepEqual(await options.allTextContents(),levels);
    assert.deepEqual(await options.evaluateAll(options => options.map(option => option.value)),['','high','low']);
  }
  return {pageErrors:[]};
}
