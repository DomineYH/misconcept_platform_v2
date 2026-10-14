import assert from 'node:assert/strict';

export default async function check(page) {
  page.setDefaultTimeout(5000);
  const base = new URL(page.url()).origin;
  for (const theme of ['light','dark']) {
    await page.emulateMedia({colorScheme:theme});
    await page.setViewportSize({width:390,height:900});
    for (const [path,text] of [
      ['/fixtures/s6/users?total=0','사용자가 없습니다.'],
      ['/fixtures/uiux/admin/sessions?empty=1','세션이 없습니다.'],
      ['/fixtures/uiux/admin/user_conversations?empty=1','대화 기록이 있는 사용자가 없습니다.'],
      ['/fixtures/uiux/admin/about?empty=1','등록된 정보가 없습니다.'],
    ]) {
      await page.goto(base+path);
      await page.getByText(text,{exact:true}).waitFor();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    }
    await page.goto(base+'/admin/api-usage?empty=1');
    assert.equal(await page.getByText('기록이 없습니다.',{exact:true}).count(),2);
    assert.equal(await page.locator('#known-cost').innerText(),'$0.000000');
    await page.goto(base+'/fixtures/s5/menu');
    assert.deepEqual(await page.locator('.action-card').evaluateAll(els => els.map(el => el.getAttribute('href'))),[
      '/admin/ai','/admin/scenarios','/admin/scenarios?compare=1','/admin/user-conversations','/admin/users','/admin/groups','/admin/about',
    ]);
    await page.route('**/about/content', async route => route.fulfill({
      contentType:'text/html',body:await (await page.request.get(base+'/about/content?empty=1')).text(),
    }));
    await page.goto(base+'/login');
    await page.locator('.hamburger').click();
    await page.locator('#open-about-modal').click();
    await page.getByText('등록된 정보가 없습니다.',{exact:true}).waitFor();
    await page.keyboard.press('Escape');
    assert(await page.locator('#open-about-modal').evaluate(el => el === document.activeElement));
    await page.unroute('**/about/content');
  }
  await page.route('**/admin/users/bulk/preview', route => route.fulfill({json:{
    rows:[{row_num:1,username:'synthetic',nickname:'합성',role:'teacher',group_id:null,errors:[]}],
    groups:[],summary:{total:1,valid:1,error:0},
  }}));
  let writes = 0;
  await page.route('**/admin/users/bulk/register', route => {
    writes++;
    return route.abort('failed');
  });
  await page.goto(base+'/admin/users');
  await page.locator('#open-bulk-modal').click();
  await page.locator('#bulk-file-input').setInputFiles({name:'synthetic.csv',mimeType:'text/csv',buffer:Buffer.from('username,nickname\nsynthetic,합성')});
  await page.locator('#bulk-register-btn').click();
  const error = page.locator('#bulk-modal').getByRole('alert');
  await error.waitFor();
  assert.equal(await error.innerText(),'등록 결과를 확인할 수 없습니다. 사용자 목록을 확인한 뒤 다시 시도하세요.');
  assert(await error.evaluate(el => el === document.activeElement));
  assert(await page.locator('#bulk-step-preview').isVisible());
  assert(await page.locator('#bulk-register-btn').isEnabled());
  assert.equal(await page.locator('#bulk-credentials-body').innerText(),'');
  assert.equal(writes,1,'lost registration is never automatically repeated');
  await page.keyboard.press('Escape');
  assert(await page.locator('#open-bulk-modal').evaluate(el => el === document.activeElement));
  return {pageErrors:[]};
}
