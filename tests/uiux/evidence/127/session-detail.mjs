import assert from 'node:assert/strict';
import {chromium} from 'playwright';
import {readFile,writeFile} from 'node:fs/promises';
const fixtures=JSON.parse(await readFile('/tmp/misconcept-uiux-120/127-logs/session-detail-fixtures.json','utf8'));
const browser=await chromium.launch();
const rows=[];const out=new URL('./',import.meta.url).pathname;
try {
 const page=await browser.newPage();const base='http://127.0.0.1:18727';
 await page.route('**/sessions/1/analysis/runs/run-1',route=>route.abort('failed'));
 for (const theme of ['light','dark']) {
  await page.emulateMedia({colorScheme:theme,reducedMotion:'reduce'});
  for (const width of [320,390,768,1024,1440]) {
   await page.setViewportSize({width,height:900});
   for (const fixture of fixtures) {
    await page.route('**/fixtures/127/session-detail',route=>route.fulfill({contentType:'text/html',body:fixture.html}));
    await page.goto(base+'/fixtures/127/session-detail');
    if(fixture.state==='accepted') await page.getByRole('heading',{name:'채택된 보고서: 정상 분석',exact:true}).waitFor();
    else assert.equal(await page.locator('.message').count(),fixture.state==='empty'?0:3);
    if(fixture.state==='legacy') assert(await page.getByText(/실제 시작 시점의 설정을 증명하지 않습니다/).isVisible());
    const status=fixture.state==='active'?'진행중':fixture.state==='deleted'?'삭제됨':'완료';
    assert.equal(await page.locator('.session-info .badge').innerText(),status);
    const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
    rows.push({state:fixture.state,theme,width,overflow,status});
    assert(!overflow,`${fixture.state}/${theme}/${width}: fragment fits shell`);
    if([390,1440].includes(width)&&['legacy','accepted','empty'].includes(fixture.state)) await page.screenshot({path:`${out}/detail-${fixture.state}-${theme}-${width}.png`});
    await page.unroute('**/fixtures/127/session-detail');
   }
  }
 }
 console.log(`PASS ${rows.length} actual session_detail fragment/state observations`);
}finally{
 await writeFile(`${out}/session-detail.json`,JSON.stringify({candidate:'6224819e327832e9c14cca2e4e138374c4ec2514',method:'Actual production Jinja session_detail included in production layout; isolated synthetic HTTP HTML interception; no invented modal caller',rows},null,2)+'\n');
 await browser.close();
}
