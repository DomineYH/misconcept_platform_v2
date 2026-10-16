import assert from 'node:assert/strict';
export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.route('**/about/content', route => route.fulfill({status:503,body:'Unavailable'}));
  await page.goto(`${base}/login`);
  assert.equal(await page.locator('#open-about-modal').innerText(), '소개');
  assert.equal(await page.getByRole('button', {name:'소개',exact:true}).getAttribute('id'), 'open-about-modal');
  await page.locator('#open-about-modal').click();
  await page.locator('#about-load-error').waitFor();
  assert.equal(await page.locator('#about-load-error').innerText(), '소개를 불러올 수 없습니다. 소개를 다시 눌러 주세요.');
  assert.equal(await page.locator('#about-modal-overlay').isVisible(), false);
  assert(await page.locator('#about-load-error').evaluate(el => el === document.activeElement));
  await page.waitForTimeout(100);
  assert(await page.locator('#about-load-error').isVisible(), 'load failure persists next to caller');
  await page.unroute('**/about/content');
  let release;
  const responseReady = new Promise(resolve => { release = resolve; });
  await page.route('**/about/content', async route => {
    await responseReady;
    await route.continue();
  });
  await page.locator('#open-about-modal').click();
  try {
    assert(await page.getByRole('dialog', {name:'소개',exact:true}).isVisible());
    assert.equal(await page.locator('#about-modal-container h2').innerText(), '소개');
    assert.equal(await page.locator('#about-modal-container [role=status]').innerText(), '소개를 불러오는 중...');
  } finally { release(); }
  await page.locator('#about-modal-container .contributor-card').waitFor();
  await page.unroute('**/about/content');
  assert.equal(await page.locator('#about-modal-container h2').innerText(), '소개');
  await page.locator('#about-modal-container .modal-close').focus();
  await page.keyboard.press('Shift+Tab');
  assert(await page.locator('#about-modal-overlay').evaluate(el => el.contains(document.activeElement)));
  await page.keyboard.press('Escape');
  assert(await page.locator('#open-about-modal').evaluate(el => el === document.activeElement));

  await page.goto(`${base}/fixtures/uiux/admin/about`);
  for (const [language, label, heading, create, edit] of [
    ['en','About','About management','Add contributor','Edit contributor'],
    ['ko','소개','소개 관리','새 등록자 추가','등록자 수정'],
  ]) {
    await Promise.all([
      page.waitForEvent('load'),
      page.locator(`[data-ui-language="${language}"]`).click(),
    ]);
    assert.equal(await page.locator('html').getAttribute('lang'), language);
    assert.equal(await page.locator('main h1').innerText(), heading);
    assert.equal(await page.locator('#open-about-modal').innerText(), label);
    assert.equal(await page.getByRole('button', {name:label,exact:true}).getAttribute('id'), 'open-about-modal');
    await page.locator('#open-about-modal').click();
    await page.locator('#about-modal-container .contributor-card').waitFor();
    assert(await page.getByRole('dialog', {name:label,exact:true}).isVisible());
    assert.equal(await page.locator('#about-modal-container h2').innerText(), label);
    await page.locator('#about-modal-container .modal-close').click();
    assert(await page.locator('#open-about-modal').evaluate(el => el === document.activeElement));
    await page.locator('#open-create-modal').click();
    assert(await page.getByRole('dialog', {name:create,exact:true}).isVisible());
    await page.keyboard.press('Escape');
    await page.locator('.edit-btn').first().click();
    assert(await page.getByRole('dialog', {name:edit,exact:true}).isVisible());
    await page.keyboard.press('Escape');
  }
  return {pageErrors:[]};
}
