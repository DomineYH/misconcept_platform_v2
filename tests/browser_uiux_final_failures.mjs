import assert from 'node:assert/strict';

export default async function check(page) {
  page.setDefaultTimeout(5000);
  const base = new URL(page.url()).origin;
  page.on('dialog', dialog => dialog.accept());
  await page.context().addCookies([{name:'csrftoken',value:'final-csrf',url:base}]);
  for (const [kind,path,row] of [
    ['users','/fixtures/s6/users','tr[data-user-id]'],
    ['groups','/fixtures/uiux/admin/groups','[data-group-id]'],
  ]) {
    let calls = 0, lost = false;
    await page.route(`**/admin/${kind}/*/delete`, route => {
      calls++;
      assert.equal(route.request().headers()['x-csrf-token'],'final-csrf');
      return lost ? route.abort('failed') : route.fulfill({status:403,json:{detail:'합성 삭제 거절'}});
    });
    await page.goto(base+path);
    const count = await page.locator(row).count();
    const button = page.locator('.delete-btn').first();
    await button.click();
    const notice = page.locator(row).first().getByRole('alert');
    await notice.waitFor();
    assert.equal(await notice.innerText(),'합성 삭제 거절','rejected deletion remains beside its row after dismissing confirmation');
    assert(await notice.evaluate(el => el === document.activeElement));
    assert(await button.isEnabled());
    assert.equal(await page.locator(row).count(),count);
    lost = true;
    await button.click();
    await notice.getByText('삭제 결과를 확인할 수 없습니다. 목록을 새로 확인하세요.',{exact:true}).waitFor();
    assert.equal(await page.locator(row).count(),count,'lost delete response retains the visible record');
    assert(await button.isEnabled());
    assert.equal(calls,2,'only explicit deletions are submitted');
    await page.unroute(`**/admin/${kind}/*/delete`);
  }
  return {pageErrors:[]};
}
