import assert from 'node:assert/strict';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.setViewportSize({width:1280,height:900});
  for (const language of ['ko','en']) {
    const next = language === 'ko' ? 'en' : 'ko';
    const choose = () => page.locator(`[data-ui-language="${next}"]`).evaluate(button => button.click());
    async function blocked(busy = false) {
      await choose();
      assert.equal(await page.locator('html').getAttribute('lang'), language);
      assert.equal(await page.locator('#language-notice').innerText(), busy
        ? language === 'ko' ? '작업이 끝난 뒤 전환하세요' : 'Wait until the task finishes before switching languages.'
        : language === 'ko' ? '저장하거나 입력을 비운 뒤 전환하세요' : 'Save or clear your input before switching languages.');
    }
    for (const kind of ['groups','about']) {
      await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
      const path = `${base}/fixtures/uiux/admin/${kind}`;
      await page.goto(path);
      await page.locator('#open-create-modal').click();
      await Promise.all([page.waitForEvent('load'), choose()]);
      assert.equal(await page.locator('html').getAttribute('lang'), next, 'empty dialog permits switching');
      await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
      await page.goto(path);
      let finish, received;
      const started = new Promise(resolve => { received = resolve; });
      await page.route(`**/admin/${kind}`, async route => {
        if (route.request().method() !== 'POST') return route.continue();
        assert.equal(route.request().headers()['x-ui-language'],language);
        await new Promise(resolve => {
          finish = () => route.fulfill({status:422,json:{detail:language === 'ko' ? '입력값을 확인하세요.' : 'Check your input.'}}).then(resolve);
          received();
        });
      });
      await page.locator('#open-create-modal').click();
      const prefix = kind === 'groups' ? 'new' : 'create';
      await page.locator(`#${prefix}-name`).fill('원문 이름');
      if (kind === 'about') {
        await page.locator('#create-affiliation').fill('원문 소속');
        await page.locator('#create-bio').fill('원문 이력');
      }
      await blocked();
      await page.locator('#create-form button[type=submit]').click();
      await started;
      await page.locator(`#${prefix}-name`).fill('저장 중 편집');
      await blocked(true);
      await finish();
      await page.locator('.modal-error').waitFor();
      await blocked();
      assert.equal(await page.locator(`#${prefix}-name`).inputValue(),'저장 중 편집');
      await page.locator(`#${prefix}-name`).fill('');
      if (kind === 'about') {
        await page.locator('#create-affiliation').fill('');
        await page.locator('#create-bio').fill('');
      }
      await Promise.all([page.waitForEvent('load'), choose()]);
      assert.equal(await page.locator('html').getAttribute('lang'),next,'cleared failed form releases protection');
      await page.unroute(`**/admin/${kind}`);
      await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
      await page.goto(path);
      await page.locator('.edit-btn').first().click();
      const edit = page.locator(kind === 'groups' ? '#edit-description' : '#edit-affiliation');
      const original = await edit.inputValue();
      await edit.fill('편집 중');
      await blocked();
      await edit.fill(original);
      await Promise.all([page.waitForEvent('load'), choose()]);
      assert.equal(await page.locator('html').getAttribute('lang'),next,'reverted edit releases protection');
    }
    for (const [path, selector, value] of [
      ['/admin/users','#search-input','unsubmitted search'],
      ['/fixtures/uiux/admin/analysis','[name=date_from]','2026-10-01'],
    ]) {
      await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
      await page.goto(base + path);
      await page.locator(selector).fill(value);
      await blocked();
      assert.equal(await page.locator(selector).inputValue(),value);
      await page.locator(selector).fill('');
      await Promise.all([page.waitForEvent('load'), choose()]);
      assert.equal(await page.locator('html').getAttribute('lang'),next,'clearing unsubmitted filters releases protection');
    }
    await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
    await page.goto(`${base}/admin/users`);
    let finishPreview, receivedPreview;
    const previewStarted = new Promise(resolve => { receivedPreview = resolve; });
    await page.route('**/admin/users/bulk/preview', async route => {
      assert.equal(route.request().headers()['x-ui-language'],language);
      await new Promise(resolve => {
        finishPreview = () => route.fulfill({json:{
          rows:[{row_num:1,username:'synthetic',nickname:'원문 별명',role:'teacher',group_id:null,errors:[]}],
          groups:[],summary:{total:1,valid:1,error:0}
        }}).then(resolve);
        receivedPreview();
      });
    });
    await page.route('**/admin/users/bulk/register', route => route.fulfill({json:{
      success_count:1,fail_count:1,credentials:[{username:'synthetic',nickname:'원문 별명',initial_password:'SYNTHETIC_ONLY'}],
      failures:[{username:'conflict',nickname:'실패 원문',reason:'conflict'}]
    }}));
    await page.locator('#open-bulk-modal').click();
    await page.locator('#bulk-file-input').setInputFiles({name:'users.csv',mimeType:'text/csv',buffer:Buffer.from('username,nickname\nsynthetic,원문 별명\n')});
    await previewStarted;
    await blocked(true);
    await finishPreview();
    await page.locator('#bulk-preview-body tr').waitFor();
    await blocked();
    await page.locator('#bulk-register-btn').click();
    await page.locator('#bulk-credentials-body tr').waitFor();
    await blocked();
    assert.match(await page.locator('#bulk-failure-body').innerText(),/실패 원문/);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#bulk-credentials-body').textContent(),'');
    await Promise.all([page.waitForEvent('load'), choose()]);
    assert.equal(await page.locator('html').getAttribute('lang'),next,'explicit CSV dismissal clears file, preview and credentials');
    await page.unroute('**/admin/users/bulk/preview');
    await page.unroute('**/admin/users/bulk/register');
    await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
    await page.goto(`${base}/admin/users`);
    await page.route('**/admin/users/bulk/preview', route => route.fulfill({status:422,json:{detail:language === 'ko' ? '입력값을 확인하세요.' : 'Check your input.'}}));
    await page.locator('#open-bulk-modal').click();
    await page.locator('#bulk-file-input').setInputFiles({name:'invalid.csv',mimeType:'text/csv',buffer:Buffer.from('invalid')});
    await page.locator('#bulk-upload-error').waitFor({state:'visible'});
    await blocked();
    assert.match(await page.locator('#bulk-file-name').innerText(),/invalid.csv/);
    await page.keyboard.press('Escape');
    await Promise.all([page.waitForEvent('load'), choose()]);
    assert.equal(await page.locator('html').getAttribute('lang'),next,'dismissing failed CSV input releases protection');
    await page.unroute('**/admin/users/bulk/preview');
  }
  return {pageErrors:[]};
}
