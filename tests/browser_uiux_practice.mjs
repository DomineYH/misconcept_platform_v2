import {colorMath} from './uiux_color_math.mjs';
import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';

// Reuse #121's rendered-color measurement without regenerating its evidence.
function measure(colorMath) {
  const {rgba, over, background, contrast} = colorMath();
  const visible = el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
  const text = [], targets = [], borders = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!visible(el) || ['SCRIPT', 'STYLE', 'OPTION'].includes(el.tagName)) continue;
    const s = getComputedStyle(el), box = el.getBoundingClientRect();
    const ownText = [...el.childNodes].filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent.trim()).join(' ').trim();
    const bg = background(el);
    if (ownText && !el.matches('.empty-state-icon, .toast-icon')) {
      const fg = rgba(s.color);
      let opacity = 1;
      for (let node = el; node; node = node.parentElement) opacity *= Number(getComputedStyle(node).opacity);
      fg[3] = (fg[3] ?? 1) * opacity;
      const color = over(fg, bg);
      const large = parseFloat(s.fontSize) >= 24 || (parseFloat(s.fontSize) >= 18.66 && Number(s.fontWeight) >= 700);
      text.push({text: ownText.slice(0, 80), color, background: bg, ratio: contrast(color, bg), minimum: large ? 3 : 4.5});
    }
    if (el.matches('button, a.btn, a.btn-primary, [role="tab"]')) targets.push({text: (el.textContent.trim() || el.getAttribute('aria-label')).slice(0, 50), width: box.width, height: box.height});
    if (el.matches('input:not([type="checkbox"]), textarea, select, .btn-secondary')) {
      const color = rgba(s.borderTopColor).slice(0, 3);
      borders.push({name: el.id || el.name || el.textContent.slice(0, 30), inside: contrast(color, bg), outside: contrast(color, background(el.parentElement))});
    }
  }
  return {overflow: document.documentElement.scrollWidth > innerWidth, text, targets, borders};
}

export default async function check(page) {
  const base = new URL(page.url()).origin;
  const out = new URL('./uiux/evidence/122/', import.meta.url);
  await mkdir(out, {recursive:true});
  await page.emulateMedia({reducedMotion:'reduce'});
  const results = [];
  const screens = [
    ['chat', '/chat?long=1&uiux122_long=1&mentor_manual=1'],
    ['analysis-running', '/fixtures/s4/analysis?state=running'],
    ['analysis-preserved', '/fixtures/s4/analysis?state=preserved'],
    ['analysis-partial', '/fixtures/s4/analysis?state=partial'],
    ['history-native', '/fixtures/uiux122/history'],
    ['history-reconstructed', '/fixtures/uiux122/history?reconstructed=1'],
  ];
  await page.route('**/sessions/1/analysis/runs/run-1', route => route.abort('failed'));
  for (const theme of ['light', 'dark']) {
    await page.emulateMedia({colorScheme:theme});
    for (const width of [320,390,767,768,769,1024,1440]) {
      await page.setViewportSize({width,height:900});
      for (const [screen,path] of screens) {
        await page.goto(base+path);
        if (screen !== 'chat') await page.locator('[data-analysis-result] h2').first().waitFor();
        await page.evaluate(async () => {
          await Promise.all(document.getAnimations().filter(a => a.effect.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {})));
        });
        const measured = await page.evaluate(`(${measure})(${colorMath})`);
        if (screen === 'chat') {
          const input = page.locator('#teacher-input');
          await input.fill('여러 줄의 초안\n두 번째 줄\n마지막 줄');
          if (width <= 768) await page.getByRole('tab', {name:'상황 정보',exact:true}).click();
          await page.getByRole('tab', {name:'문제 상황',exact:true}).click();
          const scroll = await page.locator('#scenario-panel').evaluate(el => {
            el.scrollTop = el.scrollHeight;
            return {height:el.clientHeight,content:el.scrollHeight,top:el.scrollTop};
          });
          assert(scroll.content > scroll.height && scroll.top > 0, 'information scrolls independently');
          if (width <= 768) await page.getByRole('tab', {name:'대화',exact:true}).click();
          const messages = await page.locator('#messages-container').evaluate(el => {
            el.scrollTop = el.scrollHeight;
            return {height:el.clientHeight,content:el.scrollHeight,top:el.scrollTop};
          });
          assert(messages.content > messages.height && messages.top > 0, 'long messages scroll independently');
          const composer = await page.locator('#teacher-form').boundingBox();
          assert(composer.y + composer.height <= 900, 'composer outside message scroll');
          measured.scroll = {information:scroll,messages,composer};
        }
        results.push({screen,theme,width,...measured});
        if ([390,1440].includes(width)) await page.screenshot({path:new URL(`${screen}-${theme}-${width}.png`,out).pathname});
      }
    }
  }
  const compact = results.map(row => ({...row, text:[...new Map(row.text.map(t => [JSON.stringify([t.color,t.background,t.minimum]),t])).values()]}));
  await writeFile(new URL('measurements.json',out),JSON.stringify(compact,null,2)+'\n');
  assert.equal(results.filter(row => row.overflow).length,0,'no document overflow');
  assert.equal(results.flatMap(row => row.targets).filter(t => t.width < 43.99 || t.height < 43.99).length,0,'44px action targets');
  assert.equal(results.flatMap(row => row.borders).filter(b => b.inside < 3 || b.outside < 3).length,0,'required boundaries');
  assert.equal(results.flatMap(row => row.text).filter(t => t.ratio+.01 < t.minimum).length,0,'composed text contrast');
  return {pageErrors:[]};
}
