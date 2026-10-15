import assert from 'node:assert/strict';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.context().addCookies([{name:'ui_lang',value:'en',url:base}]);
  await page.route('**/admin/comparisons', route => {
    if (route.request().method() === 'GET') return route.fulfill({json:{items:[],next_cursor:null}});
    const body = route.request().postDataJSON();
    return route.fulfill({json:{...body,comparison_run_id:'reordered-result',panels:body.panels.map(panel => ({
      ...panel,status:'completed',text:'Original response',run_id:`run-${panel.panel_id}`,
      model_config:Object.fromEntries(Object.entries(panel.model_config).reverse()),
    }))}});
  });
  await page.goto(`${base}/fixtures/s2/editor`);
  await page.getByRole('button',{name:'Open model comparison',exact:true}).click();
  await page.locator('#comparison-question').fill('Stored question');
  await page.getByRole('button',{name:'Run comparison',exact:true}).click();
  await page.getByText("Comparison ended. Check each panel's results.",{exact:true}).waitFor();
  await page.locator('[data-ui-language="ko"]').click();
  assert.equal(await page.locator('html').getAttribute('lang'),'ko','equivalent stored model settings release the guard regardless of JSON key order');
  return {pageErrors:[]};
}
