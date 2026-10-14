import assert from 'node:assert/strict';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.route('**/admin/comparison-conversations', route => route.fulfill({json:{items:[],next_cursor:null}}));
  for (const count of [1,2,3]) {
    await page.goto(`${base}/fixtures/uiux/comparison?panels=${count}`);
    const cards = page.locator('#conversation-rounds article');
    await cards.first().waitFor();
    assert.match(await cards.first().getByRole('heading').innerText(), /패널 1 · 학생봇 · 긴모델이름/);
    assert.equal(await cards.getByRole('status').count(),0, 'token redraws do not recreate student-panel live regions');
    await page.getByLabel('공통 교사 질문', {exact:true}).evaluate(el => { el.value = '보존할 초안'; });
    for (const width of [320,390,768,800,801,1024,1279,1280,1440]) {
      await page.setViewportSize({width,height:900});
      const boxes = await cards.evaluateAll(els => els.map(el => {
        const {x,y,width} = el.getBoundingClientRect(); return {x,y,width};
      }));
      const columns = width <= 800 ? 1 : width < 1056 ? Math.min(count,2) : count;
      assert.equal(new Set(boxes.map(b => Math.round(b.x))).size, columns, `${width}px / ${count} panels`);
      if (width > 800) assert(boxes.every(b => b.width >= 320), 'minimum 320px columns');
      if (columns > 1) assert(Math.abs(boxes[1].x - boxes[0].x - boxes[0].width - 16) < 1, '16px gap');
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no page overflow');
      assert.equal(await page.getByLabel('공통 교사 질문', {exact:true}).inputValue(), '보존할 초안');
      assert.equal(await page.getByRole('tab').count(), 0, 'new comparison has no mobile result tabs');
      assert(await cards.evaluateAll(els => els.every(el => getComputedStyle(el).overflowY === 'visible')), 'document-flow results');
    }
  }
  return {pageErrors:[]};
}
