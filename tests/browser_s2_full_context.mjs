import assert from 'node:assert/strict';

export default async function checkFullContextEditor(page) {
  const base = new URL(page.url()).origin;
  const writes = [];
  await page.route('**/admin/scenarios/1/update', route => {
    writes.push(route.request().postDataJSON());
    return route.fulfill({status:200,json:{id:1,version:2,status:'draft'}});
  });
  await page.route('**/admin/scenarios', route => {
    writes.push(route.request().postDataJSON());
    return route.fulfill({status:200,json:{id:2,version:1,status:'draft'}});
  });
  for (const isNew of [false, true]) {
    await page.route('**/fixtures/s2/editor*', async route => {
      const response = await route.fetch();
      const pattern = /(<script id="scenario-editor-data" type="application\/json">)([\s\S]*?)(<\/script>)/;
      let html = await response.text();
      const data = JSON.parse(html.match(pattern)[2]);
      data.config.runtime = isNew ? {} : {context_turn_limit:7};
      html = html.replace(pattern, (_, before, json, after) => `${before}${JSON.stringify(data)}${after}`);
      await route.fulfill({response,body:html});
    });
    await page.goto(`${base}/fixtures/s2/editor${isNew ? '?new=1' : ''}`);
    assert.equal(await page.locator('[data-field="runtime.context_turn_limit"]').count(), 0);
    await page.getByRole('button',{name:'초안으로 저장',exact:true}).click();
    await page.locator('#editor-status[data-state="success"]').waitFor();
    assert.deepEqual(writes.at(-1).config.runtime, isNew ? {} : {context_turn_limit:7});
    await page.unroute('**/fixtures/s2/editor*');
  }
  return {pageErrors:[]};
}
