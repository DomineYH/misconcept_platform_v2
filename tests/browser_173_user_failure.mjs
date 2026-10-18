import assert from 'node:assert/strict';

// Existing user forms keep pending and rejected input; presentation changes must preserve those policies.
export default async function check(page) {
  const base=new URL(page.url()).origin;
  const requests=[];
  await page.context().addCookies([{name:'csrftoken',value:'173-user-form',url:base}]);
  for(const [language,theme,width] of [['ko','light',320],['en','dark',390]]) {
    await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
    await page.emulateMedia({colorScheme:theme,reducedMotion:'reduce'});
    await page.setViewportSize({width,height:900});
    for(const kind of ['create','edit']) {
      let release,received;
      const held=new Promise(resolve=>{release=resolve;});
      const started=new Promise(resolve=>{received=resolve;});
      const url=kind==='create'?'**/admin/users':'**/admin/users/*/update';
      const handler=async route=>{
        assert.equal(route.request().method(),'POST');
        assert.equal(route.request().headers()['x-csrf-token'],'173-user-form');
        requests.push(route.request().postDataJSON());received();await held;
        await route.fulfill({status:403,json:{detail:'173 synthetic permission failure'}});
      };
      await page.route(url,handler);
      try {
        await page.goto(`${base}/fixtures/s6/users`);
        const opener=page.locator(kind==='create'?'#open-create-modal':'.edit-btn').first();
        const root=page.locator(kind==='create'?'#create-modal':'#edit-panel');
        await opener.click();
        const name=page.locator(kind==='create'?'#new-nickname':'#edit-nickname');
        await name.fill('173 preserved name');
        if(kind==='create') {
          await page.locator('#new-username').fill('teacher_173');
          await page.locator('#new-password').fill('synthetic-only-173');
        }
        const count=requests.length;
        const submit=root.locator('button[type=submit]');
        await submit.click();await started;
        assert.equal(await name.inputValue(),'173 preserved name','pending request keeps entered name');
        assert(await submit.isEnabled(),'existing pending policy keeps submit control enabled');
        assert.equal(await root.getAttribute('role'),'dialog','pending form remains the same dialog');
        release();
        const error=root.locator('.modal-error');
        await error.waitFor();
        assert(await error.evaluate(el=>el===document.activeElement),'rejected form focuses local error');
        assert.equal(await name.inputValue(),'173 preserved name','403 preserves entered name');
        assert.equal(requests.length,count+1,'one request per explicit submit');
        assert(await root.evaluate(el=>el.classList.contains('active')),'403 keeps dialog open');
        if(kind==='create') {
          assert.equal(await page.locator('#new-username').inputValue(),'teacher_173');
          assert.equal(await page.locator('#new-password').inputValue(),'synthetic-only-173','failure preserves unsaved secret until close');
        } else assert(await page.locator('#edit-username-display').isDisabled(),'user identifier remains disabled');
        await root.locator('.modal-header button,.panel-header button').first().click();
        assert(await opener.evaluate(el=>el===document.activeElement),'close returns to caller');
        if(kind==='create')assert.equal(await page.locator('#new-password').inputValue(),'','close clears unsaved password');
        await opener.click();await page.keyboard.press('Escape');
        assert(await opener.evaluate(el=>el===document.activeElement),'Escape returns to caller');
      } finally {release();await page.unroute(url,handler);}
    }
  }
  assert.equal(requests.length,4,'create and edit refusal in both representative layouts');
  return {pageErrors:[]};
}
