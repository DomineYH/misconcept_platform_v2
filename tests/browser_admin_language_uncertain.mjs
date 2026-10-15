import assert from 'node:assert/strict';
import {comparisonFixture} from './s5_browser_fixtures.mjs';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  await comparisonFixture(page,['completed','completed','completed']);
  await page.context().addCookies([{name:'ui_lang',value:'en',url:base}]);
  await page.goto(`${base}/fixtures/s2/editor`);
  await page.getByRole('button',{name:'Open model comparison',exact:true}).click();
  await page.locator('#comparison-question').fill('Previously saved question');
  await page.getByRole('button',{name:'Run comparison',exact:true}).click();
  await page.getByText("Comparison ended. Check each panel's results.",{exact:true}).waitFor();
  await page.route('**/admin/comparisons', route => route.request().method() === 'POST' ? route.abort('failed') : route.fallback());
  await page.getByRole('button',{name:'Run comparison',exact:true}).click();
  await page.getByText('Connection interrupted. Refresh the state to check saved results. Your input is preserved.',{exact:true}).waitFor();
  const refreshed = page.waitForResponse(response => response.request().method() === 'GET' && response.url().includes('/admin/comparisons/'));
  await page.getByRole('button',{name:'Refresh state',exact:true}).click();
  await refreshed;
  await page.waitForTimeout(100);
  await page.locator('[data-ui-language="ko"]').click();
  assert.equal(await page.locator('html').getAttribute('lang'),'en','an old run cannot confirm a new unacknowledged admission');
  assert.match(await page.locator('#language-notice').innerText(),/Wait until/);
  let snapshot;
  await page.route('**/fixtures/uiux/comparison?panels=2', async route => {
    const response = await route.fetch();
    const html = await response.text();
    await route.fulfill({response,body:html.replace(/(<script[^>]*id="comparison-conversation-data"[^>]*>)([\s\S]*?)(<\/script>)/, (_,open,json,close) => {
      const data = JSON.parse(json);
      snapshot = data.snapshot;
      Object.assign(snapshot,{phase:'open',children:[],pairs:[],pending_auto:false,allowed_actions:{turn:true},version:1});
      return open + JSON.stringify(data) + close;
    })});
  });
  await page.route('**/admin/comparison-conversations/visual-only**', route => route.request().method() === 'POST'
    ? route.abort('failed') : route.fulfill({json:snapshot}));
  await page.goto(`${base}/fixtures/uiux/comparison?panels=2`);
  await page.locator('#conversation-question').fill('Unacknowledged question');
  await page.getByRole('button',{name:'Send question',exact:true}).click();
  await page.getByRole('button',{name:'Send question',exact:true}).waitFor({state:'visible'});
  await page.waitForFunction(() => !document.querySelector('#conversation-question').disabled);
  await page.locator('#conversation-question').fill('');
  await page.locator('[data-ui-language="ko"]').click();
  assert.equal(await page.locator('html').getAttribute('lang'),'en','an unchanged parent version cannot confirm a lost mutation');
  assert.match(await page.locator('#language-notice').innerText(),/Wait until/);
  snapshot.version = 2;
  const oldRefresh = await page.getByRole('button',{name:'Refresh state',exact:true}).elementHandle();
  await oldRefresh.click();
  await oldRefresh.waitForElementState('hidden');
  await Promise.all([page.waitForEvent('load'),page.locator('[data-ui-language="ko"]').click()]);
  assert.equal(await page.locator('html').getAttribute('lang'),'ko','advanced terminal state resolves the unknown mutation');
  return {pageErrors:[]};
}
