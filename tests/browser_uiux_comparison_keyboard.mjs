import assert from 'node:assert/strict';
import {openComparison} from './s5_browser_fixtures.mjs';

export default async function check(page) {
  page.setDefaultTimeout(5000);
  await openComparison(page);
  const question = page.getByLabel('학생 현재 질문',{exact:true});
  await question.fill('탭과 크기 변경 뒤에도 남는 입력');
  await page.getByLabel('패널 3 최대 출력 토큰').fill('777');
  await question.evaluate(el => { window.originalQuestion = el; el.setSelectionRange(3,6); });
  await page.setViewportSize({width:390,height:900});
  const tabs = page.getByRole('tablist',{name:'비교 패널'});
  await tabs.getByRole('tab',{name:'패널 1',exact:true}).focus();
  for (const [key,panel] of [['ArrowRight',2],['End',3],['ArrowLeft',2],['Home',1]]) {
    await page.keyboard.press(key);
    assert.equal(await tabs.getByRole('tab',{name:`패널 ${panel}`,exact:true}).getAttribute('aria-selected'),'true');
    assert(await tabs.getByRole('tab',{name:`패널 ${panel}`,exact:true}).evaluate(el => el === document.activeElement));
    assert(await page.locator(`[data-comparison-panel="${panel}"]`).isVisible());
  }
  await page.keyboard.press('End');
  assert.equal(await page.getByLabel('패널 3 최대 출력 토큰').inputValue(),'777');
  for (const width of [768,800,801,1024,1279,1280,390]) {
    await page.setViewportSize({width,height:900});
    assert.equal(await question.inputValue(),'탭과 크기 변경 뒤에도 남는 입력');
    assert.equal(await question.evaluate(el => el.selectionStart),3);
    assert(await question.evaluate(el => el === window.originalQuestion));
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  }
  await page.setViewportSize({width:768,height:900});
  const grid = page.getByRole('region',{name:'비교 패널 결과'});
  await grid.focus();
  await page.keyboard.press('ArrowRight');
  await page.waitForFunction(() => document.querySelector('.comparison-grid').scrollLeft > 0);
  const focus = await grid.evaluate(el => {
    const s = getComputedStyle(el); return {style:s.outlineStyle,width:parseFloat(s.outlineWidth),offset:parseFloat(s.outlineOffset)};
  });
  assert.equal(focus.style,'solid');
  assert(focus.width >= 2 && focus.offset >= 2, 'visible keyboard focus');
  return {pageErrors:[]};
}
