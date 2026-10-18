import assert from 'node:assert/strict';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {colorMath} from './uiux_color_math.mjs';
import {settle} from './browser_uiux_scenario_layout.mjs';

const evidence = process.env.BROWSER_173_ARTIFACT_DIR || '/tmp/mp2-browser-173';
const baseline = process.env.BROWSER_173_BASELINE_CSS_DIR;
const phase = baseline ? 'before' : 'after';

function presentation(el, colorMath) {
  const {rgba, over, background, contrast} = colorMath();
  const s=getComputedStyle(el),r=el.getBoundingClientRect(),bg=background(el);
  return {width:Math.round(r.width*100)/100,height:Math.round(r.height*100)/100,border:s.border,
    radius:s.borderRadius,padding:s.padding,fontSize:s.fontSize,lineHeight:s.lineHeight,
    background:s.backgroundColor,color:s.color,outline:s.outline,outlineOffset:s.outlineOffset,
    text:el.textContent.trim(),label:el.getAttribute('aria-label'),transform:s.transform,
    contrast:contrast(over(rgba(s.color),bg),bg),focusVisible:el.matches(':focus-visible'),
    outlineWidth:parseFloat(s.outlineWidth),outlineStyle:s.outlineStyle,
    focusContrast:contrast(rgba(s.outlineColor),background(el.parentElement))};
}

// Static forms have no asynchronous opening state. Existing save-failure checks cover their submission paths.
const staticDialogs = [
  ['groups-create','/fixtures/uiux/admin/groups','#open-create-modal','#create-modal','[data-modal="create-modal"].btn-secondary'],
  ['groups-edit','/fixtures/uiux/admin/groups','.edit-btn','#edit-modal','[data-modal="edit-modal"].btn-secondary'],
  ['about-create','/fixtures/uiux/admin/about','#open-create-modal','#create-modal','#cancel-create'],
  ['about-edit','/fixtures/uiux/admin/about','.edit-btn','#edit-modal','#cancel-edit'],
  ['about-delete','/fixtures/uiux/admin/about','.delete-btn','#delete-modal','#cancel-delete'],
  ['users-create','/fixtures/s6/users','#open-create-modal','#create-modal','[data-modal="create-modal"].btn-secondary'],
  ['users-edit','/fixtures/s6/users','.edit-btn','#edit-panel','#cancel-edit'],
  ['users-bulk','/fixtures/s6/users','#open-bulk-modal','#bulk-modal','#bulk-modal-footer button[onclick="closeBulkModal()"]'],
];

