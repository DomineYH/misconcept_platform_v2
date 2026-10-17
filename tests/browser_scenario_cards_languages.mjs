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
      for (const width of [320,390,767,768,769,1024,1136,1180,1440]) {
        await page.setViewportSize({width,height:900});
        let column;
        const fixtures = [
          {name:'multiple',total:20,count:20},
          {name:'single',total:1,count:1},
          {name:'last-page-21',total:21,page:2,count:1},
          {name:'last-page-41',total:41,page:3,count:1},
          {name:'two',total:2,count:2},
          {name:'four',total:4,count:4},
          {name:'filtered-single',search:'시나리오 2',count:1,id:'2'},
          {name:'restored-single',count:4},
          {name:'legacy-only',search:'시나리오 3',count:1,id:'3'},
          {name:'restored-legacy',count:4},
          {name:'filtered-select',publication:'published',count:1,id:'4'},
          {name:'restored-select',count:4},
          {name:'no-results',search:'no_matching_scenario_166',count:0},
          {name:'restored-empty',count:4},
        ];
        for (const {name:fixture,total,page:currentPage = 1,search = '',publication = '',count,id} of fixtures) {
          if (total) await page.goto(`${base}/fixtures/s6/scenarios?total=${total}&page=${currentPage}&uiux123=1`);
          else {
            await page.locator('#search-input').fill(search);
            await page.locator('#filter-publication').selectOption(publication);
          }
          await settle(page);
          assert.equal(await page.locator('html').getAttribute('lang'),language);
          const layout = await page.evaluate(() => {
            const box = el => {
              const r = el.getBoundingClientRect();
              return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};
            };
            return {
              overflow:document.documentElement.scrollWidth > innerWidth,
              domCount:document.querySelectorAll('.scenario-card').length,
              hiddenCount:document.querySelectorAll('.scenario-card[style*="display: none"]').length,
              emptyVisible:!document.querySelector('#scenario-filter-empty').hidden,
              cards:[...document.querySelectorAll('.scenario-card')].filter(card => card.getClientRects().length).map(card => ({
                id:card.dataset.scenarioId,
                box:box(card),
                body:box(card.querySelector('.card-body')),
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
          assert.equal(layout.cards.length,count,`${label}: visible result count`);
          assert.equal(layout.domCount,total ? count : 4,`${label}: hidden siblings stay in the DOM`);
          assert.equal(layout.hiddenCount,layout.domCount-count,`${label}: only matching cards visible`);
          assert.equal(layout.emptyVisible,count === 0,`${label}: no-results status`);
          if (fixture === 'multiple') column = layout.cards[0].box;
          if (count) assert(Math.abs(layout.cards[0].box.left-column.left) < 1, `${label}: first visible card in first column`);
          if (id) assert.equal(layout.cards[0].id,id,`${label}: expected filtered card`);
          if (fixture === 'multiple') {
            const columns = new Set(layout.cards.map(card => card.box.left)).size;
            assert(width <= 768 ? columns === 1 : columns > 1, `${label}: responsive grid preserved`);
            assert.equal(layout.cards.filter(card => card.buttons.length === 1).length,1, `${label}: legacy card present`);
          }
          for (const card of layout.cards) {
            const legacy = card.buttons.length === 1;
            assert.equal(legacy,total ? count > 1 && card === layout.cards[1] : card.id === '3', `${label}: legacy actions preserved`);
            assert(Math.abs(card.box.width-column.width) < 1, `${label}: same width as a populated grid column`);
            assert(card.actions.top >= card.body.bottom, `${label}: vertical information/actions`);
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
