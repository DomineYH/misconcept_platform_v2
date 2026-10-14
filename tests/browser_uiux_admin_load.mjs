import assert from 'node:assert/strict';
export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.route('**/admin/user-conversations/1/sessions', route => route.fulfill({status:403, json:[]}));
  await page.goto(`${base}/fixtures/uiux/admin/user_conversations`);
  await page.getByRole('button', {name:'개별', exact:true}).click();
  await page.getByText('세션 목록을 불러올 수 없습니다.', {exact:true}).waitFor({timeout:3000});
  assert(await page.locator('#sessionModal').evaluate(el => el.contains(document.activeElement)), 'failed load stays focused in its dialog');
  await page.keyboard.press('Escape');
  assert(await page.getByRole('button', {name:'개별',exact:true}).evaluate(el => el === document.activeElement));
  await page.unroute('**/admin/user-conversations/1/sessions');
  await page.route('**/admin/user-conversations/1/sessions', route => route.fulfill({json:[{
    id:1,started_at:'2026-10-01',scenario_title:'합성 <시나리오>',message_count:2
  }]}));
  await page.getByRole('button', {name:'개별',exact:true}).click();
  const link = page.locator('#sessionListContent a');
  await link.waitFor();
  assert.equal(await link.getAttribute('href'),'/admin/sessions/1/download');
  assert(await page.getByText('합성 <시나리오>', {exact:true}).isVisible());
  await page.keyboard.press('Escape');
  await page.unroute('**/admin/user-conversations/1/sessions');
  let release, received;
  const held = new Promise(resolve => { release = resolve; });
  const started = new Promise(resolve => { received = resolve; });
  let calls = 0;
  await page.route('**/admin/user-conversations/1/sessions', async route => {
    if (++calls === 1) {
      received();
      await held;
      await route.fulfill({json:[{id:9,started_at:'2026-10-01',scenario_title:'늦은 원래 목록',message_count:1}]});
    } else await route.fulfill({json:[]});
  });
  await page.getByRole('button', {name:'개별',exact:true}).click();
  await started;
  await page.keyboard.press('Escape');
  await page.getByRole('button', {name:'개별',exact:true}).click();
  await page.getByText('완료된 세션이 없습니다.', {exact:true}).waitFor();
  const response = page.waitForResponse(r => r.url().endsWith('/sessions'));
  release();
  await (await response).finished();
  await page.waitForTimeout(100);
  assert(await page.getByText('완료된 세션이 없습니다.', {exact:true}).isVisible(), 'closed request cannot replace the reopened empty list');
  assert.equal(await page.locator('#sessionListContent a').count(),0);
  await page.keyboard.press('Escape');
  return {pageErrors:[]};
}
