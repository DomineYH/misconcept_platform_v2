import assert from 'node:assert/strict';

export default async function checkMentorPreviewLabels(page) {
  const base = new URL(page.url()).origin;
  const mentorName = '<img src=x onerror="window.xss=1"> {mentor}';
  let submitted;
  await page.route('**/admin/scenarios/1/update', route => {
    submitted = route.request().postDataJSON();
    return route.fulfill({status:200, json:{version:2, status:'draft'}});
  });
  for (const [language, modeTitle, mentorTitle, labels] of [
    ['ko', '멘토 사용 모드', '멘토', ['사용 안 함', '도움 버튼으로 확인', '완료 턴 뒤 자동 검사']],
    ['en', 'Mentor mode', 'Mentor', ['Disabled', 'Use the help button', 'Automatically check after completed turns']],
  ]) {
    await page.context().addCookies([{name:'ui_lang', value:language, url:base}]);
    await page.goto(`${base}/fixtures/s2/editor`);
    await page.locator('[data-step="3"]').click();
    const mode = page.locator('[id="mentor.mode"]');
    await page.locator('[id="mentor.name"]').fill(mentorName);
    assert.deepEqual(await mode.locator('option').allTextContents(), labels);
    assert.deepEqual(await mode.locator('option').evaluateAll(options => options.map(option => option.value)), ['off', 'manual', 'auto']);
    for (const [index, value] of ['off', 'manual', 'auto'].entries()) {
      await mode.selectOption(value);
      const modeLabel = `${modeTitle}: ${labels[index]}`;
      assert.equal(await page.locator('[data-preview="mentor_mode"]').innerText(), modeLabel);
      assert((await page.locator('#execution-summary').innerText()).includes(modeLabel));
      const name = page.locator('[data-preview="mentor_name"]');
      const greeting = page.locator('[data-preview="greeting_message"]');
      if (value === 'off') {
        assert(await name.isHidden());
        assert(await greeting.isHidden());
      } else {
        assert.equal(await name.innerText(), `${mentorTitle}: ${mentorName}`);
        assert(await greeting.isVisible());
      }
      const preview = await page.locator('#public-preview').innerText();
      assert(!/\b(off|manual|auto)\b/.test(preview));
      for (const privateValue of ['INTERNAL_', 'gpt-5-mini', 'max_output_tokens', '정답을 바로 알려 줄 때']) assert(!preview.includes(privateValue));
      assert.equal(await page.locator('#public-preview img, #public-preview script').count(), 0);
      assert.equal(await page.evaluate(() => window.xss), undefined);
      await page.locator('#save-draft').click();
      await page.waitForFunction(() => document.querySelector('#editor-status').dataset.state === 'success');
      assert.equal(submitted.config.mentor.mode, value);
      assert.equal(submitted.config.mentor.name, mentorName);
    }
  }
  return {pageErrors:[]};
}
