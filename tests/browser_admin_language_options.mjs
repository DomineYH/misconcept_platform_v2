import assert from 'node:assert/strict';
import {state} from './browser_ai_connections.mjs';
import {comparisonFixture} from './s5_browser_fixtures.mjs';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  const data = state();
  data.models[1].verification_state.mentor.status = 'stale';
  data.models[0].default_options.retained_unknown = 7;
  await page.context().addCookies([{name:'ui_lang',value:'en',url:base}]);
  await page.route('**/admin/ai/state', route => route.fulfill({json:data}));
  let finish, received;
  const started = new Promise(resolve => { received = resolve; });
  await page.route('**/admin/ai/models/1/update', async route => {
    const body = route.request().postDataJSON();
    assert(!Object.hasOwn(body.default_options,'retained_unknown'));
    data.models[0].display_name = body.display_name;
    data.models[0].default_options = body.default_options;
    await new Promise(resolve => {
      finish = () => route.fulfill({json:{status:'saved'}}).then(resolve);
      received();
    });
  });
  await page.goto(`${base}/admin/ai`);
  await page.getByRole('button',{name:'Edit display name, activation and options',exact:true}).first().click();
  await page.getByRole('button',{name:'Remove undefined saved options',exact:true}).click();
  await page.locator('[data-ui-language="ko"]').click();
  assert.equal(await page.locator('html').getAttribute('lang'),'en','removing a saved option is an unsaved edit');
  await page.locator('#ai-display_name').fill('Submitted name');
  await page.getByRole('button',{name:'Save model settings',exact:true}).click();
  await started;
  await page.locator('#ai-display_name').fill('기본 모델');
  await finish();
  await page.getByText('Task completed.',{exact:true}).waitFor();
  await page.locator('[data-ui-language="ko"]').click();
  assert.equal(await page.locator('html').getAttribute('lang'),'en','reverting during save differs from the submitted value');
  assert.equal(await page.locator('#ai-display_name').inputValue(),'기본 모델');
  await page.getByRole('button',{name:'Close',exact:true}).click();
  await Promise.all([page.waitForEvent('load'),page.locator('[data-ui-language="ko"]').click()]);
  await comparisonFixture(page,['completed','completed','completed']);
  await page.context().addCookies([{name:'ui_lang',value:'en',url:base}]);
  await page.goto(`${base}/fixtures/s2/editor`);
  await page.getByRole('button',{name:'Open model comparison',exact:true}).click();
  await page.getByLabel('Comparison role',{exact:true}).selectOption('mentor');
  await page.getByLabel('Panel 1 Maximum output tokens',{exact:true}).fill('1501');
  await page.getByLabel('Comparison role',{exact:true}).selectOption('student');
  await page.locator('#comparison-question').fill('Saved student question');
  await page.getByRole('button',{name:'Run comparison',exact:true}).click();
  await page.getByText("Comparison ended. Check each panel's results.",{exact:true}).waitFor();
  await page.locator('[data-ui-language="ko"]').click();
  assert.equal(await page.locator('html').getAttribute('lang'),'en','a different role still has unsaved options after this role finishes');
  await page.getByRole('button',{name:'Reset comparison input',exact:true}).click();
  await Promise.all([page.waitForEvent('load'),page.locator('[data-ui-language="ko"]').click()]);
  return {pageErrors:[]};
}
