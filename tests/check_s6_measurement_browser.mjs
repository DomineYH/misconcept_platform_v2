// Real localhost lesson rendering; all provider traffic stays in the Python SDK mock.
import assert from 'node:assert/strict';
import {chromium} from 'playwright';

const base = `http://127.0.0.1:${process.argv[2]}`;
const browser = await chromium.launch({headless:true});
const page = await browser.newPage();
const errors = [];
page.on('pageerror', error => errors.push(error.message));
await page.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
await page.exposeFunction('visibleBody', () => console.log(JSON.stringify({kind:'visible'})));
await page.addInitScript(() => {
  const fetch = window.fetch;
  window.fetch = (...args) => {
    if (String(args[0]).endsWith('/turns/stream') && args[1]?.method === 'POST') {
      window.measurement = {start:performance.now(), visible:null, completed:null};
    }
    return fetch(...args);
  };
  document.addEventListener('DOMContentLoaded', () => {
    new MutationObserver(() => {
      const m = window.measurement;
      if (!m) return;
      const bodies = [...document.querySelectorAll('.message-student .message-bubble')];
      const body = bodies.at(-1);
      // Only the new synthetic body counts; reasoning/status/empty deltas cannot match.
      if (m.visible === null && body?.innerText.includes('First body') && body.getClientRects().length) {
        m.visible = performance.now();
        void window.visibleBody();
      }
      if (m.visible !== null && m.completed === null && body?.innerText.includes('Saved body') && !document.querySelector('#teacher-input').disabled) {
        m.completed = performance.now();
      }
    }).observe(document.documentElement, {childList:true, subtree:true, characterData:true, attributes:true});
  });
});
try {
  await page.goto(`${base}/login`);
  await page.locator('[name=username]').fill('live_owner');
  await page.locator('[name=password]').fill('BROWSER-PASSWORD-SENTINEL');
  await Promise.all([page.waitForURL(url => url.pathname !== '/login'), page.locator('button[type=submit]').click()]);
  await page.goto(`${base}/scenarios/1`);
  await page.waitForFunction(() => document.querySelector('#teacher-form').dataset.studentStream);
  for (let repeat = 0; repeat < 6; repeat++) {
    await page.evaluate(() => { window.measurement = null; });
    await page.locator('#teacher-input').fill('Synthetic measurement question');
    await page.locator('#teacher-input').press('Enter');
    await page.waitForFunction(() => window.measurement?.completed !== null && window.measurement?.completed !== undefined);
    const sample = await page.evaluate(() => ({...window.measurement}));
    assert(sample.start <= sample.visible && sample.visible <= sample.completed);
    console.log(JSON.stringify({kind:'sample', repeat, browser:browser.version(), raw_browser_ms:sample,
      first_visible_body_ms:sample.visible - sample.start, completion_display_ms:sample.completed - sample.start}));
  }
  assert.equal(errors.length, 0);
} finally {
  await browser.close();
}
