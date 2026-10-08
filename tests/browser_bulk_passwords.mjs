// One-time bulk credentials on real administrator markup, with isolated HTTP.
export default async function checkBulkPasswords(page) {
  const base = new URL(page.url()).origin;
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const errors = [], requests = [], checks = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write'], {origin: base});
  await page.context().addCookies([{name:'csrftoken', value:'bulk-csrf', url:base}]);
  const nicknames = ['"><svg/onload=window.x=1>', '=2+3,"quoted"'];
  const credentials = nicknames.map((nickname, i) => ({
    username: `teacher_${i}`, nickname, initial_password: `initial_password_${i}`
  }));
  const preview = {
    rows: credentials.map(({username, nickname}, i) => ({
      row_num:i + 1, username, nickname, role:'teacher', group_id:null, errors:[]
    })), groups:[], summary:{total:2, valid:2, error:0}
  };
  await page.route('**/admin/users/bulk/preview', async route => {
    requests.push(route.request().headers()['x-csrf-token']);
    await route.fulfill({json:preview});
  });
  await page.route('**/admin/users/bulk/register', async route => {
    requests.push(route.request().headers()['x-csrf-token']);
    assert(route.request().postDataJSON().users[0].nickname === nicknames[0], 'preview preserves quoted text');
    await route.fulfill({json:{
      success_count:2, fail_count:1, credentials,
      failures:[{username:'existing', nickname:nicknames[0], reason:'Already exists'}]
    }});
  });
  const expectedCsv = 'username,nickname,initial_password\r\n'
    + '"teacher_0","""><svg/onload=window.x=1>","initial_password_0"\r\n'
    + '"teacher_1","\'=2+3,""quoted""","initial_password_1"';
  async function register() {
    await page.locator('#open-bulk-modal').click();
    await page.locator('#bulk-file-input').setInputFiles({
      name:'users.csv', mimeType:'text/csv', buffer:Buffer.from('username,nickname\n')
    });
    await page.locator('#bulk-register-btn').click();
    await page.locator('#bulk-close-btn').waitFor({state:'visible'});
  }
  async function checkCleared() {
    assert(await page.locator('#bulk-credentials-body').innerText() === '', 'credential DOM cleared');
    assert(await page.locator('#bulk-step-upload').isVisible(), 'reopening starts a new upload');
    assert(!await page.locator('#bulk-copy-credentials').isVisible(), 'closed credentials cannot be copied');
    const saved = await page.evaluate(() => ({local:{...localStorage}, session:{...sessionStorage}}));
    assert(!JSON.stringify(saved).includes('initial_password_'), 'no browser storage of credentials');
  }
  for (const width of [1280, 390]) {
    await page.setViewportSize({width, height:900});
    await page.goto(`${base}/admin/users`);
    assert(!(await page.content()).includes('00000000'), 'fixed password guidance removed');
    await register();
    assert(await page.getByText('이 화면을 닫으면 다시 볼 수 없음', {exact:true}).isVisible(), 'one-time warning');
    assert(await page.locator('#bulk-credentials-body tr').count() === 2, 'all successes displayed');
    for (let i = 0; i < 2; i++) {
      const cells = await page.locator('#bulk-credentials-body tr').nth(i).locator('td').allTextContents();
      assert(JSON.stringify(cells) === JSON.stringify([credentials[i].username, nicknames[i], credentials[i].initial_password]), 'exact safe text in result');
    }
    assert(await page.locator('#bulk-modal svg').count() === 0, 'no injected SVG');
    assert(await page.evaluate(() => window.x) === undefined, 'no XSS execution');
    assert(await page.locator('#bulk-failure-body').innerText() === `existing\t${nicknames[0]}\tAlready exists`, 'failure text stays safe');
    await page.locator('#bulk-copy-credentials').focus();
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => document.getElementById('bulk-credential-status').textContent.includes('복사했습니다'));
    assert(await page.evaluate(() => navigator.clipboard.readText()) === expectedCsv, 'clipboard CSV with formula protection');
    const [download] = await Promise.all([
      page.waitForEvent('download'), page.locator('#bulk-download-credentials').click()
    ]);
    const chunks = [];
    for await (const chunk of await download.createReadStream()) chunks.push(chunk);
    assert(Buffer.concat(chunks).toString('utf8') === '\ufeff' + expectedCsv, 'UTF-8 CSV with quotes and formula protection');
    assert(download.suggestedFilename() === 'bulk_user_credentials.csv', 'credential filename');
    await page.locator('#bulk-modal .close-modal-btn').click();
    await page.locator('#open-bulk-modal').click();
    await checkCleared();
    await page.locator('#bulk-modal .close-modal-btn').click();
    await register();
    await page.keyboard.press('Escape');
    assert(!(await page.locator('#bulk-modal').evaluate(el => el.classList.contains('active'))), 'Escape closes credentials');
    await page.locator('#open-bulk-modal').click();
    await checkCleared();
    checks.push(`${width}px safe credentials, keyboard copy, CSV download, close/Escape clears secrets`);
  }
  assert(requests.length === 8 && requests.every(token => token === 'bulk-csrf'), 'CSRF on every preview and register');
  await page.locator('#bulk-modal .close-modal-btn').click();
  await register();
  await page.evaluate(() => {
    navigator.clipboard.writeText = () => Promise.reject(new Error('Permission denied'));
  });
  await page.locator('#bulk-copy-credentials').click();
  await page.getByText('복사할 수 없습니다. CSV로 내려받으세요.', {exact:true}).waitFor();
  await page.locator('#bulk-modal .close-modal-btn').click();

  let releaseResponse, signalRequest;
  const held = new Promise(resolve => { releaseResponse = resolve; });
  const received = new Promise(resolve => { signalRequest = resolve; });
  await page.unroute('**/admin/users/bulk/register');
  await page.route('**/admin/users/bulk/register', async route => {
    signalRequest();
    await held;
    await route.fulfill({json:{success_count:2, fail_count:0, failures:[], credentials}});
  });
  await page.locator('#open-bulk-modal').click();
  await page.locator('#bulk-file-input').setInputFiles({
    name:'users.csv', mimeType:'text/csv', buffer:Buffer.from('username,nickname\n')
  });
  await page.locator('#bulk-register-btn').click();
  await received;
  await page.locator('#bulk-modal .close-modal-btn').click();
  await page.locator('#open-bulk-modal').click();
  const lateResponse = page.waitForResponse(response => response.url().endsWith('/bulk/register'));
  releaseResponse();
  await (await lateResponse).finished();
  // Let the fetch continuation finish before observing the reopened screen.
  await page.waitForTimeout(100);
  await checkCleared();
  checks.push('clipboard denial offers CSV; late response after close cannot restore credentials');
  assert(errors.length === 0, `uncaught page errors: ${errors.join('; ')}`);
  return {checks, submissions:requests.length, pageErrors:errors};
}
