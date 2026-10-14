import {colorMath} from './uiux_color_math.mjs';
import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {state as aiState} from './browser_ai_connections.mjs';

// Measure rendered colors after ancestor backgrounds are composed, not token literals.
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
  const out = new URL('./uiux/evidence/121/', import.meta.url);
  await mkdir(out, {recursive: true});
  await page.emulateMedia({reducedMotion: 'reduce'});
  await page.route('**/admin/ai/state', route => route.fulfill({json: aiState()}));
  await page.route('**/admin/comparison-conversations', route => route.fulfill({json: {items: [], next_cursor: null}}));
  await page.route('**/admin/comparisons*', route => route.fulfill({json: {items: [], next_cursor: null}}));
  const screens = [
    ['login', '/login?error=1'], ['selection', '/scenarios?long=1&many=1'],
    ['empty', '/scenarios?empty=1'], ['unavailable', '/scenarios?missing_problem=1'],
    ['chat', '/chat?mentor_manual=1&mentor_history=1&mentor_coached=1'],
    ['analysis', '/fixtures/s4/analysis?state=preserved'],
    ['scenarios', '/fixtures/s6/scenarios'], ['editor', '/fixtures/s2/editor'],
    ['comparison', '/fixtures/uiux/comparison'], ['legacy', '/fixtures/s2/editor?new=1'],
    ['ai', '/admin/ai'], ['users', '/fixtures/s6/users'], ['usage', '/admin/api-usage'],
  ];
  const results = [], failures = [];
  for (const theme of ['light', 'dark']) {
    await page.emulateMedia({colorScheme: theme});
    for (const width of [320, 390, 767, 768, 769, 800, 801, 1024, 1279, 1280, 1440]) {
      await page.setViewportSize({width, height: 900});
      for (const [screen, path] of screens) {
        await page.goto(base + path);
        if (screen === 'editor' || screen === 'legacy') await page.locator('#common-model').waitFor();
        if (screen === 'legacy') await page.getByRole('button', {name: '모델 비교 열기', exact: true}).click();
        if (screen === 'analysis') await page.locator('[data-analysis-result]').getByRole('status').waitFor({state: 'attached'});
        if (screen === 'ai') await page.locator('[data-provider="openai"]').waitFor();
        await page.evaluate(async () => {
          await Promise.all(document.getAnimations().filter(a => a.effect.getTiming().iterations !== Infinity).map(a => a.finished.catch(() => {})));
        });
        const measured = await page.evaluate(`(${measure})(${colorMath})`);
        const row = {screen, theme, width, ...measured};
        results.push(row);
        for (const item of measured.text.filter(t => t.ratio + .01 < t.minimum)) failures.push({screen, theme, width, ...item});
        if ([390, 1440].includes(width)) await page.screenshot({path: new URL(`${screen}-${theme}-${width}.png`, out).pathname});
      }
    }
  }
  const compact = results.map(row => {
    const pairs = new Map();
    for (const item of row.text) {
      const key = JSON.stringify([item.color, item.background, item.minimum]);
      if (!pairs.has(key)) pairs.set(key, {...item, count: 0});
      pairs.get(key).count++;
    }
    return {...row, text: [...pairs.values()]};
  });
  await writeFile(new URL('measurements.json', out), JSON.stringify(compact, null, 2) + '\n');
  await writeFile(new URL('contrast-failures.json', out), JSON.stringify(failures, null, 2) + '\n');
  assert.equal(results.filter(row => row.overflow).length, 0, 'no document overflow');
  assert.equal(results.flatMap(row => row.targets).filter(t => t.width < 43.99 || t.height < 43.99).length, 0, '44px action targets');
  assert.equal(results.flatMap(row => row.borders).filter(b => b.inside < 3 || b.outside < 3).length, 0, 'required boundaries against both adjacent surfaces');
  assert.equal(failures.length, 0, `composed text contrast: ${JSON.stringify(failures.slice(0, 8))}`);
  return {pageErrors: []};
}
