import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';

function measure(el) {
  const rgba = value => value.match(/[\d.]+/g)?.map(Number) || [0, 0, 0, 0];
  const over = (fg, bg) => fg.slice(0, 3).map((v, i) => v * (fg[3] ?? 1) + bg[i] * (1 - (fg[3] ?? 1)));
  const background = el => {
    if (!el) return [255, 255, 255];
    return over(rgba(getComputedStyle(el).backgroundColor), background(el.parentElement));
  };
  const luminance = rgb => rgb.map(v => {v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;})
    .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
  const contrast = (a, b) => (Math.max(luminance(a), luminance(b)) + .05) / (Math.min(luminance(a), luminance(b)) + .05);
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
        const tab = page.locator(family === 'mobile' ? (selected ? '#mobile-tab-scenario' : '#mobile-tab-chat') : (selected ? '#info-tab-guide' : '#info-tab-profile'));
        for (const state of ['rest','focus','hover','hover-focus']) {
          await page.mouse.move(0,0);
          await tab.evaluate(el => el.blur());
          if (state.includes('focus')) { await page.keyboard.press('Tab'); await tab.focus(); }
          if (state.includes('hover')) await tab.hover();
          await page.evaluate(async () => {
            await Promise.all(document.getAnimations().filter(a => a.effect.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {})));
          });
          assert.equal(await tab.getAttribute('aria-selected'),String(selected));
          const row = {theme,family,selected,state,...await tab.evaluate(measure)};
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
