// Run with a Playwright Page against tests/browser_server.py; no real API/DB.
export default async function checkScenarioScreens(page) {
  const base = page.url().split('/').slice(0, 3).join('/');
  await page.unrouteAll({behavior: 'wait'});
  page.removeAllListeners('pageerror');
  page.removeAllListeners('dialog');
  const errors = [], requests = [], checks = [];
  const assert = (value, message) => { if (!value) throw new Error(message); };
  page.on('pageerror', error => errors.push(error.message));
  await page.context().addCookies([{name:'csrftoken', value:'screen-test-token', url:base}]);
  await page.route('**/admin/scenarios/1/delete', async route => {
    requests.push({body:route.request().postDataJSON(), csrf:route.request().headers()['x-csrf-token']});
    await route.fulfill({status:409, json:{detail:{code:'version_conflict', current_version:2}}});
  });
  const noLegacyVideo = async () => {
    const html = await page.content();
    for (const removed of ['legacy-secret', 'PRIVATE LEGACY TRANSCRIPT', 'video_url', 'video_transcript', 'videoUrl']) {
      assert(!html.includes(removed), `legacy video exposed: ${removed}`);
    }
    assert(await page.locator('iframe, [data-tab="video"], [name="video_url"], [name="video_transcript"]').count() === 0, 'no video UI');
  };

  for (const width of [1280, 390]) {
    await page.setViewportSize({width, height:900});
    await page.goto(`${base}/chat?mentor_manual=1`);
    await noLegacyVideo();
    assert(await page.locator('.greeting-message .message-bubble').innerText() === 'Hello', 'mentor greeting retained');
    if (width === 390) await page.locator('[data-panel="scenario"]').click();
    const problemTab = page.locator('[data-tab="situation"]');
    await problemTab.focus();
    await page.keyboard.press('Enter');
    assert(await page.locator('#tab-situation').isVisible(), 'keyboard opens problem tab');
    assert((await page.locator('#tab-situation').innerText()).trim() === 'Problem', 'public problem retained');
    assert(await problemTab.evaluate(el => el === document.activeElement), 'tab retains focus');
    await page.locator('[data-tab="profile"]').focus();
    await page.keyboard.press('Space');
    assert(await page.locator('#tab-profile').innerText() === 'Profile', 'student profile retained');
    if (width === 390) await page.locator('[data-panel="chat"]').click();
    const input = page.locator('#teacher-input');
    await input.focus();
    await input.fill('Keyboard draft');
    await page.keyboard.press('Control+b');
    assert(await input.evaluate(el => el === document.activeElement), 'composer focus retained');
    assert(await input.inputValue() === 'Keyboard draft', 'composer draft retained');
    checks.push(`${width}px teacher public problem/profile/greeting and keyboard/focus`);

    await page.goto(`${base}/chat?missing_problem=1`);
    if (width === 390) await page.locator('[data-panel="scenario"]').click();
    await page.locator('[data-tab="situation"]').click();
    assert((await page.locator('#tab-situation').innerText()).includes('문제 상황 보완 필요'), 'missing problem status');
    assert(!(await page.content()).includes('PRIVATE STUDENT PROMPT'), 'no hidden prompt fallback');
    await noLegacyVideo();
    await page.goto(`${base}/scenarios?missing_problem=1`);
    assert(await page.getByText('문제 상황 보완 필요', {exact:true}).isVisible(), 'missing problem on selection');
    assert(await page.getByRole('button', {name:'대화 시작'}).isDisabled(), 'incomplete scenario cannot start from UI');
    checks.push(`${width}px missing public problem state without hidden prompt`);

    await page.goto(`${base}/admin/scenarios-page`);
    await noLegacyVideo();
    assert(await page.getByRole('link', {name:'통합 초안 작성'}).getAttribute('href') === '/admin/scenarios/new', 'unified creation link');
    assert(await page.getByRole('link', {name:'수정', exact:true}).getAttribute('href') === '/admin/scenarios/1/edit', 'unified edit link');
    assert(await page.locator('[name="framework_id"], [name="student_template_id"], [name="tutor_template_id"], #create-form, #edit-form').count() === 0, 'retired selectors/forms absent');
    assert(!(await page.content()).includes('PRIVATE STUDENT PROMPT'), 'list contains no private source');
    assert((await page.locator('.scenario-card').innerText()).includes('초안'), 'draft state shown');
    await page.locator('#filter-publication').selectOption('published');
    assert(!(await page.locator('.scenario-card').isVisible()), 'publication filter');
    await page.locator('#filter-publication').selectOption('draft');
    assert(await page.locator('.scenario-card').isVisible(), 'draft restored');
    await page.locator('#search-input').fill('missing');
    assert(!(await page.locator('.scenario-card').isVisible()), 'search filter');
    await page.locator('#search-input').fill('Browser');
    assert(await page.locator('.scenario-card').isVisible(), 'search restored');
    await page.locator('#filter-status').selectOption('inactive');
    assert(!(await page.locator('.scenario-card').isVisible()), 'activation filter');
    await page.locator('#filter-status').selectOption('active');
    assert(await page.locator('.scenario-card').isVisible(), 'active restored');
    await page.locator('.delete-btn').focus();
    const confirm = page.waitForEvent('dialog').then(dialog => dialog.accept());
    const failed = page.waitForEvent('dialog', {predicate:d => d.type() === 'alert'}).then(async dialog => {
      assert(dialog.message().includes('다른 관리자'), 'conflict is explicit');
      await dialog.accept();
    });
    await page.keyboard.press('Enter');
    await Promise.all([confirm, failed]);
    assert(await page.locator('.delete-btn').isEnabled(), 'failed deletion preserves control');
    assert(await page.locator('.scenario-card').isVisible(), 'conflict preserves list');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no horizontal overflow');
    checks.push(`${width}px unified list links/filter/keyboard/CSRF/version conflict`);
  }
  assert(requests.length === 2, 'one request per delete attempt');
  for (const request of requests) {
    assert(request.body.expected_version === 1, 'deletion carries revision');
    assert(request.csrf === 'screen-test-token', 'CSRF retained');
  }
  assert(errors.length === 0, `uncaught page errors: ${errors.join('; ')}`);
  return {checks, submissions:requests.length, pageErrors:errors};
}
