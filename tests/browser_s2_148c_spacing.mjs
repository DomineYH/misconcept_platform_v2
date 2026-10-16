import assert from 'node:assert/strict';

export default async function checkScenarioHeadingAndSpacing(page) {
  const base = new URL(page.url()).origin;
  const submissions = [];
  await page.route('**/admin/scenarios/1/update', async route => {
    submissions.push(route.request().postDataJSON());
    await route.fulfill({status:422, json:{detail:[
      {path:'student.resolved_model_config', code:'common_model_required', message:'A common model is required'}
    ]}});
  });
  for (const language of ['ko','en']) {
    await page.context().addCookies([{name:'ui_lang', value:language, url:base}]);
    for (const theme of ['light','dark']) {
      await page.emulateMedia({colorScheme:theme});
      for (const width of [390,1440]) {
        await page.setViewportSize({width, height:900});
        await page.goto(`${base}/fixtures/s2/editor`);
        await page.waitForFunction(() => document.getElementById('editor-status').textContent);
        const gaps = await page.evaluate(() => {
          const rect = selector => document.querySelector(selector).getBoundingClientRect();
          const gap = (before, after) => rect(after).top - rect(before).bottom;
          return {
            minimum:parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--spacing-md')) * parseFloat(getComputedStyle(document.documentElement).fontSize),
            statusToComparison:gap('#editor-status', '.scenario-editor > p:has(a[href^="/admin/model-comparison"])'),
            comparisonToNotice:gap('.scenario-editor > p:has(a[href^="/admin/model-comparison"])', '.scenario-editor > p:has(+ #open-comparison)'),
            noticeToButton:gap('.scenario-editor > p:has(+ #open-comparison)', '#open-comparison'),
            buttonToModel:gap('#open-comparison', 'label[for="common-model"]'),
            modelToSteps:gap('#common-model-status', '.scenario-layout'),
            headingToLabel:gap('#step-0 > h2', 'label[for="title"]')
          };
        });
        const label = `${language}/${theme}/${width}`;
        for (const [name, value] of Object.entries(gaps)) {
          if (name !== 'minimum') assert(value >= gaps.minimum - .01, `${label} ${name}: ${value}px, expected at least ${gaps.minimum}px`);
        }
        const title = `Preserved ${label}`;
        await page.locator('#title').fill(title);
        for (const [button, action] of [['#save-draft','save_draft'], ['#publish-scenario','publish']]) {
          const response = page.waitForResponse(r => r.url().endsWith('/admin/scenarios/1/update'));
          await page.locator(button).click();
          assert.equal((await response).status(), 422);
          await page.locator('#editor-errors').waitFor();
          assert.equal(await page.locator('#editor-errors h2').innerText(), language === 'ko' ? '저장·게시 전 보완 항목' : 'Items to complete before saving or publishing');
          assert.equal(submissions.at(-1).action, action);
          assert.equal(await page.locator('#title').inputValue(), title);
        }
        console.log(`${label}: spacing and save/publish error headings passed`);
      }
    }
  }
  return {pageErrors:[]};
}
