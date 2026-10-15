import assert from 'node:assert/strict';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  let cases = 0;
  for (const language of ['ko','en']) {
    await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
    for (const colorScheme of ['light','dark']) {
      await page.emulateMedia({colorScheme});
      for (const width of [769,1024,1440]) {
        await page.setViewportSize({width,height:900});
        for (const [path, input] of [
          ['/login','#username'], ['/chat','#teacher-input'],
          ['/fixtures/uiux/admin/groups','#new-name'],
        ]) {
          await page.goto(base + path);
          if (path.includes('groups')) await page.locator('#open-create-modal').click();
          await page.locator(input).fill('원문 입력');
          await page.locator(`[data-ui-language="${language === 'ko' ? 'en' : 'ko'}"]`).evaluate(button => button.click());
          await page.locator('#language-notice').waitFor({state:'visible'});
          if (path.includes('groups')) await page.keyboard.press('Escape');
          await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
          const bounds = await page.evaluate(() => ({
            notice:document.querySelector('.nav-notices').getBoundingClientRect().bottom,
            content:document.querySelector('main').getBoundingClientRect().top,
            header:document.querySelector('body > header').getBoundingClientRect().height,
            overflow:document.documentElement.scrollWidth > innerWidth,
          }));
          assert(bounds.content >= bounds.notice - 1, `${language}/${colorScheme}/${width}/${path}: notice obscures content ${JSON.stringify(bounds)}`);
          assert.equal(bounds.header,60);
          assert.equal(bounds.overflow,false);
          if (path === '/chat') {
            const composer = await page.locator('#teacher-form').boundingBox();
            assert(composer.y + composer.height <= 900, 'notices leave the composer in the viewport');
          }
          if (path.includes('groups')) await page.locator('#open-create-modal').click();
          cases++;
        }
      }
    }
  }
  console.log(`Measured ${cases} notice layouts: both languages/themes, 769/1024/1440px, login/teacher/admin`);
  return {pageErrors:[]};
}
