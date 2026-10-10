import assert from 'node:assert/strict';

export default async function checkSafeSharedProjection(page) {
  const origin = new URL(page.url()).origin;
  const payload = '<img src=x onerror="window.xssFired=true"><script>window.xssFired=true</script>';
  const projection = await (await page.request.get(`${origin}/fixtures/s4/result?state=partial&admin=1`)).json();
  const report = projection.accepted_report;
  projection.messages[2].content = payload;
  report.brief_feedback = [payload];
  report.misconception_findings[0].claim = payload;
  report.misconception_findings[0].evidence[0].quote = payload;
  report.strengths[0].reason = payload;
  report.improvements[0].alternative_question = payload;
  report.dialogue_coaching[0].note = payload;
  projection.raw_config = 'PRIVATE_CONFIG';
  report.criteria = 'PRIVATE_CRITERIA';
  projection.latest_run.raw_error = 'PRIVATE_SDK_ERROR';
  await page.addInitScript(() => { window.xssFired = false; });
  await page.route('**/fixtures/s4/result?**', route => route.fulfill({json: projection}));
  const modal = await (await page.request.get(`${origin}/fixtures/s4/modal?state=partial&admin=1`)).text();
  await page.route('**/admin/sessions/1/analysis_modal', route => route.fulfill({body: modal, contentType: 'text/html'}));
  for (const width of [1280, 390]) {
    await page.setViewportSize({width, height: 800});
    for (const surface of ['analysis', 'history']) {
      await page.goto(`${origin}/fixtures/s4/${surface}?state=partial&admin=1`);
      let opener;
      if (surface === 'history') {
        opener = page.locator('.view-analysis-btn');
        await opener.focus();
        await page.keyboard.press('Enter');
        await page.getByRole('dialog', {name: '세션 분석 결과'}).waitFor();
      }
      const result = page.locator('[data-analysis-result]');
      await result.getByRole('heading', {name: '채택된 보고서: 부분 분석'}).waitFor();
      if (surface === 'history') {
        const close = page.locator('#analysis-modal .close-modal-btn');
        await close.focus();
        await page.keyboard.press('Shift+Tab');
        assert(await page.locator('#analysis-modal').getByRole('link', {name: 'CSV 다운로드'}).evaluate(el => el === document.activeElement));
        await page.keyboard.press('Tab');
        assert(await close.evaluate(el => el === document.activeElement), 'keyboard focus stays inside the modal');
      }
      assert.equal(await result.locator('img,script').count(), 0);
      assert.equal(await page.evaluate(() => window.xssFired), false);
      assert((await result.innerText()).includes(payload), 'generated text remains literal');
      const html = await page.content();
      for (const secret of ['PRIVATE_CONFIG', 'PRIVATE_CRITERIA', 'PRIVATE_SDK_ERROR']) assert(!html.includes(secret));
      const evidence = result.getByRole('link', {name: `원문 103: ${payload}`});
      await evidence.click();
      const message = result.locator('[data-message-id="103"]');
      assert(await message.evaluate(el => el === document.activeElement));
      assert((await message.innerText()).includes(payload), 'raw transcript remains literal');
      if (surface === 'history') {
        await page.keyboard.press('Escape');
        assert(await opener.evaluate(el => el === document.activeElement));
        assert.equal(await result.count(), 0);
      } else assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    }
  }
  return {checks: ['same projection in page/history modal, literal generated/transcript text, private fields hidden, focus restoration'], pageErrors: []};
}
