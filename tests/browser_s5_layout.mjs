import assert from 'node:assert/strict';

export default async function checkComparisonEntryAndLayout(page) {
  page.setDefaultTimeout(3000);
  const base = new URL(page.url()).origin;
  await page.goto(`${base}/fixtures/s5/menu`);
  await page.getByRole('link',{name:'모델 비교 동일 맥락으로 모델·옵션 시험'}).click();
  assert.equal(await page.getByRole('link',{name:'모델 비교',exact:true}).getAttribute('href'), '/admin/model-comparison?scenario_id=1');
  assert.equal(await page.getByRole('link',{name:'전체 모델 비교',exact:true}).getAttribute('href'), '/admin/model-comparison');
  await page.goto(`${base}/fixtures/s2/editor?new=1`);
  await page.getByRole('button',{name:'모델 비교 열기',exact:true}).click();
  await page.getByRole('region',{name:'동일 맥락 모델 비교',exact:true}).waitFor();
  assert.match(await page.locator('#scenario-comparison').innerText(), /새 작성 · 원본 없음/);
  for (const width of [1280,1000,390]) {
    await page.setViewportSize({width,height:900});
    const panels = page.locator('[data-comparison-panel]');
    if (width === 1280) {
      const boxes = await Promise.all([0,1,2].map(i => panels.nth(i).boundingBox()));
      assert.equal(boxes[0].y,boxes[2].y,'three desktop columns');
      assert(boxes[0].x < boxes[1].x && boxes[1].x < boxes[2].x);
    } else if (width === 1000) {
      assert(await page.locator('.comparison-grid').evaluate(el => el.scrollWidth > el.clientWidth),'medium horizontal scroll');
    } else {
      const tabs = page.getByRole('tablist',{name:'비교 패널'});
      await tabs.getByRole('tab',{name:'패널 1',exact:true}).focus();
      await page.keyboard.press('ArrowRight');
      assert.equal(await tabs.getByRole('tab',{name:'패널 2',exact:true}).getAttribute('aria-selected'),'true');
      assert(await panels.nth(1).isVisible());
      assert(!(await panels.nth(0).isVisible()));
      await page.keyboard.press('End');
      assert(await panels.nth(2).isVisible());
    }
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),'no page overflow');
  }
  for (const [count,text] of [[0,'미설정'],[1,'단독 테스트'],[2,'2패널']]) {
    await page.goto(`${base}/fixtures/s2/editor?ready=${count}`);
    await page.getByRole('button',{name:'모델 비교 열기',exact:true}).click();
    assert.equal(await page.locator('[data-comparison-panel]').count(),count);
    assert.match(await page.locator('#scenario-comparison').innerText(),new RegExp(text));
    assert.equal(await page.getByRole('button',{name:'비교 실행',exact:true}).isDisabled(),count === 0);
  }
  return {pageErrors:[]};
}
