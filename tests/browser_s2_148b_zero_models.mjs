import assert from 'node:assert/strict';

export default async function checkZeroModelGuidance(page) {
  const base = new URL(page.url()).origin;
  const submissions = [];
  const message = 'COMMON_MODEL_REQUIRED';
  await page.route('**/admin/scenarios', async route => {
    submissions.push(route.request().postDataJSON());
    await route.fulfill({status:422, json:{detail:[
      {path:'student.resolved_model_config', code:'required', message}
    ]}});
  });
  for (const [language, notice, linkName, unverified, inactive] of [
    ['ko', '등록·검증된 모델이 필요합니다.', 'AI 연결·모델', '미검증', '비활성'],
    ['en', 'A registered and verified model is required.', 'AI connections and models', 'Unverified', 'Inactive']
  ]) {
    await page.context().addCookies([{name:'ui_lang', value:language, url:base}]);
    await page.goto(`${base}/fixtures/s2/editor?new=1&ready=0`);
    const common = page.locator('#common-model');
    const status = page.locator('#common-model-status');
    const link = status.getByRole('link', {name:linkName, exact:true});
    await link.waitFor({timeout:3000});
    assert((await status.innerText()).includes(notice));
    assert.equal(await link.getAttribute('href'), '/admin/ai');
    assert((await common.getAttribute('aria-describedby')).split(' ').includes('common-model-status'));
    assert.equal(await common.locator('option').count(), 1, 'only the placeholder remains');
    assert.equal(await common.inputValue(), '');
    for (const colorScheme of ['light', 'dark']) {
      await page.emulateMedia({colorScheme});
      for (const width of [390, 1440]) {
        await page.setViewportSize({width, height:900});
        assert(await link.isVisible(), `${language}/${colorScheme}/${width}: guidance link visible`);
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no page overflow');
      }
    }
    await page.locator('#save-draft').click();
    await page.locator('#editor-errors').waitFor();
    assert.equal(submissions.at(-1).action, 'save_draft');
    for (const role of ['student', 'mentor', 'analysis']) {
      assert.equal(submissions.at(-1).config[role].resolved_model_config, null, 'no model silently supplied');
    }
    assert.equal(await common.getAttribute('aria-invalid'), 'true');
    await page.getByRole('button', {name:message, exact:true}).click();
    assert(await common.evaluate(el => el === document.activeElement));
    assert(await link.isVisible(), 'guidance survives rejected save');
    await page.goto(`${base}/fixtures/s2/editor`);
    await common.locator('option[value="4"]').waitFor({state:'attached', timeout:3000});
    assert.equal(await status.getByRole('link', {name:linkName, exact:true}).count(), 0, 'registered models suppress empty-state guidance');
    for (const [id, reason] of [['4', unverified], ['10', inactive]]) {
      const option = common.locator(`option[value="${id}"]`);
      assert(await option.evaluate(el => el.disabled));
      assert((await option.innerText()).includes(reason), reason);
    }
  }
  assert.equal(submissions.length, 2, 'one explicit save attempt per language');
  return {pageErrors:[]};
}
