import assert from 'node:assert/strict';
import {mkdir,writeFile} from 'node:fs/promises';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  page.on('dialog', dialog => dialog.accept());
  let writes = 0, fail = false;
  await page.route('**/sessions/1/end', route => { writes++; return route.fulfill({status:202,json:{latest_run:{status:'running'}}}); });
  await page.route('**/sessions/1/analysis_modal', async route => {
    if (fail) return route.fulfill({status:500,body:'PRIVATE ERROR'});
    const html = await (await page.request.get(`${base}/fixtures/s4/modal?state=preserved`)).text();
    return route.fulfill({contentType:'text/html',body:html});
  });
  await page.goto(`${base}/chat`);
  const opener = page.locator('#end-session-btn');
  await opener.click();
  const dialog = page.getByRole('dialog', {name:'세션 분석 결과', exact:true});
  await dialog.waitFor({state:'visible', timeout:3000});
  const close = dialog.getByRole('button', {name:'닫기',exact:true}).first();
  await close.waitFor();
  await dialog.getByRole('heading', {name:'채택된 보고서: 정상 분석',exact:true}).waitFor();
  assert(await close.evaluate(el => el === document.activeElement), 'reading modal focuses close');
  await page.keyboard.press('Shift+Tab');
  assert(await dialog.evaluate(el => el.contains(document.activeElement)), 'focus stays inside');
  await page.keyboard.press('Escape');
  assert(await opener.evaluate(el => el === document.activeElement), 'Escape returns to opener');
  assert.equal(await page.locator('.split-layout').evaluate(el => el.inert), false);
  fail = true;
  await opener.click();
  await dialog.getByText('분석 결과를 불러오지 못했습니다. 다시 불러오거나 닫을 수 있습니다.', {exact:true}).waitFor();
  assert(!(await dialog.innerText()).includes('PRIVATE ERROR'));
  assert.equal(writes,1,'view failure does not create another analysis');
  fail = false;
  await dialog.getByRole('button', {name:'결과 다시 불러오기',exact:true}).click();
  await dialog.getByRole('heading', {name:'채택된 보고서: 정상 분석',exact:true}).waitFor();
  await close.click();
  assert(await opener.evaluate(el => el === document.activeElement));
  assert.equal(writes,1);
  const out = new URL('./uiux/evidence/122/', import.meta.url);
  await mkdir(out,{recursive:true});
  const measurements = [];
  await page.emulateMedia({reducedMotion:'reduce'});
  for (const theme of ['light','dark']) {
    await page.emulateMedia({colorScheme:theme});
    for (const width of [390,1440]) {
      await page.setViewportSize({width,height:900});
      if (width <= 768) await page.getByRole('tab',{name:'상황 정보',exact:true}).click();
      for (const state of ['preserved','failed']) {
        fail = state === 'failed';
        await opener.click();
        if (fail) await dialog.getByText('분석 결과를 불러오지 못했습니다. 다시 불러오거나 닫을 수 있습니다.', {exact:true}).waitFor();
        else await dialog.getByRole('heading',{name:'채택된 보고서: 정상 분석',exact:true}).waitFor();
        await page.evaluate(async () => { await Promise.all(document.getAnimations().filter(a => a.effect.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {}))); });
        const box = await page.locator('#analysis-modal-container').boundingBox();
        assert(box.width <= 900 && box.height <= 810 && box.x >= 16 && box.x+box.width <= width-16, 'bounded modal fits viewport');
        measurements.push({theme,width,state,box});
        await page.screenshot({path:new URL(`modal-${state}-${theme}-${width}.png`,out).pathname});
        await page.keyboard.press('Escape');
      }
    }
  }
  assert.equal(writes,1,'capture and view retries never regenerate');
  await writeFile(new URL('modal-measurements.json',out),JSON.stringify(measurements,null,2)+'\n');
  await page.route('**/sessions/1/analysis_modal', route => route.fulfill({status:401, headers:{'HX-Trigger':'{"auth-expired":{"redirect_url":"/login"}}'}, body:'Login required'}));
  await opener.click();
  await page.locator('#session-login-btn').waitFor({state:'visible'});
  assert(await dialog.isHidden(), 'expired modal restores access to login guidance');
  assert.equal(await page.locator('.split-layout').evaluate(el => el.inert), false);
  assert.equal(writes,1);
  return {pageErrors:[]};
}
