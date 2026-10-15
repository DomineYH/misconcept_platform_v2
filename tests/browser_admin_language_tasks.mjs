import assert from 'node:assert/strict';
import {state} from './browser_ai_connections.mjs';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  const data = state();
  data.models[1].verification_state.mentor.status = 'stale';
  await page.context().addCookies([{name:'ui_lang',value:'en',url:base}]);
  let expectedLanguage = 'en';
  await page.route('**/admin/ai/state', route => {
    assert.equal(route.request().headers()['x-ui-language'],expectedLanguage);
    return route.fulfill({json:data});
  });
  let finish;
  await page.route('**/admin/ai/settings/update', async route => {
    await new Promise(resolve => { finish = () => route.fulfill({status:409,json:{detail:{code:'version_conflict'}}}).then(resolve); });
  });
  await page.goto(`${base}/admin/ai`);
  await page.getByRole('button',{name:'Edit authoring defaults and call settings',exact:true}).click();
  await page.locator('#ai-limit_total').fill('9');
  await page.locator('[data-ui-language="ko"]').click();
  assert.equal(await page.locator('html').getAttribute('lang'),'en');
  assert.match(await page.locator('#language-notice').innerText(),/Save or clear/);
  await page.getByRole('button',{name:'Save settings',exact:true}).click();
  await page.locator('[data-ui-language="ko"]').click();
  assert.match(await page.locator('#language-notice').innerText(),/Wait until/);
  await finish();
  await page.getByText('Task failed.',{exact:true}).waitFor();
  await page.locator('[data-ui-language="ko"]').click();
  assert.equal(await page.locator('#ai-limit_total').inputValue(),'9');
  assert.match(await page.locator('#language-notice').innerText(),/Save or clear/);
  await page.getByRole('button',{name:'Close',exact:true}).click();
  expectedLanguage = 'ko';
  await Promise.all([page.waitForEvent('load'),page.locator('[data-ui-language="ko"]').click()]);
  await page.context().addCookies([{name:'ui_lang',value:'en',url:base}]);
  await page.goto(`${base}/fixtures/s2/editor`);
  await page.getByRole('button',{name:'Open model comparison',exact:true}).click();
  await page.locator('#comparison-question').fill('Unsubmitted comparison question');
  await page.locator('[data-ui-language="ko"]').click();
  assert.equal(await page.locator('html').getAttribute('lang'),'en');
  assert.match(await page.locator('#language-notice').innerText(),/Save or clear/);
  await page.getByRole('button',{name:'Reset comparison input',exact:true}).click();
  await Promise.all([page.waitForEvent('load'),page.locator('[data-ui-language="ko"]').click()]);
  await page.context().addCookies([{name:'ui_lang',value:'en',url:base}]);
  let finishStream, received;
  const started = new Promise(resolve => { received = resolve; });
  await page.route('**/admin/comparisons', async route => {
    if (route.request().method() === 'GET') return route.fulfill({json:{items:[],next_cursor:null}});
    assert.equal(route.request().headers()['x-ui-language'],'en');
    const frames = [];
    for (const panel of route.request().postDataJSON().panels) {
      const identity = {comparison_run_id:'language-stream',panel_id:panel.panel_id,run_id:'language-' + panel.panel_id};
      frames.push('event: run.accepted\ndata: ' + JSON.stringify({...identity,seq:0}) + '\n\n');
      const failed = panel.panel_id === '2';
      frames.push('event: ' + (failed ? 'run.failed' : 'output.completed') + '\ndata: ' + JSON.stringify({...identity,seq:1,error_code:failed ? 'timeout_total' : null,text:failed ? '' : '원문 AI 응답',outcome_status:failed ? 'failed' : 'completed'}) + '\n\n');
    }
    await new Promise(resolve => {
      finishStream = () => route.fulfill({contentType:'text/event-stream',body:frames.join('')}).then(resolve);
      received();
    });
  });
  await page.goto(`${base}/fixtures/s2/editor`);
  await page.getByRole('button',{name:'Open model comparison',exact:true}).click();
  await page.locator('#comparison-question').fill('원문 교사 질문');
  // A sibling tab may change the cookie; this document still starts in English.
  await page.context().addCookies([{name:'ui_lang',value:'ko',url:base}]);
  await page.getByRole('button',{name:'Run comparison',exact:true}).click();
  await started;
  await page.locator('[data-ui-language="ko"]').click();
  assert.match(await page.locator('#language-notice').innerText(),/Wait until/);
  await finishStream();
  await page.getByText('The response timed out. No validated partial results',{exact:true}).waitFor();
  assert.equal(await page.locator('html').getAttribute('lang'),'en');
  assert.equal(await page.getByText('원문 AI 응답',{exact:true}).count(),2);
  await Promise.all([page.waitForEvent('load'),page.locator('[data-ui-language="ko"]').click()]);
  return {pageErrors:[]};
}
