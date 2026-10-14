import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';

export default async function check(page) {
  const base = new URL(page.url()).origin, out = new URL('./uiux/evidence/127/',import.meta.url);
  await mkdir(out,{recursive:true});
  const requests = [];
  page.on('request',request => requests.push(request.url()));
  for (const theme of ['light','dark']) {
    await page.emulateMedia({colorScheme:theme});
    await page.setViewportSize({width:390,height:844});
    const url = base+'/fixtures/s6/users?page=2';
    await page.goto(url);
    const rows = page.locator('#users-table tbody tr[data-user-id]');
    assert.equal(await rows.count(),20);
    const original = await rows.allTextContents();
    const requestCount = requests.length;
    const status = page.getByRole('status').filter({hasText:'현재 페이지에서 검색·필터에 맞는 사용자가 없습니다.'});
    for (const [control,value] of [['#search-input','definitely_no_matching_user_120'],['#filter-role','admin'],['#filter-group','1']]) {
      if (control === '#filter-group') await page.locator('#filter-role').selectOption('admin');
      if (control === '#search-input') await page.locator(control).fill(value);
      else await page.locator(control).selectOption(value);
      assert.equal(await rows.filter({visible:true}).count(),0);
      await status.waitFor({state:'visible'});
      assert.equal(await page.locator(control).inputValue(),value);
      assert.equal(page.url(),url);
      await page.screenshot({path:new URL(`users-filter-${control.slice(1)}-${theme}.png`,out).pathname});
      if (control === '#search-input') await page.locator(control).fill('');
      else await page.locator(control).selectOption('');
      if (control === '#filter-group') await page.locator('#filter-role').selectOption('');
      assert.equal(await rows.filter({visible:true}).count(),20);
      assert.equal(await status.isVisible(),false);
      assert.deepEqual(await rows.allTextContents(),original);
    }
    assert.equal(requests.length,requestCount,'current-page filters make no server request');
    await page.goto(base+'/fixtures/s6/users?total=0');
    await page.locator('#search-input').fill('unmatched');
    assert.equal(await status.isVisible(),false,'server-empty dataset keeps its distinct message');
    await page.getByText('사용자가 없습니다.',{exact:true}).waitFor();
  }
  await writeFile(new URL('users-filter.json',out),JSON.stringify({themes:['light','dark'],filters:['search','role','group'],populatedRows:20,noServerSearch:true,serverEmptyDistinct:true},null,2)+'\n');
  return {pageErrors:[]};
}
