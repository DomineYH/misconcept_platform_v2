import assert from 'node:assert/strict';

// Existing user form behavior on a page-sized list, including rejected edits.
export default async function checkPagedUserForms(page) {
  const base = new URL(page.url()).origin;
  const listUrl = `${base}/admin/users?page=2`;
  let status = 403, submissions = [];
  await page.context().addCookies([{name:'csrftoken', value:'s6-form-csrf', url:base}]);
  page.on('dialog', dialog => dialog.accept());
  await page.route('**/admin/users?*', async route => {
    await route.fulfill({response:await route.fetch({url:`${base}/fixtures/s6/users?page=2`})});
  });
  async function submit(route) {
    const request = route.request();
    assert(request.headers()['x-csrf-token'] === 's6-form-csrf');
    submissions.push(request.postDataJSON());
    await route.fulfill({status, json:status === 200 ? {} : {detail:'권한이 없습니다.'}});
  }
  await page.route('**/admin/users/21/update', submit);
  await page.route('**/admin/users', submit);
  for (const width of [1280, 390]) {
    await page.setViewportSize({width, height:900});
    await page.goto(listUrl);
    await page.locator('.edit-btn[data-id="21"]').click();
    assert(await page.locator('#edit-username-display').inputValue() === 'teacher_21', 'edit targets current page row');
    assert(await page.locator('#edit-group').inputValue() === '1', 'group assignment loaded');
    await page.locator('#edit-nickname').fill('수정 중인 교사');
    await page.locator('#edit-group').selectOption('0');
    status = 403;
    const rejected = page.waitForResponse(r => r.url().endsWith('/update'));
    await page.locator('#edit-form button[type="submit"]').click();
    await rejected;
    assert(await page.locator('#edit-nickname').inputValue() === '수정 중인 교사', 'denial preserves dirty input');
    assert(await page.locator('#edit-panel').evaluate(el => el.classList.contains('active')), 'denial preserves panel');
    assert(page.url() === listUrl, 'denial preserves page');
    assert.deepEqual(submissions.at(-1), {nickname:'수정 중인 교사', role:'teacher', group_id:0}, 'group removal and no unsolicited password reset');
    status = 200;
    const updated = page.waitForResponse(r => r.url().endsWith('/update'));
    await Promise.all([
      page.waitForEvent('load'), page.locator('#edit-form button[type="submit"]').click()
    ]);
    await updated;
    assert(!await page.locator('#edit-panel').evaluate(el => el.classList.contains('active')), 'saved edit closes panel');
    assert(page.url() === listUrl, 'edit reload retains current page');

    await page.locator('#open-create-modal').click();
    await page.locator('#new-username').fill('new_teacher');
    await page.locator('#new-password').fill('synthetic-password');
    await page.locator('#new-nickname').fill('새 교사');
    await page.locator('#new-group').selectOption('1');
    const created = page.waitForResponse(r => r.url() === `${base}/admin/users`);
    await Promise.all([
      page.waitForEvent('load'), page.locator('#create-form button[type="submit"]').click()
    ]);
    await created;
    assert(!await page.locator('#create-modal').evaluate(el => el.classList.contains('active')), 'saved creation closes modal');
    assert.deepEqual(submissions.at(-1), {
      username:'new_teacher', password:'synthetic-password', nickname:'새 교사', role:'teacher', group_id:1
    }, 'creation preserves group assignment');
    assert(page.url() === listUrl, 'create reload retains current page');
  }
  assert(submissions.length === 6, 'one request per explicit submit');
  return {pageErrors:[]};
}
