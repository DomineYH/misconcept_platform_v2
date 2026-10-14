import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {comparisonFixture, openComparison} from './s5_browser_fixtures.mjs';

function measure() {
  const rgba = value => value.match(/[\d.]+/g)?.map(Number) || [0,0,0,0];
  const over = (fg,bg) => fg.slice(0,3).map((v,i) => v * (fg[3] ?? 1) + bg[i] * (1 - (fg[3] ?? 1)));
  const background = el => !el ? [255,255,255] : over(rgba(getComputedStyle(el).backgroundColor),background(el.parentElement));
  const luminance = rgb => rgb.map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v+.055)/1.055)**2.4; })
    .reduce((sum,v,i) => sum + v * [.2126,.7152,.0722][i],0);
  const contrast = (a,b) => (Math.max(luminance(a),luminance(b))+.05)/(Math.min(luminance(a),luminance(b))+.05);
  const text = [], targets = [], borders = [];
  const root = document.querySelector('.comparison-page, #scenario-comparison');
  for (const el of root.querySelectorAll('*')) {
    if (!el.getClientRects().length || ['SCRIPT','STYLE','OPTION'].includes(el.tagName)) continue;
    const s = getComputedStyle(el), bg = background(el), box = el.getBoundingClientRect();
    const own = [...el.childNodes].filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent.trim()).join(' ').trim();
    if (own) {
      const large = parseFloat(s.fontSize) >= 24 || (parseFloat(s.fontSize) >= 18.66 && Number(s.fontWeight) >= 700);
      const color = over(rgba(s.color),bg);
      text.push({text:own.slice(0,70),color,background:bg,ratio:contrast(color,bg),minimum:large ? 3 : 4.5});
    }
    if (el.matches('button, a.btn, summary')) targets.push({text:el.textContent.slice(0,50),width:box.width,height:box.height});
    if (el.matches('input:not([type="checkbox"]), textarea, select')) {
      const color = rgba(s.borderTopColor).slice(0,3);
      borders.push({name:el.id,inside:contrast(color,bg),outside:contrast(color,background(el.parentElement))});
    }
  }
  const grid = root.querySelector('.conversation-panels, .comparison-grid');
  const panels = [...grid.children].filter(el => el.getClientRects().length).map(el => {
    const {x,y,width,height} = el.getBoundingClientRect(); return {x,y,width,height,overflowY:getComputedStyle(el).overflowY};
  });
  return {overflow:document.documentElement.scrollWidth > innerWidth,text,targets,borders,panels,internalHorizontalScroll:grid.scrollWidth > grid.clientWidth};
}

