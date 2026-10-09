import assert from 'node:assert/strict';

export default async function checkScenarioAuthoring(page) {
  const base = new URL(page.url()).origin;
  await page.goto(`${base}/fixtures/s2/editor`);
  await page.getByRole('heading', {name:'시나리오 작성', exact:true}).waitFor({timeout:3000});
  for (const width of [1280, 390]) {
    await page.setViewportSize({width, height:900});
    await page.getByRole('button', {name:'1. 기본 정보', exact:true}).focus();
    await page.keyboard.press('Enter');
    await page.getByLabel('제목', {exact:true}).fill('분수 {1/3} 비교');
    await page.getByRole('button', {name:'다음 단계', exact:true}).click();
    assert(await page.getByRole('heading', {name:'2. 문제 상황'}).isVisible());
    await page.getByLabel('공개 문제 상황').fill('같은 전체를 나누어 보세요.');
    await page.getByRole('button', {name:'이전 단계', exact:true}).click();
    assert.equal(await page.getByLabel('제목', {exact:true}).inputValue(), '분수 {1/3} 비교');
    assert((await page.locator('#public-preview').innerText()).includes('같은 전체를 나누어 보세요.'));
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'no page overflow');
    assert(await page.getByRole('region', {name:'실행 설정 확인'}).isVisible());
  }
  return {pageErrors:[]};
}
