import assert from 'node:assert/strict';
import {state} from './browser_ai_connections.mjs';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.route('**/admin/ai/state', route => route.fulfill({json:state()}));
  for (const width of [320, 390, 767, 768, 769, 1024, 1440]) {
    await page.setViewportSize({width, height:900});
    await page.goto(`${base}/admin/ai`);
    await page.locator('[data-provider="google"]').waitFor();
    const cards = await page.locator('[data-provider]').evaluateAll(els => els.map(el => {
      const rect = el.getBoundingClientRect();
      return {x:rect.x, y:rect.y};
    }));
    if (width <= 768) assert.equal(cards[0].x, cards[1].x, `${width}px provider cards use one column`);
    else assert.equal(cards[0].y, cards[1].y, `${width}px provider cards share a row`);
    await page.getByRole('button', {name:'작성 기본값·호출 설정 수정'}).click();
    const editor = await page.locator('#ai-editor').boundingBox();
    assert(editor.width <= 600, 'single form is at most 600px');
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'page fits viewport');
  }
  return {pageErrors:[]};
}