export default async function check(page) {
  const base=new URL(page.url()).origin,rows=[],lifecycle=[],failures=[];
  const expect=(condition,message)=>{if(!condition)failures.push(message);};
  await mkdir(evidence,{recursive:true});
  await page.emulateMedia({reducedMotion:'reduce'});
  page.on('dialog',dialog=>dialog.accept());
  if(baseline) await page.route('**/static/css/*.css',async route=>{
    const name=new URL(route.request().url()).pathname.split('/').at(-1);
    if(!['styles.css','scenario-editor.css','scenario-comparison.css'].includes(name))return route.continue();
    await route.fulfill({contentType:'text/css',body:await readFile(`${baseline}/${name}`,'utf8')});
  });
  const active=root=>root.evaluate(el=>el.classList.contains('active')||el.classList.contains('flex'));
  async function record(meta,consumer,root){
    await settle(page);
    const focused=await page.evaluateHandle(()=>document.activeElement);
    const close=root.locator('.modal-header button,.panel-header button').first();
    for(const state of ['default','hover','focus','active']){
      await page.mouse.move(0,0);await close.evaluate(el=>el.blur());
      if(state==='hover'||state==='active')await close.hover();
      if(state==='focus'){await page.keyboard.press('Tab');await close.focus();}
      if(state==='active')await page.mouse.down();
      await settle(page);
      const row={...meta,viewportWidth:meta.width,consumer,state,...await close.evaluate(new Function('el',`return (${presentation})(el,(${colorMath}));`))};
      rows.push(row);
      const name=`${meta.language}/${meta.theme}/${meta.width}/${consumer}/${state}`;
      expect(row.width>=44&&row.height>=44,`${name}: 44×44 target ${row.width}×${row.height}`);
      expect(row.contrast>=4.5,`${name}: text/glyph contrast ${row.contrast}`);
      expect(row.text==='×'&&row.label===(meta.language==='ko'?'닫기':'Close'),`${name}: common glyph and accessible label`);
      if(state==='focus')expect(row.focusVisible&&row.outlineWidth>=2&&row.outlineStyle!=='none'&&row.focusContrast>=3,`${name}: visible keyboard focus`);
      if(state==='active')expect(row.transform==='none',`${name}: active target stays unscaled`);
      await page.mouse.move(-10,-10);await page.mouse.up();
    }
    await close.evaluate(el=>el.blur());await page.mouse.move(0,0);await settle(page);
    if(meta.language==='ko'&&meta.theme==='light'&&[320,390,1180].includes(meta.width))await page.screenshot({path:`${evidence}/${phase}-modal-${consumer}-${meta.width}.png`});
    const box=await root.evaluate(el=>{const r=(el.querySelector('.modal-container')||el).getBoundingClientRect();return{x:r.x,right:r.right,y:r.y,bottom:r.bottom};});
    expect(box.x>=15.99&&box.right<=meta.width-15.99&&box.y>=15.99&&box.bottom<=884.01,`${consumer}: bounded dialog ${JSON.stringify(box)}`);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`${consumer}: document fits viewport`);
    await writeFile(`${evidence}/${phase}-modal-measurements.json`,JSON.stringify({rows,lifecycle,failures},null,2)+'\n');
    await focused.evaluate(el=>{if(el.isConnected)el.focus();});await focused.dispose();
  }
  async function trap(root,name){
    expect(await root.evaluate(el=>el.contains(document.activeElement)),`${name}: initial focus enters dialog`);
    const items=root.locator('button:not(:disabled),a[href],input:not(:disabled):not([type=hidden]),select:not(:disabled),textarea:not(:disabled),summary,[tabindex="0"]').filter({visible:true});
    await items.last().focus();await page.keyboard.press('Tab');
    expect(await root.evaluate(el=>el.contains(document.activeElement)),`${name}: forward trap`);
    await items.first().focus();await page.keyboard.press('Shift+Tab');
    expect(await root.evaluate(el=>el.contains(document.activeElement)),`${name}: reverse trap`);
  }
  async function exits(meta,consumer,opener,root,cancel=null,outside=true,backdrop=null){
    const open=async()=>{await opener.click();await settle(page);};
    // record() blurs the glyph to measure its resting state; restore entry before checking the trap.
    await root.locator('.modal-header button,.panel-header button').first().focus();await trap(root,consumer);
    await root.locator('.modal-header button,.panel-header button').first().click();
    expect(!await active(root)&&await opener.evaluate(el=>el===document.activeElement),`${consumer}: close returns focus`);
    await open();await page.keyboard.press('Escape');
    expect(!await active(root)&&await opener.evaluate(el=>el===document.activeElement),`${consumer}: Escape returns focus`);
    if(cancel){await open();await root.locator(cancel).click();expect(!await active(root)&&await opener.evaluate(el=>el===document.activeElement),`${consumer}: cancel returns focus`);}
    await open();
    if(backdrop)await page.locator(backdrop).click({position:{x:1,y:1}});else await root.click({position:{x:1,y:1}});
    expect(await active(root)!==outside,`${consumer}: existing outside-click policy`);
    if(!outside)await page.keyboard.press('Escape');
    lifecycle.push({...meta,consumer,cancel:Boolean(cancel),outside,close:true,escape:true,trap:true});
  }
  async function remote(meta,c){
    let release,mode='held';const held=new Promise(resolve=>{release=resolve;});
    const handler=async route=>{
      if(mode==='held')await held;
      if(mode==='failure')return route.fulfill({status:403,body:'PRIVATE FAILURE'});
      await route.fulfill(await c.success());
    };
    await page.route(c.url,handler);
    try{
      await page.goto(base+c.path);
      if(c.consumer==='about'&&meta.width<=768)await page.locator('.hamburger').click();
      if(c.consumer==='teacher-analysis'&&meta.width<=768)await page.locator('#mobile-tab-scenario').click();
      const opener=page.locator(c.open).first(),root=page.locator(c.root);
      await opener.click();await root.waitFor({state:'visible'});
      await trap(root,`${c.consumer}-loading`);await record(meta,`${c.consumer}-loading`,root);
      mode='success';release();await root.locator(c.loaded).first().waitFor();await trap(root,`${c.consumer}-loaded`);
      await record(meta,c.consumer,root);await exits(meta,c.consumer,opener,root);
      if(meta.width===320){
        mode='failure';await page.goto(base+c.path);
        if(c.consumer==='about'&&meta.width<=768)await page.locator('.hamburger').click();
        if(c.consumer==='teacher-analysis'&&meta.width<=768)await page.locator('#mobile-tab-scenario').click();
        await page.locator(c.open).first().click();await page.locator(c.failure).first().waitFor({state:'visible'});
        if(c.consumer==='about'){
          expect(!await active(root),'About failure closes dialog');
          expect(await page.locator(c.failure).evaluate(el=>el===document.activeElement),'About failure focuses caller-side guidance');
        }else{
          expect(!(await root.innerText()).includes('PRIVATE FAILURE'),`${c.consumer}: raw server error stays private`);
          await trap(root,`${c.consumer}-failure`);await record(meta,`${c.consumer}-failure`,root);
          await page.keyboard.press('Escape');expect(await page.locator(c.open).first().evaluate(el=>el===document.activeElement),`${c.consumer}: failure Escape returns focus`);
        }
        lifecycle.push({...meta,consumer:c.consumer,state:'failure',focus:true,closed:true});
      }
    }finally{release();await page.unroute(c.url,handler);}
  }
  const html=async path=>({contentType:'text/html',body:await(await page.request.get(base+path)).text()});
  await page.route('**/sessions/1/end',route=>route.fulfill({status:202,json:{latest_run:{status:'running'}}}));
  for(const language of baseline?['ko']:['ko','en']){
    await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
    for(const theme of baseline?['light']:['light','dark']){
      await page.emulateMedia({colorScheme:theme});
      for(const width of baseline?[320,390,1180]:[320,390,768,1024,1180,1440]){
        const meta={language,theme,width};console.log(`173 modal ${phase}: ${language}/${theme}/${width}`);
        await page.setViewportSize({width,height:900});
        for(const[consumer,path,open,selector,cancel]of staticDialogs){
          await page.goto(base+path);const opener=page.locator(open).first(),root=page.locator(selector);
          await opener.click();await trap(root,consumer);await record(meta,consumer,root);
          await exits(meta,consumer,opener,root,cancel,consumer!=='users-bulk',consumer==='users-edit'?'#edit-panel-overlay':null);
        }
        for(const c of[
          {consumer:'about',path:'/login',open:'#open-about-modal',root:'#about-modal-overlay',url:'**/about/content',loaded:'.contributor-card',success:()=>html('/about/content'),failure:'#about-load-error'},
          {consumer:'admin-detail',path:'/fixtures/uiux/admin/analysis',open:'[hx-target="#detail-modal"]',root:'#detail-modal',url:'**/admin/analysis/1/detail',loaded:'h3',success:()=>html('/fixtures/uiux/admin/detail'),failure:'#detail-modal .modal-error'},
          {consumer:'admin-session-analysis',path:'/fixtures/uiux/admin/sessions',open:'.view-analysis-btn',root:'#analysis-modal',url:'**/admin/sessions/1/analysis_modal',loaded:'.modal-footer',success:()=>html('/fixtures/s4/modal?state=preserved'),failure:'#analysis-modal .modal-error'},
          {consumer:'user-conversations',path:'/fixtures/uiux/admin/user_conversations',open:'button[onclick="showSessionList(1)"]',root:'#sessionModal',url:'**/admin/user-conversations/1/sessions',loaded:'#sessionListContent a',success:async()=>({json:[{id:1,started_at:'2026-10-01',scenario_title:'Long session '.repeat(10),message_count:2}]}),failure:'#sessionModal .modal-error'},
          {consumer:'teacher-analysis',path:'/chat',open:'#end-session-btn',root:'#analysis-modal-overlay',url:'**/sessions/1/analysis_modal',loaded:'.analysis-accepted-report',success:()=>html('/fixtures/s4/modal?state=preserved'),failure:'#analysis-modal-container .modal-footer button'},
        ])await remote(meta,c);
      }
    }
  }
  for(const row of rows){
    const reference=rows.find(other=>other.language===row.language&&other.theme===row.theme&&other.viewportWidth===row.viewportWidth&&other.state===row.state);
    for(const key of['width','height','border','radius','padding','fontSize','lineHeight','background','color','outline','outlineOffset','text','label'])expect(row[key]===reference[key],`${row.language}/${row.theme}/${row.width}/${row.consumer}/${row.state}: unified ${key} (${row[key]} vs ${reference[key]})`);
  }
  await writeFile(`${evidence}/${phase}-modal-measurements.json`,JSON.stringify({rows,lifecycle,failures},null,2)+'\n');
  console.log(`173 modal: ${rows.length} state measurements; ${lifecycle.length} lifecycle checks; ${failures.length} failures`);
  assert.equal(failures.length,0,failures.slice(0,20).join('; '));return{pageErrors:[]};
}
