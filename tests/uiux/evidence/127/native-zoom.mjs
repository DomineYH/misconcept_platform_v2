import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {state} from '../../../browser_ai_connections.mjs';
import {comparisonFixture,openComparison} from '../../../s5_browser_fixtures.mjs';
import {mkdir,writeFile} from 'node:fs/promises';
const out=new URL('./',import.meta.url).pathname;
await mkdir(out,{recursive:true});
const context=await chromium.launchPersistentContext('/tmp/misconcept-uiux-120/127-zoom-family-profile',{headless:false,viewport:null,args:['--window-size=1440,1000']});
const rows=[];
try {
 const page=await context.newPage();page.setDefaultTimeout(10000);
 const base='http://127.0.0.1:18727';
 await page.route('**/admin/ai/state',route=>route.fulfill({json:state()}));
 await page.route('**/admin/comparison-conversations',route=>route.fulfill({json:{items:[],next_cursor:null}}));
 await page.route('**/admin/comparisons*',route=>route.fulfill({json:{items:[],next_cursor:null}}));
 await page.route('**/sessions/1/analysis/runs/run-1',route=>route.abort('failed'));
 const cdp=await context.newCDPSession(page);
 const {windowId}=await cdp.send('Browser.getWindowForTarget');
 for (const zoom of [1,2]) {
  await page.goto('chrome://settings/appearance');
  await page.locator('#zoomLevel').selectOption(String(zoom));
  assert.equal((await page.locator('#zoomLevel option:checked').innerText()).trim(),`${zoom*100}%`);
  for (const outer of [780,1440]) {
   await cdp.send('Browser.setWindowBounds',{windowId,bounds:{width:outer,height:1000}});
   for (const theme of ['light','dark']) {
    await page.emulateMedia({colorScheme:theme,reducedMotion:'reduce'});
    for (const [screen,path,selector] of [
     ['selection','/scenarios?long=1&many=1','a[href="/scenarios/1"]'],
     ['chat','/chat?long=1&uiux122_long=1&mentor_manual=1','#teacher-input'],
     ['analysis','/fixtures/s4/analysis?state=preserved','[data-analysis-result] summary'],
     ['list','/fixtures/s6/scenarios?uiux123=1','#search-input'],
     ['editor','/fixtures/s2/editor?uiux123=1','#save-draft'],
     ...[1,2,3].map(n=>[`new-${n}`,`/fixtures/uiux/comparison?panels=${n}`,'#conversation-controls button:last-child']),
     ...[1,2,3].map(n=>[`legacy-${n}`,'/fixtures/s2/editor?new=1','#comparison-question']),
     ['ai','/admin/ai','#ai-refresh'],
    ]) {
     if(screen.startsWith('legacy')) {
      await comparisonFixture(page,['completed','failed','completed']);
      await openComparison(page);
      for(let i=3;i>Number(screen.at(-1));i--) {
       const tab=page.getByRole('tab',{name:`패널 ${i}`,exact:true});
       if(await tab.isVisible()) await tab.click();
       await page.getByRole('button',{name:`패널 ${i} 제거`,exact:true}).click();
      }
      await page.getByRole('button',{name:'비교 실행',exact:true}).click();
      await page.getByRole('article',{name:'패널 1',exact:true}).getByText('완료',{exact:true}).waitFor();
     }else await page.goto(base+path);
     if(screen==='ai') await page.locator('[data-provider="openai"]').waitFor();
     if(screen==='analysis') await page.getByRole('heading',{name:'채택된 보고서: 정상 분석',exact:true}).waitFor();
     if(screen==='editor') await page.locator('#common-model').waitFor();
     const control=page.locator(selector).first();
     if(screen==='chat') {
      await control.fill('200% 확대에서도 유지할 여러 줄 초안\n마지막 줄');
      if(await page.locator('.mobile-tab[data-panel="scenario"]').isVisible()) {
       await page.locator('.mobile-tab[data-panel="scenario"]').click();
       await page.locator('.mobile-tab[data-panel="chat"]').click();
      }
      assert.equal(await control.inputValue(),'200% 확대에서도 유지할 여러 줄 초안\n마지막 줄');
     }
     await page.keyboard.press('Tab');
     await control.focus();await control.scrollIntoViewIfNeeded();
     await page.evaluate(async()=>Promise.all(document.getAnimations().filter(a=>a.effect.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{}))));
     const observed=await control.evaluate(el=>{
      const rgb=s=>s.match(/[\d.]+/g).map(Number);
      const bg=node=>{
       if(!node)return [255,255,255];
       const c=rgb(getComputedStyle(node).backgroundColor),parent=bg(node.parentElement);
       return c.slice(0,3).map((v,i)=>v*(c[3]??1)+parent[i]*(1-(c[3]??1)));
      };
      const s=getComputedStyle(el),color=rgb(s.outlineColor).slice(0,3),background=bg(el.parentElement);
      const lum=c=>c.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);
      const ratio=(Math.max(lum(color),lum(background))+.05)/(Math.min(lum(color),lum(background))+.05);
      const r=el.getBoundingClientRect();
      return {innerWidth,innerHeight,outerWidth,outerHeight,dpr:devicePixelRatio,visualScale:visualViewport.scale,visualHeight:visualViewport.height,overflow:document.documentElement.scrollWidth>innerWidth,
       focus:{outlineWidth:s.outlineWidth,outlineOffset:s.outlineOffset,outlineStyle:s.outlineStyle,color,background,ratio,active:el===document.activeElement,rect:{x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height}}};
     });
     rows.push({screen,theme,zoom,requestedOuterWidth:outer,...observed});
     assert.equal(observed.dpr,zoom,'browser zoom changes DPR without emulation');
     assert(!observed.overflow,`${screen}/${theme}/${outer}/${zoom}: document fits`);
     assert(observed.focus.active);
     assert(parseFloat(observed.focus.outlineWidth)>=2&&observed.focus.outlineStyle==='solid',`${screen}: visible focus`);
     assert(observed.focus.ratio>=3,`${screen}: composed focus contrast`);
     assert(observed.focus.rect.y>=0&&observed.focus.rect.bottom<=observed.visualHeight,`${screen}: focused action/input reachable`);
     if(screen==='chat') {
      const composer=await page.locator('#teacher-form').boundingBox();
      rows.at(-1).composer=composer;
      assert(composer.y>=0&&composer.y+composer.height<=observed.visualHeight,'whole composer remains in the actual zoom viewport');
     }
     if(zoom===2&&outer===1440&&(!screen.includes('-')||screen.endsWith('-3'))) {
      const shot=await cdp.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false,fromSurface:true});
      const pixels=Buffer.from(shot.data,'base64');
      rows.at(-1).capture={method:'CDP Page.captureScreenshot without clip, actual full browser viewport',width:pixels.readUInt32BE(16),height:pixels.readUInt32BE(20)};
      assert.equal(pixels.readUInt32BE(16),observed.outerWidth);
      await writeFile(`${out}/zoom-${screen}-${theme}.png`,pixels);
     }
    }
   }
  }
 }
 console.log(`PASS ${rows.length} headed native zoom/focus observations`);
}finally{
 await writeFile(`${out}/zoom-focus.json`,JSON.stringify({method:'Headed Chromium native Page zoom dropdown; viewport:null; Browser.setWindowBounds changes native outer window only; system theme emulation; no CSS scaling/deviceScaleFactor',candidate:'6224819e327832e9c14cca2e4e138374c4ec2514',rows},null,2)+'\n');
 await context.close();
}
