import assert from 'node:assert/strict';
import {state} from './browser_ai_connections.mjs';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.route('**/admin/ai/state', route => route.fulfill({json:state()}));
  const screens = [
    ['/fixtures/s5/menu','Admin dashboard'],
    ['/fixtures/s6/scenarios?total=41&uiux123=1','Scenario management'],
    ['/fixtures/s6/users?total=41','User management'],
    ['/fixtures/uiux/admin/groups','Group management'],
    ['/fixtures/uiux/admin/about','About management'],
    ['/fixtures/uiux/admin/sessions','Dialogue sessions'],
    ['/fixtures/uiux/admin/user_conversations','User conversations'],
    ['/fixtures/uiux/admin/analysis','Analysis management'],
    ['/admin/api-usage','API usage'],
    ['/admin/ai','AI connections and models'],
    ['/fixtures/s2/editor','Scenario editor'],
    ['/fixtures/uiux/comparison','Scenario model comparison'],
  ];
  for (const language of ['ko','en']) {
    await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
    for (const colorScheme of ['light','dark']) {
      await page.emulateMedia({colorScheme});
      for (const width of [320,390,768,1024,1440]) {
        await page.setViewportSize({width,height:900});
        for (const [path,title] of screens) {
          await page.goto(base + path);
          assert.equal(await page.locator('html').getAttribute('lang'),language,path);
          if (language === 'en') assert.equal((await page.locator('main h1').first().innerText()).trim(),title,path);
          assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),`${language}/${colorScheme}/${width}/${path}: page overflow`);
          const create = page.locator('#open-create-modal');
          if (await create.count()) {
            await create.click();
            const modal = await page.locator('#create-modal .modal-container').boundingBox();
            assert(modal.x >= 0 && modal.x + modal.width <= width + 1,`${path}: modal fits`);
            await page.keyboard.press('Escape');
          }
        }
      }
    }
  }
  return {pageErrors:[]};
}
