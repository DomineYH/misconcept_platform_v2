import {colorMath} from './uiux_color_math.mjs';
import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';

function measure(el, colorMath) {
  const {rgba, over, background, contrast} = colorMath();
  const bg = background(el), color = over(rgba(getComputedStyle(el).color), bg);
  return {color, background:bg, ratio:contrast(color,bg), outlineWidth:getComputedStyle(el).outlineWidth};
}

export default async function check(page) {
  const base = new URL(page.url()).origin, out = new URL('./uiux/evidence/127/',import.meta.url);
  await mkdir(out,{recursive:true});
  await page.setViewportSize({width:390,height:844});
  const rows = [];
  for (const theme of ['light','dark']) {
    await page.emulateMedia({colorScheme:theme});
    await page.goto(base+'/chat');
    await page.getByRole('tab',{name:'상황 정보',exact:true}).click();
    for (const family of ['mobile','info']) {
      for (const selected of [true,false]) {
        const tab = page.locator(family === 'mobile' ? (selected ? '#mobile-tab-scenario' : '#mobile-tab-chat') : (selected ? '#info-tab-situation' : '#info-tab-profile'));
        for (const state of ['rest','focus','hover','hover-focus']) {
          await page.mouse.move(0,0);
          await tab.evaluate(el => el.blur());
          if (state.includes('focus')) { await page.keyboard.press('Tab'); await tab.focus(); }
          if (state.includes('hover')) await tab.hover();
          await page.evaluate(async () => {
            await Promise.all(document.getAnimations().filter(a => a.effect.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {})));
          });
          assert.equal(await tab.getAttribute('aria-selected'),String(selected));
          const row = {theme,family,selected,state,...await tab.evaluate(new Function('el', `return (${measure})(el, (${colorMath}));`))};
          rows.push(row);
          if (state.includes('focus')) assert(parseFloat(row.outlineWidth) >= 2);
          if (state === 'hover-focus') await page.screenshot({path:new URL(`tabs-${family}-${selected ? 'selected' : 'unselected'}-${theme}.png`,out).pathname});
        }
      }
    }
  }
  await writeFile(new URL('tab-contrast.json',out),JSON.stringify(rows,null,2)+'\n');
  assert.equal(rows.length,32);
  assert(rows.every(r => r.ratio >= 4.5),`composed tab contrast: ${JSON.stringify(rows.filter(r => r.ratio < 4.5))}`);
  return {pageErrors:[]};
}