export default async function check(page) {
  page.setDefaultTimeout(5000);
  const base = new URL(page.url()).origin;
  const out = new URL('./uiux/evidence/124/',import.meta.url);
  await mkdir(out,{recursive:true});
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.route('**/admin/comparison-conversations', route => route.fulfill({json:{items:[],next_cursor:null}}));
  const rows = [];
  for (const theme of ['light','dark']) {
    await page.emulateMedia({colorScheme:theme});
    for (const count of [1,2,3]) {
      for (const screen of ['new','legacy']) {
        if (screen === 'new') {
          await page.goto(`${base}/fixtures/uiux/comparison?panels=${count}`);
          await page.locator('#conversation-rounds article').first().waitFor();
        } else {
          const fixture = await comparisonFixture(page,Array.from({length:3},(_,i) => i === 1 ? 'failed' : 'completed'));
          await openComparison(page);
          for (let i = 3; i > count; i--) await page.getByRole('button',{name:`패널 ${i} 제거`,exact:true}).click();
          await page.getByRole('button',{name:'비교 실행',exact:true}).click();
          await page.getByRole('article',{name:'패널 1',exact:true}).getByText('완료',{exact:true}).waitFor();
          assert.equal(fixture.requests.length,1);
        }
        for (const width of [320,390,767,768,769,800,801,1024,1279,1280,1440]) {
          await page.setViewportSize({width,height:900});
          await page.evaluate(async () => {
            await Promise.all(document.getAnimations().filter(a => a.effect.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {})));
          });
          const row = {screen,theme,count,width,...await page.evaluate(measure)};
          rows.push(row);
          assert(!row.overflow,`${screen}/${theme}/${count}/${width}: no page overflow`);
          assert(row.text.every(t => t.ratio + .01 >= t.minimum),`${screen}/${theme}/${count}/${width}: composed text contrast ${JSON.stringify(row.text.filter(t => t.ratio + .01 < t.minimum))}`);
          assert(row.targets.every(t => t.width >= 43.99 && t.height >= 43.99),`${screen}/${theme}/${count}/${width}: 44px targets ${JSON.stringify(row.targets.filter(t => t.width < 43.99 || t.height < 43.99))}`);
          assert(row.borders.every(b => b.inside >= 3 && b.outside >= 3),`${screen}/${theme}/${count}/${width}: required boundaries`);
          if (count === 3 && [390,1440].includes(width)) {
            if (screen === 'legacy' && width === 390) await page.getByRole('tab',{name:'패널 2',exact:true}).click();
            await page.evaluate(() => scrollTo({top:0,behavior:'instant'}));
            await page.screenshot({path:new URL(`${screen}-mixed-${theme}-${width}.png`,out).pathname,fullPage:true});
          }
        }
      }
    }
  }
  let snapshot, missing = false;
  await page.route('**/admin/comparison-conversations/visual-only', route => missing
    ? route.fulfill({status:404,json:{detail:{code:'not_found'}}}) : route.fulfill({json:snapshot}));
  await page.route('**/admin/comparison-conversations/visual-only/adopt', route => route.abort('failed'));
  for (const theme of ['light','dark']) {
    await page.emulateMedia({colorScheme:theme});
    for (const width of [390,1440]) {
      missing = false;
      await page.setViewportSize({width,height:900});
      await page.goto(`${base}/fixtures/uiux/comparison?panels=3`);
      snapshot = await page.locator('#comparison-conversation-data').evaluate(el => JSON.parse(el.textContent).snapshot);
      snapshot.phase = 'generating';
      snapshot.children[0].panels[1].status = 'running';
      snapshot.children[0].panels[1].run_id = 'active-retry';
      snapshot.children[0].panels[1].attempts = [{status:'failed',text:'이전 시도의 잠정 출력',error_code:'invalid_output'}];
      await page.getByRole('button',{name:'상태 새로고침',exact:true}).click();
      await page.getByText('다른 패널 실행 중 · 완료 응답도 채택할 수 없습니다.',{exact:true}).waitFor();
      await page.evaluate(async () => { await Promise.all(document.getAnimations().filter(a => a.effect.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {}))); });
      rows.push({screen:'new-running',theme,count:3,width,...await page.evaluate(measure)});
      await page.evaluate(() => scrollTo({top:0,behavior:'instant'}));
            await page.screenshot({path:new URL(`new-running-${theme}-${width}.png`,out).pathname,fullPage:true});
      await page.getByLabel('공통 교사 질문',{exact:true}).evaluate(el => { el.value = '조회 실패에도 보존되는 초안'; });
      missing = true;
      await page.getByRole('button',{name:'상태 새로고침',exact:true}).click();
      await page.getByText(/비교 대화를 찾거나 열 수 없습니다/).waitFor();
      await page.evaluate(async () => { await Promise.all(document.getAnimations().filter(a => a.effect.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {}))); });
      rows.push({screen:'new-unavailable',theme,count:3,width,...await page.evaluate(measure)});
      await page.evaluate(() => scrollTo({top:0,behavior:'instant'}));
            await page.screenshot({path:new URL(`new-unavailable-${theme}-${width}.png`,out).pathname,fullPage:true});
      missing = false;
      snapshot.phase = 'awaiting_adoption';
      snapshot.children[0].panels[1].status = 'failed';
      await page.getByRole('button',{name:'상태 새로고침',exact:true}).click();
      await page.getByRole('article',{name:'패널 1',exact:true}).getByRole('button',{name:'학생 응답 채택',exact:true}).click();
      await page.getByText('요청 결과를 확인할 수 없습니다. 입력은 유지됩니다. 상태 새로고침으로 저장된 결과를 확인하세요.',{exact:true}).waitFor();
      await page.evaluate(async () => { await Promise.all(document.getAnimations().filter(a => a.effect.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {}))); });
      rows.push({screen:'new-uncertain',theme,count:3,width,...await page.evaluate(measure)});
      await page.evaluate(() => scrollTo({top:0,behavior:'instant'}));
      await page.screenshot({path:new URL(`new-uncertain-${theme}-${width}.png`,out).pathname,fullPage:true});
    }
  }
  assert(rows.every(row => !row.overflow && row.text.every(t => t.ratio + .01 >= t.minimum)), 'state captures retain overflow and contrast requirements');
  assert(rows.every(row => row.targets.every(t => t.width >= 43.99 && t.height >= 43.99)), 'state capture targets');
  const compact = rows.map(row => {
    const pairs = new Map();
    for (const item of row.text) {
      const key = JSON.stringify([item.color,item.background,item.minimum]);
      if (!pairs.has(key)) pairs.set(key,{...item,count:0});
      pairs.get(key).count++;
    }
    return {...row,text:[...pairs.values()]};
  });
  await writeFile(new URL('measurements.json',out),JSON.stringify(compact,null,2)+'\n');
  return {pageErrors:[]};
}
