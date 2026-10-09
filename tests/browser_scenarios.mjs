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
  await page.route('**/admin/scenarios', captureSubmission);
  await page.route('**/admin/scenarios/1/update', captureSubmission);
  async function captureSubmission(route) {
    requests.push({body:route.request().postDataJSON(), csrf:route.request().headers()['x-csrf-token']});
    // Keep the fixture screen open while exercising the real form handler.
    await route.fulfill({status:422, contentType:'application/json', body:'{"detail":"Fixture: no writes"}'});
  }
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
    await page.locator('#open-create-modal').focus();
    await page.keyboard.press('Enter');
    assert(await page.locator('#create-modal').evaluate(el => el.classList.contains('active')), 'keyboard opens create modal');
    await page.locator('#new-title').fill('New screen scenario');
    await page.locator('#new-framework').selectOption('1');
    await page.locator('#new-student-template').selectOption('1');
    await page.locator('#new-prompt').fill('Internal student context');
    await page.locator('#new-profile').fill('Student profile');
    await page.locator('#new-problem-situation').fill('New public problem');
    await page.locator('#new-problem-situation').press('Tab');
    assert(await page.locator('#new-greeting-message').evaluate(el => el === document.activeElement), 'create keyboard field order');
    await page.locator('#new-greeting-message').fill('New mentor greeting');
    await Promise.all([
      page.waitForResponse(response => response.url() === `${base}/admin/scenarios`),
      page.waitForEvent('dialog').then(dialog => dialog.accept()),
      page.locator('#create-form button[type="submit"]').click()
    ]);
    assert(requests.at(-1).body.problem_situation === 'New public problem', 'create keeps public problem');
    assert(requests.at(-1).body.greeting_message === 'New mentor greeting', 'create keeps greeting');
    await page.keyboard.press('Escape');
    assert(!(await page.locator('#create-modal').evaluate(el => el.classList.contains('active'))), 'Escape closes create');
    checks.push(`${width}px admin create fields, keyboard and submission`);

    await page.locator('.edit-btn').focus();
    await page.keyboard.press('Enter');
    assert(await page.locator('#edit-panel').evaluate(el => el.classList.contains('active')), 'keyboard opens edit panel');
    assert(await page.locator('#edit-problem-situation').inputValue() === 'Problem', 'edit public problem populated');
    assert(await page.locator('#edit-greeting-message').inputValue() === 'Hello', 'edit greeting populated');
    await page.locator('#edit-problem-situation').fill('Edited public problem');
    await page.locator('#edit-problem-situation').press('Tab');
    assert(await page.locator('#edit-greeting-message').evaluate(el => el === document.activeElement), 'edit keyboard field order');
    await page.locator('#edit-greeting-message').fill('Edited mentor greeting');
    await Promise.all([
      page.waitForResponse(response => response.url() === `${base}/admin/scenarios/1/update`),
      page.waitForEvent('dialog').then(dialog => dialog.accept()),
      page.locator('#edit-form button[type="submit"]').click()
    ]);
    assert(requests.at(-1).body.problem_situation === 'Edited public problem', 'edit keeps public problem');
    assert(requests.at(-1).body.greeting_message === 'Edited mentor greeting', 'edit keeps greeting');
    await page.keyboard.press('Escape');
    assert(!(await page.locator('#edit-panel').evaluate(el => el.classList.contains('active'))), 'Escape closes edit');
    checks.push(`${width}px admin edit legacy fixture, keyboard and submission`);
  }
  assert(requests.length === 4, 'one request per form submission');
  for (const request of requests) {
    assert(!('video_url' in request.body) && !('video_transcript' in request.body), 'no video keys submitted');
    assert(request.csrf === 'screen-test-token', 'CSRF retained');
  }
  assert(errors.length === 0, `uncaught page errors: ${errors.join('; ')}`);
  return {checks, submissions:requests.length, pageErrors:errors};
}
