import assert from 'node:assert/strict';
export default async function check(page) {
  const base = new URL(page.url()).origin;
  // A short synthetic viewport checks reflow; this is not a real virtual keyboard.
  await page.setViewportSize({width:320,height:360});
  for (const theme of ['light','dark']) {
    await page.emulateMedia({colorScheme:theme});
  for (const [path,opener,root] of [
    ['/fixtures/uiux/admin/groups','#open-create-modal','#create-modal'],
    ['/fixtures/uiux/admin/groups','.edit-btn','#edit-modal'],
    ['/fixtures/uiux/admin/about','#open-create-modal','#create-modal'],
    ['/fixtures/uiux/admin/about','.edit-btn','#edit-modal'],
    ['/fixtures/uiux/admin/about','.delete-btn','#delete-modal'],
    ['/fixtures/s6/users','#open-create-modal','#create-modal'],
    ['/fixtures/s6/users','.edit-btn','#edit-panel'],
    ['/admin/users','#open-bulk-modal','#bulk-modal'],
  ]) {
    await page.goto(base+path);
    await page.locator(opener).first().click();
    const dialog = page.locator(root);
    for (let i=0;i<18;i++) {
      await page.keyboard.press('Tab');
      assert(await dialog.evaluate(el => el.contains(document.activeElement)),`${root}: focus is trapped through last action`);
    }
    await dialog.evaluate(async el => {
      await Promise.all(el.getAnimations({subtree:true}).filter(a => a.effect.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {})));
    });
    const bounds = await dialog.evaluate(el => {
      const box = (el.querySelector('.modal-container') || el).getBoundingClientRect();
      const footer = el.querySelector('.modal-footer, .panel-footer').getBoundingClientRect();
      return {x:box.x,right:box.right,y:box.y,bottom:box.bottom,footer:footer.bottom};
    });
    assert(bounds.x>=16 && bounds.right<=304 && bounds.y>=16 && bounds.bottom<=344 && bounds.footer<=344,JSON.stringify(bounds));
    if (root === '#edit-panel') {
      await page.locator('#edit-panel summary').click();
      await page.locator('#edit-password').fill('synthetic-password');
      const bottom = await page.locator('#edit-panel .panel-footer').evaluate(el => el.getBoundingClientRect().bottom);
      assert(bottom <= 344, 'expanded credential field keeps final action reachable');
    }
    await page.keyboard.press('Escape');
    assert(await page.locator(opener).first().evaluate(el => el === document.activeElement));
    if (root === '#edit-panel') assert.equal(await page.locator('#edit-password').inputValue(),'');
  }
  }
  return {pageErrors:[]};
}
