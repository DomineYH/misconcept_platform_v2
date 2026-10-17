import assert from 'node:assert/strict';
import {settle} from './browser_uiux_scenario_layout.mjs';

export default async function checkSharedGrids(page) {
  const base = new URL(page.url()).origin;
  await page.emulateMedia({reducedMotion:'reduce'});
  for (const theme of ['light','dark']) {
    await page.emulateMedia({colorScheme:theme});
    for (const width of [320,390,767,768,769,1024,1180,1440]) {
      await page.setViewportSize({width,height:900});
      for (const path of ['/scenarios','/fixtures/uiux/admin/groups']) {
        await page.goto(`${base}${path}`);
        for (const count of [1,4]) {
          if (count === 4) await page.locator('.scenarios-grid').evaluate(grid => {
            const card = grid.querySelector('.scenario-card');
            for (let index = 1; index < 4; index++) grid.append(card.cloneNode(true));
          });
          await settle(page);
          const layout = await page.locator('.scenarios-grid').evaluate(grid => {
            const rect = grid.getBoundingClientRect();
            return {
              width:rect.width,left:rect.left,gap:parseFloat(getComputedStyle(grid).columnGap),
              cards:[...grid.children].map(card => {
                const box = card.getBoundingClientRect();
                return {width:box.width,left:box.left,right:box.right};
              }),
              overflow:document.documentElement.scrollWidth > innerWidth,
            };
          });
          const label = `${theme}/${width}/${path}/${count}`;
          assert.equal(layout.cards.length,count,label);
          const columns = width <= 768 ? 1 : Math.min(count,Math.floor((layout.width+layout.gap)/(280+layout.gap)));
          const expected = (layout.width-(columns-1)*layout.gap)/columns;
          assert(Math.abs(layout.cards[0].left-layout.left) < 1,`${label}: first column alignment`);
          assert(layout.cards.every(card => Math.abs(card.width-expected) < 1),`${label}: shared auto-fit column widths preserved`);
          assert(!layout.overflow,`${label}: no document overflow`);
        }
      }
    }
  }
  return {pageErrors:[]};
}
