import assert from 'node:assert/strict';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  await page.emulateMedia({reducedMotion: 'reduce'});
  for (const width of [769, 1024, 1440]) {
    await page.setViewportSize({width, height: 900});
    await page.goto(`${base}/chat?long=1&mentor_manual=1&mentor_history=1`);
    const ratio = await page.locator('#scenario-panel').evaluate(el => el.getBoundingClientRect().width / el.parentElement.getBoundingClientRect().width);
    assert(Math.abs(ratio - .35) < .005, `35% information panel at ${width}, actual ${ratio}`);
    await page.locator('#scenario-panel-toggle').click();
    await page.waitForFunction(() => document.querySelector('#scenario-panel').getBoundingClientRect().width === 48);
    await page.reload();
    assert.equal(await page.locator('#scenario-panel-toggle').getAttribute('aria-expanded'), 'false');
    await page.keyboard.press('Control+b');
    await page.waitForFunction(() => document.querySelector('#scenario-panel-toggle').getAttribute('aria-expanded') === 'true');
    const input = page.locator('#teacher-input');
    await input.fill('유지할 초안');
    await input.focus();
    await page.keyboard.press('Control+b');
    assert.equal(await page.locator('#scenario-panel-toggle').getAttribute('aria-expanded'), 'true', 'editing shortcut is excluded');
  }
  await page.setViewportSize({width:390, height:844});
  await page.locator('.mobile-tab[data-panel="scenario"]').click();
  await page.setViewportSize({width:390, height:600});
  assert(await page.locator('#scenario-panel').isVisible(), 'height resize keeps selected mobile information');
  const info = page.getByRole('tab', {name:'학생 정보', exact:true});
  await info.focus();
  await page.keyboard.press('ArrowRight');
  assert.equal(await page.getByRole('tab', {name:'문제 상황', exact:true}).getAttribute('aria-selected'), 'true');
  await page.getByRole('tab', {name:'대화', exact:true}).click();
  assert.equal(await page.locator('#teacher-input').inputValue(), '유지할 초안');
  const box = await page.locator('#teacher-form').boundingBox();
  assert(box.y + box.height <= 600, 'composer remains inside available viewport');
  for (const width of [512,720]) {
    await page.setViewportSize({width,height:450});
    await page.locator('#teacher-input').fill('짧은 높이에서도 초안과 전송에 접근합니다.\n마지막 줄');
    const form = await page.locator('#teacher-form').boundingBox();
    assert(form.y + form.height <= 450, 'reflow preserves composer access');
  }
  return {pageErrors:[]};
}
