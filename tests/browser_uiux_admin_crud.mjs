import assert from 'node:assert/strict';
export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.context().addCookies([{name:'csrftoken',value:'admin-uiux',url:base}]);
  const requests = [];
  page.on('dialog', dialog => dialog.accept());
  for (const kind of ['groups', 'about']) {
    await page.route(`**/admin/${kind}**`, async route => {
      const req = route.request();
      if (req.method() !== "POST") return route.continue();
      requests.push({url:req.url(), method:req.method(), csrf:req.headers()['x-csrf-token'], body:req.postDataJSON()});
      await route.fulfill({status:422,json:{detail:[{msg:'합성 입력 오류'}]}});
    });
    await page.goto(`${base}/fixtures/uiux/admin/${kind}`);
    await page.locator('#open-create-modal').click();
    const prefix = kind === 'groups' ? 'new' : 'create';
    await page.locator(`#${prefix}-name`).fill('합성 등록');
    if (kind === 'about') {
      await page.locator('#create-affiliation').fill('소속');
      await page.locator('#create-bio').fill('여러 줄\n이력');
    }
    await page.locator('#create-form button[type=submit]').click();
    await page.locator('#create-form .modal-error').waitFor();
    assert.equal(await page.locator('#create-form .modal-error').textContent(), '합성 입력 오류');
    assert.equal(await page.locator(`#${prefix}-name`).inputValue(), '합성 등록');
    assert(await page.locator('.modal-error').evaluate(el => el === document.activeElement));
    await page.keyboard.press('Escape');
    await page.locator('.edit-btn').first().click();
    const original = await page.locator('#edit-name').inputValue();
    assert(original.includes('긴'), 'existing literal long name is loaded');
    if (kind === 'groups') assert(original.includes('\"따옴표\"'), 'group name retains quotes without HTML entities');
    await page.locator('#edit-name').fill('수정 이름');
    await page.locator('#edit-form button[type=submit]').click();
    await page.locator('#edit-form .modal-error').waitFor();
    assert.equal(await page.locator('#edit-name').inputValue(), '수정 이름');
    await page.keyboard.press('Escape');
    assert(await page.locator('.edit-btn').first().evaluate(el => el === document.activeElement));
    if (kind === 'groups') {
      await page.locator('.delete-btn').first().click();
      await page.waitForFunction(() => !document.querySelector('.delete-btn').disabled);
      assert.equal(await page.locator('[data-group-id]').count(),1, 'rejected group deletion retains card');
    }
    if (kind === 'about') {
      await page.locator('.delete-btn').first().click();
      assert(await page.locator('#delete-confirm-msg').innerText());
      await page.locator('#confirm-delete').click();
      await page.locator('#delete-modal .modal-error').waitFor();
      assert(await page.locator('#delete-modal').isVisible(), 'failed delete stays inside confirmation');
      await page.keyboard.press('Escape');
    }
    await page.unroute(`**/admin/${kind}**`);
    // Success uses the existing reload, with no second mutation.
    await page.route(`**/admin/${kind}`, async route => {
      if (route.request().method() !== "POST") return route.continue();
      requests.push({url:route.request().url(), csrf:route.request().headers()['x-csrf-token'], body:route.request().postDataJSON()});
      await route.fulfill({status:201,json:{id:2}});
    });
    await page.locator('#open-create-modal').click();
    await page.locator(`#${prefix}-name`).fill('정상 등록');
    if (kind === 'about') {
      await page.locator('#create-affiliation').fill('소속');
      await page.locator('#create-bio').fill('이력');
    }
    await Promise.all([page.waitForEvent('load'), page.locator('#create-form button[type=submit]').click()]);
    assert.equal(await page.locator('#create-modal').isVisible(), false);
    await page.unroute(`**/admin/${kind}`);
  }
  assert.equal(requests.length,8);
  assert(requests.every(req => req.csrf === 'admin-uiux'));
  assert(requests.some(req => req.body?.name === '정상 등록'));
  await page.goto(`${base}/fixtures/uiux/admin/groups?empty=1`);
  await page.locator('#open-create-modal-empty').click();
  await page.locator('#open-create-modal-empty').evaluate(el => el.remove());
  await page.keyboard.press('Escape');
  assert(await page.locator('main h1').evaluate(el => el === document.activeElement), 'removed caller returns to task heading');
  return {pageErrors:[]};
}
