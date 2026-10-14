import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {writeFile} from 'node:fs/promises';
const browser=await chromium.launch();const rows=[];const out=new URL('./',import.meta.url).pathname;
try {
 const p=await browser.newPage();const base='http://127.0.0.1:18727';
 p.on('dialog',dialog=>dialog.accept());
 await p.route('**/admin/users/*/delete',route=>route.fulfill({status:403,json:{detail:'사용자 삭제를 완료하지 못했습니다. 권한을 확인하세요.'}}));
 await p.route('**/admin/groups/*/delete',route=>route.fulfill({status:403,json:{detail:'그룹 삭제를 완료하지 못했습니다. 권한과 멤버를 확인하세요.'}}));
 await p.route('**/admin/sessions/export-selected',route=>route.fulfill({status:403,body:'PRIVATE_DOWNLOAD_ERROR'}));
 for(const theme of ['light','dark']) {
  await p.emulateMedia({colorScheme:theme,reducedMotion:'reduce'});
  for(const width of [320,390,768,1024,1440]) {
   await p.setViewportSize({width,height:900});
   for(const [screen,path,buttonSelector,noticeSelector] of [
    ['user-delete','/fixtures/s6/users?total=1','.delete-btn','tr[data-user-id] [role="alert"]'],
    ['group-delete','/fixtures/uiux/admin/groups','.delete-btn','[data-group-id] [role="alert"]'],
    ['download','/fixtures/uiux/admin/sessions','#btn-download-selected','.table-container [role="alert"]'],
   ]) {
    await p.goto(base+path);
    if(screen==='download') await p.locator('.session-checkbox').check();
    const button=p.locator(buttonSelector).first();await button.click();
    const notice=p.locator(noticeSelector);await notice.waitFor();
    await p.evaluate(async()=>Promise.all(document.getAnimations().filter(a=>a.effect.getTiming().iterations!==Infinity).map(a=>a.finished.catch(()=>{}))));
    const observation=await notice.evaluate(el=>{
     const rgb=s=>s.match(/[\d.]+/g).map(Number),bg=node=>{
      if(!node)return [255,255,255];const c=rgb(getComputedStyle(node).backgroundColor),under=bg(node.parentElement);
      return c.slice(0,3).map((v,i)=>v*(c[3]??1)+under[i]*(1-(c[3]??1)));
     };
     const lum=c=>c.map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;}).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);
     const foreground=rgb(getComputedStyle(el).color).slice(0,3),background=bg(el);
     const ratio=(Math.max(lum(foreground),lum(background))+.05)/(Math.min(lum(foreground),lum(background))+.05);
     return {text:el.innerText,foreground,background,ratio,focused:el===document.activeElement,overflow:document.documentElement.scrollWidth>innerWidth};
    });
    const target=await button.boundingBox();
    rows.push({screen,theme,width,...observation,target});
    assert(observation.focused&&!observation.overflow&&observation.ratio>=4.5);
    assert(target.width>=44&&target.height>=44);
    assert(!(await p.content()).includes('PRIVATE_DOWNLOAD_ERROR'));
    if([390,1440].includes(width)) await p.screenshot({path:`${out}/${screen}-${theme}-${width}.png`});
   }
  }
 }
 console.log(`PASS ${rows.length} composed final failure-state observations`);
}finally{
 await writeFile(`${out}/final-errors.json`,JSON.stringify({candidate:'d773d5ba938fb83e61036c29b7ac5cf337ad8b17',rows},null,2)+'\n');
 await browser.close();
}
