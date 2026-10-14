import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {measure, settle} from './browser_uiux_scenario_layout.mjs';

export default async function checkUncertainSave(page) {
  const base = new URL(page.url()).origin;
  await page.emulateMedia({reducedMotion:'reduce'});
  const evidence = new URL('./uiux/evidence/123/', import.meta.url);
  await mkdir(evidence,{recursive:true});
  const measurements = [];
  async function capture(state,theme,width) {
    await settle(page);
    const row = await page.evaluate(measure);
    assert(!row.overflow);
    assert(row.text.every(item => item.ratio >= item.minimum),`${state} text contrast: ${JSON.stringify(row.text.filter(item => item.ratio < item.minimum))}`);
    assert(row.targets.every(item => item.width >= 44 && item.height >= 44),`${state} action targets`);
    measurements.push({state,theme,width,...row});
    await page.evaluate(() => scrollTo(0,0));
    await page.screenshot({path:new URL(`${state}-${theme}-${width}.png`,evidence).pathname,fullPage:true});
  }
  for (const theme of ['light','dark']) for (const width of [390,1440]) {
    await page.emulateMedia({colorScheme:theme});
    await page.setViewportSize({width,height:900});
    const submissions = [];
    let response = null;
    await page.route('**/admin/scenarios/1/update', route => {
      submissions.push(route.request().postDataJSON());
      return response ? route.fulfill(response) : route.abort('failed');
    });
    await page.goto(`${base}/fixtures/s2/editor?published=1`);
    await page.getByLabel('제목', {exact:true}).fill('현재 화면의 긴 제목');
    await page.getByRole('button', {name:'수정 후 게시', exact:true}).click();
    await page.getByText('저장 결과를 확인할 수 없습니다. 현재 화면의 입력은 유지됩니다.', {exact:true}).waitFor({timeout:3000});
    assert.equal(await page.getByLabel('제목', {exact:true}).inputValue(), '현재 화면의 긴 제목');
    assert.equal(submissions.length, 1);
    assert.equal(submissions[0].expected_version, 1);
    assert(await page.getByRole('button', {name:'수정 후 게시', exact:true}).isEnabled());
    await capture('uncertain',theme,width);
    await page.getByRole('button', {name:'5. 사후 분석', exact:true}).click();
    await page.getByLabel('발화 분류 사용').uncheck();
    response = {status:422,json:{detail:[
      {path:'analysis.rubric.0.criteria',message:'숨긴 분류 기준을 확인하세요.'},
      {path:'student.resolved_model_config',message:'공통 모델을 확인하세요.'}
    ]}};
    await page.getByRole('button', {name:'수정 후 게시', exact:true}).click();
    await page.locator('#editor-errors').waitFor();
    assert(await page.locator('#editor-errors').evaluate(el => el === document.activeElement));
    assert.equal(await page.locator('[data-errors="2"]').innerText(),'1');
    assert.equal(await page.locator('[data-errors="4"]').innerText(),'1');
    await page.getByRole('button',{name:'공통 모델을 확인하세요.',exact:true}).focus();
    await page.keyboard.press('Enter');
    assert(await page.locator('#common-model').evaluate(el => el === document.activeElement));
    await page.getByRole('button',{name:'숨긴 분류 기준을 확인하세요.',exact:true}).focus();
    await page.keyboard.press('Enter');
    assert(await page.getByLabel('발화 분류 사용').evaluate(el => el === document.activeElement), 'hidden field error focuses its controlling setting');
    assert(await page.getByLabel('발화 분류 사용').evaluate(el => parseFloat(getComputedStyle(el).outlineWidth) >= 2), 'keyboard error target has a visible focus outline');
    assert(!(await page.getByLabel('발화 분류 사용').isChecked()), 'error navigation does not enable classification');
    await capture('validation',theme,width);
    response = {status:409,json:{detail:{current_version:9}}};
    await page.getByRole('button',{name:'수정 후 게시',exact:true}).click();
    await page.locator('#editor-conflict').waitFor();
    assert(await page.locator('#editor-conflict').evaluate(el => el === document.activeElement));
    assert(await page.getByRole('button',{name:'수정 후 게시',exact:true}).isEnabled());
    await capture('conflict',theme,width);
    const requestsBeforeCancel = submissions.length;
    let reloadRequests = 0;
    const countReload = request => { if (request.isNavigationRequest()) reloadRequests++; };
    page.on('request',countReload);
    page.once('dialog',dialog => dialog.dismiss());
    await page.getByRole('button',{name:'최신 내용 다시 불러오기',exact:true}).click();
    assert.equal(submissions.length,requestsBeforeCancel);
    assert.equal(reloadRequests,0);
    page.off('request',countReload);
    await page.getByRole('button',{name:'1. 기본 정보',exact:true}).click();
    assert.equal(await page.getByLabel('제목',{exact:true}).inputValue(),'현재 화면의 긴 제목');
    assert.equal(submissions.at(-1).expected_version,1);
    assert.equal(submissions.at(-1).config.analysis.classification_enabled,false);
    assert.deepEqual(submissions.at(-1).config.student.resolved_model_config.options,submissions[0].config.student.resolved_model_config.options);
    await page.unroute('**/admin/scenarios/1/update');
  }
  await writeFile(new URL('error-measurements.json',evidence),JSON.stringify(measurements,null,2)+'\n');
  return {pageErrors:[]};
}
