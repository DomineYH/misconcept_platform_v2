import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {settle} from './browser_uiux_scenario_layout.mjs';

export default async function checkScenarioCardLanguages(page) {
  const base = new URL(page.url()).origin;
  const evidence = resolve(process.env.SCENARIO_LAYOUT_EVIDENCE_DIR || 'test-results/scenario-cards');
  await mkdir(evidence, {recursive:true});
  await page.emulateMedia({reducedMotion:'reduce'});
  const names = {
    ko:['대화목록','모델 비교','수정','삭제'],
    en:['Conversations','Model comparison','Edit','Delete'],
  };
  const measurements = [];
  for (const language of ['ko','en']) {
    await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
    for (const theme of ['light','dark']) {
      await page.emulateMedia({colorScheme:theme});
      for (const width of [320,390,768,769,1024,1136,1440]) {
        await page.setViewportSize({width,height:900});
        for (const fixture of ['single','multiple','legacy-only']) {
          await page.goto(`${base}/fixtures/s6/scenarios?total=${fixture === 'single' ? 1 : 20}&uiux123=1`);
          if (fixture === 'legacy-only') await page.locator('.scenario-card').evaluateAll(cards => {
            cards.forEach((card,index) => { if (index !== 1) card.remove(); });
          });
          await settle(page);
          assert.equal(await page.locator('html').getAttribute('lang'),language);
          const layout = await page.evaluate(() => {
            const box = el => {
              const r = el.getBoundingClientRect();
              return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};
            };
            return {
              overflow:document.documentElement.scrollWidth > innerWidth,
              cards:[...document.querySelectorAll('.scenario-card')].map(card => ({
                box:box(card),
                actions:box(card.querySelector('.card-actions')),
                buttons:[...card.querySelectorAll('.card-actions .btn')].map(el => {
                  const range = document.createRange();
                  range.selectNodeContents(el);
                  return {text:el.textContent.trim(),box:box(el),textBox:box(range),clipped:el.scrollWidth > el.clientWidth || el.scrollHeight > el.clientHeight};
                }),
                deletion:card.querySelector('.delete-btn')?.dataset,
              })),
            };
          });
          const label = `${language}/${theme}/${width}/${fixture}`;
          assert(!layout.overflow, `${label}: no horizontal document scroll`);
          assert.equal(layout.cards.length,fixture === 'multiple' ? 20 : 1);
          if (fixture === 'multiple') {
            const columns = new Set(layout.cards.map(card => card.box.left)).size;
            assert(width <= 768 ? columns === 1 : columns > 1, `${label}: responsive grid preserved`);
            assert.equal(layout.cards.filter(card => card.buttons.length === 1).length,1, `${label}: legacy card present`);
          }
          for (const card of layout.cards) {
            const legacy = card.buttons.length === 1;
            assert.equal(legacy,fixture === 'legacy-only' || (fixture === 'multiple' && card === layout.cards[1]));
            assert.deepEqual(card.buttons.map(button => button.text),legacy ? names[language].slice(0,1) : names[language],label);
            if (!legacy) {
              for (const key of ['id','title','sessionCount','version']) assert(card.deletion[key], `${label}: deletion ${key} preserved`);
            }
            for (const [index,button] of card.buttons.entries()) {
              const {box,textBox} = button;
              assert(box.width >= 44 && box.height >= 44, `${label}: 44px target: ${button.text}`);
              assert(!button.clipped, `${label}: full label: ${button.text}`);
              assert(textBox.left >= box.left && textBox.right <= box.right && textBox.top >= box.top && textBox.bottom <= box.bottom, `${label}: text inside button`);
              assert(box.left >= card.actions.left && box.right <= card.actions.right+0.5 && box.top >= card.actions.top && box.bottom <= card.actions.bottom+0.5, `${label}: button inside action area`);
              assert(box.left >= card.box.left && box.right <= card.box.right+0.5, `${label}: button inside card`);
              for (const other of card.buttons.slice(index+1)) {
                assert(box.right <= other.box.left+0.5 || box.bottom <= other.box.top+0.5, `${label}: buttons do not overlap`);
              }
              if (width >= 769) assert(Math.abs(box.top-card.buttons[0].box.top) < 1, `${label}: all actions share one row`);
            }
          }
          measurements.push({language,theme,width,fixture,...layout});
          if ([320,1440].includes(width)) await page.screenshot({path:resolve(evidence,`${language}-${theme}-${width}-${fixture}.png`),fullPage:fixture !== 'multiple'});
        }
      }
    }
  }
  await writeFile(resolve(evidence,'languages.json'),JSON.stringify(measurements,null,2)+'\n');
  return {pageErrors:[]};
}
