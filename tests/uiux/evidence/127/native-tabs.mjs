import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createServer} from 'node:net';
import {setTimeout as delay} from 'node:timers/promises';
import {writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';
import {colorMath} from '../../../uiux_color_math.mjs';

const root = fileURLToPath(new URL('../../../../',import.meta.url));
const listener = createServer();
await new Promise(resolve => listener.listen(0,'127.0.0.1',resolve));
const port = listener.address().port;
await new Promise(resolve => listener.close(resolve));
const server = spawn('uv',['run','--frozen','python','tests/browser_server.py'],{cwd:root,env:{...process.env,BROWSER_TEST_PORT:String(port)},stdio:'ignore'});
const stopped = new Promise(resolve => server.once('close',resolve));
let context;
const rows = [];
try {
  const base = `http://127.0.0.1:${port}`;
  let ready = false;
  for (let i=0;i<150;i++) {
    try { if ((await fetch(base+'/chat')).ok) {ready=true;break;} } catch {}
    await delay(100);
  }
  assert(ready);
  context = await chromium.launchPersistentContext('/tmp/misconcept-uiux-120/review-fix/native-profile',{headless:false,viewport:null,args:['--window-size=1440,1000']});
  const page = await context.newPage();
  await page.goto('chrome://settings/appearance');
  await page.locator('#zoomLevel').selectOption('2');
  assert.equal((await page.locator('#zoomLevel option:checked').innerText()).trim(),'200%');
  const cdp = await context.newCDPSession(page);
  for (const theme of ['light','dark']) {
    await page.emulateMedia({colorScheme:theme});
    await page.goto(base+'/chat?long=1&uiux122_long=1&mentor_manual=1');
    await page.getByRole('tab',{name:'상황 정보',exact:true}).click();
    for (const family of ['mobile','info']) for (const selected of [true,false]) {
      const tab = page.locator(family === 'mobile' ? (selected ? '#mobile-tab-scenario' : '#mobile-tab-chat') : (selected ? '#info-tab-guide' : '#info-tab-profile'));
      for (const state of ['rest','focus','hover','hover-focus']) {
        await page.mouse.move(0,0); await tab.evaluate(el => el.blur());
        if (state.includes('focus')) {await page.keyboard.press('Tab');await tab.focus();}
        if (state.includes('hover')) await tab.hover();
        await page.evaluate(async () => Promise.all(document.getAnimations().filter(a => a.effect.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {}))));
        const observed = await tab.evaluate(new Function('el', `
          const {rgba,over,background,contrast} = (${colorMath})();
          const s = getComputedStyle(el), bg = background(el), color = over(rgba(s.color),bg);
          return {color,background:bg,ratio:contrast(color,bg),outlineWidth:s.outlineWidth,innerWidth,outerWidth,dpr:devicePixelRatio,selected:el.getAttribute('aria-selected')};
        `));
        assert.equal(observed.dpr,2);
        assert.equal(observed.selected,String(selected));
        assert(observed.ratio>=4.5);
        if (state.includes('focus')) assert(parseFloat(observed.outlineWidth)>=2);
        rows.push({theme,family,state,...observed});
      }
    }
    await page.getByRole('tab',{name:'대화',exact:true}).click();
    await page.locator('#teacher-input').fill('200% 확대에서도 유지할 여러 줄 초안\n마지막 줄');
    await page.keyboard.press('Tab'); await page.locator('#teacher-input').focus();
    await page.evaluate(async () => Promise.all(document.getAnimations().filter(a => a.effect.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {}))));
    const screenshot = await cdp.send('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
    await writeFile(new URL(`zoom-chat-${theme}.png`,import.meta.url),Buffer.from(screenshot.data,'base64'));
  }
  await writeFile(new URL('native-tab-contrast.json',import.meta.url),JSON.stringify(rows,null,2)+'\n');
  console.log(`PASS ${rows.length} native 200% tab states; two chat captures`);
} finally {
  await context?.close();
  server.kill('SIGTERM');
  await stopped;
}
