import assert from 'node:assert/strict';

export default async function checkResponsivePrototype(page) {
  page.setDefaultTimeout(5000);
  const base = new URL(page.url()).origin;
  const writes = [];
  page.on('request', request => { if (request.method() !== 'GET') writes.push(request.url()); });
  const widths = [320,390,767,768,769,800,801,1024,1279,1280,1440];
  const routes = ['/chat','/fixtures/s2/editor','/fixtures/s2/editor?comparison=1','/fixtures/responsive/conversation'];
  const go = async (route, variant = 'A') => {
    await page.goto(`${base}${route}${route.includes('?') ? '&' : '?'}variant=${variant}`);
    await page.locator('#prototype-switcher').waitFor();
  };
  const overflow = async label => assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `page overflow: ${label}`);
  for (const theme of ['light','dark']) {
    await page.emulateMedia({colorScheme:theme});
    for (const route of routes) {
      await go(route);
      for (const width of widths) {
        await page.setViewportSize({width,height:900});
        await overflow(`${theme} ${route} ${width}`);
        if (route === '/chat') {
          assert.equal(await page.locator('.mobile-tabs').isVisible(), width <= 768);
          if (width <= 768) {
            await page.locator('[data-panel="scenario"]').click();
            await overflow(`chat context ${width}`);
            await page.locator('[data-panel="chat"]').click();
          }
          await page.locator('#teacher-input').fill('입력 보존 검사');
          const box = await page.locator('#teacher-input').boundingBox();
          assert(box.width > 80 && box.y + box.height < 820, 'composer above switcher');
        } else if (route.includes('comparison=1')) {
          const panels = page.locator('[data-comparison-panel]');
          if (width < 768) {
            const tabs = page.getByRole('tablist',{name:'비교 패널'});
            await tabs.getByRole('tab').first().focus();
            await page.keyboard.press('End');
            assert(await panels.nth(2).isVisible());
            await page.keyboard.press('ArrowLeft');
            assert(await panels.nth(1).isVisible());
            assert.equal(new URL(page.url()).searchParams.get('variant'), 'A');
          } else if (width < 1280) {
            assert(await page.locator('.comparison-grid').evaluate(el => getComputedStyle(el).overflowX === 'auto' && (el.clientWidth >= 992 || el.scrollWidth > el.clientWidth)));
            await panels.nth(2).getByRole('button',{name:'학생 응답 채택'}).scrollIntoViewIfNeeded();
          } else {
            const boxes = await panels.evaluateAll(els => els.map(el => el.getBoundingClientRect().y));
            assert.equal(boxes[0], boxes[2]);
          }
        } else if (route.includes('editor')) {
          const layout = await page.locator('.scenario-layout').evaluate(el => getComputedStyle(el).display);
          assert.equal(layout, width <= 800 ? 'block' : 'grid');
        } else {
          const boxes = await page.locator('.conversation-panels article').evaluateAll(els => els.map(el => ({x:el.offsetLeft,y:el.offsetTop})));
          if (width <= 800) assert(boxes[0].y < boxes[1].y);
          if (width >= 1280) assert.equal(boxes[0].y, boxes[2].y);
          for (const panel of await page.locator('.conversation-panels article').all()) {
            await panel.getByRole('button',{name:'학생 응답 채택'}).scrollIntoViewIfNeeded();
            assert(await panel.isVisible());
          }
        }
      }
    }
  }
  for (const variant of ['B','C']) {
    for (const width of [390,1024,1440]) {
      await page.setViewportSize({width,height:900});
      for (const route of routes) {
        await go(route, variant);
        await overflow(`${variant} ${route} ${width}`);
        if (width > 800 && route === '/chat') {
          const context = await page.locator('#scenario-panel').boundingBox();
          const work = await page.locator('#chat-panel').boundingBox();
          assert(variant === 'B' ? work.y >= context.y + context.height : work.x < context.x + context.width, 'stack versus overlay');
        }
        if (width > 800 && route.includes('/conversation')) {
          const cards = await page.locator('.conversation-panels article').evaluateAll(els => els.map(el => el.getBoundingClientRect().y));
          assert(variant === 'B' ? cards[0] < cards[1] : cards[0] === cards[1], 'one column versus two');
        }
        if (variant === 'C' && !route.includes('comparison=1')) {
          for (const details of await page.locator('.prototype-context').all()) {
            assert(!(await details.getAttribute('open')));
            await details.locator('summary').click();
            assert.equal(await details.getAttribute('open'), '');
          }
        }
      }
    }
  }
  for (const count of [1,2,3]) {
    for (const width of [390,1024,1440]) {
      await page.setViewportSize({width,height:900});
      await go(`/fixtures/responsive/conversation?panels=${count}`);
      assert.equal(await page.locator('.conversation-panels article').count(), count);
      await overflow(`new ${count} ${width}`);
      await go(`/fixtures/s2/editor?comparison=1&ready=${count}`);
      assert.equal(await page.locator('[data-comparison-panel]').count(), count);
      await overflow(`legacy ${count} ${width}`);
    }
  }
  for (const [route,input] of [['/chat','#teacher-input'], ['/fixtures/s2/editor','[data-field="title"]'], ['/fixtures/responsive/conversation','#conversation-question']]) {
    await go(route);
    await page.locator(input).fill('변형 전환 뒤에도 유지');
    await page.locator(input).press('ArrowRight');
    assert.equal(new URL(page.url()).searchParams.get('variant'),'A');
    await page.locator('#prototype-switcher button').last().click();
    assert.equal(await page.locator(input).inputValue(),'변형 전환 뒤에도 유지');
    await page.keyboard.press('ArrowRight');
    assert.equal(new URL(page.url()).searchParams.get('variant'),'C');
    await page.reload();
    assert.equal(await page.locator('html').getAttribute('data-variant'),'C');
    await page.locator('#prototype-switcher button').last().click();
    assert.equal(new URL(page.url()).searchParams.get('variant'),'A');
  }
  await page.evaluate(() => {
    const editable = document.createElement('div'); editable.contentEditable = 'true';
    editable.id = 'editable-check'; document.body.append(editable); editable.focus();
  });
  await page.keyboard.press('ArrowLeft');
  assert.equal(new URL(page.url()).searchParams.get('variant'),'A');
  await page.locator('#editable-check').evaluate(el => el.remove());
  await page.setViewportSize({width:1024,height:900});
  await go('/chat');
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await page.locator('#scenario-panel-toggle').click();
  await page.reload();
  assert.equal(await page.locator('#scenario-panel-toggle').getAttribute('aria-expanded'),'false');
  await page.locator('#teacher-input').fill('단축키 제외');
  await page.locator('#teacher-input').press('Control+b');
  assert.equal(await page.locator('#scenario-panel-toggle').getAttribute('aria-expanded'),'false');
  await page.locator('#prototype-switcher button').first().focus();
  await page.keyboard.press('Control+b');
  assert.equal(await page.locator('#scenario-panel-toggle').getAttribute('aria-expanded'),'true');
  await page.keyboard.press('Meta+b');
  assert.equal(await page.locator('#scenario-panel-toggle').getAttribute('aria-expanded'),'false');
  await go('/fixtures/s2/editor');
  await page.getByLabel('제목',{exact:true}).fill('저장 시도 뒤 입력');
  await page.getByRole('button',{name:'초안으로 저장',exact:true}).click();
  assert.equal(await page.getByLabel('제목',{exact:true}).inputValue(),'저장 시도 뒤 입력');
  for (const theme of ['light','dark']) {
    await page.emulateMedia({colorScheme:theme});
    assert.equal(await page.evaluate(() => getComputedStyle(document.body).backgroundColor), theme === 'light' ? 'rgb(251, 250, 247)' : 'rgb(14, 15, 28)');
  }
  // Half CSS viewport is reflow evidence, not browser zoom or mobile keyboard emulation.
  for (const width of [512,720]) {
    await page.setViewportSize({width,height:450});
    for (const route of routes) {
      await go(route);
      await overflow(`200% equivalent ${route} ${width}`);
      const input = page.locator(route === '/chat' ? '#teacher-input' : route.includes('/conversation') ? '#conversation-question' : '[data-field="title"]');
      await input.evaluate(el => el.scrollIntoView({block:'center', behavior:'instant'}));
      await input.fill('확대 대응 입력');
      const box = await input.boundingBox();
      assert(box.y >= 0 && box.y + box.height < 380, `input reachable above test bar: ${route} ${width} ${JSON.stringify(box)}`);
    }
  }
  await go('/fixtures/s2/editor');
  for (const step of await page.locator('.scenario-steps button').all()) {
    await step.focus(); await page.keyboard.press('Enter');
    assert.equal(await step.getAttribute('aria-current'),'step');
  }
  assert.equal(await page.locator('.scenario-steps button').count(),5);
  assert(!/INTERNAL_/.test(await page.locator('.scenario-preview').innerText()));
  for (const route of ['/chat','/fixtures/s2/editor','/fixtures/s2/editor?variant=invalid','/admin/scenarios/new?variant=A']) {
    await page.goto(`${base}${route}`);
    assert.equal(await page.locator('#prototype-switcher').count(),0);
    assert.equal(await page.locator('script[src="/fixtures/responsive/guard.js"]').count(),0);
  }
  assert.deepEqual(writes, [], 'fixture mutations never reach HTTP');
  return {pageErrors:[]};
}
