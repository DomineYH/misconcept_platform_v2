import assert from 'node:assert/strict';

// The production Jinja screen, rendered only by the isolated browser server.
export default async function checkApiUsage(page) {
  const base = new URL(page.url()).origin;
  const cells = async id => (await page.locator(`#usage-row-${id} td`).allTextContents()).map(text => text.trim());
  const checks = [];
  for (const width of [1280, 390]) {
    await page.setViewportSize({width, height:900});
    await page.goto(`${base}/admin/api-usage`);
    assert.equal(await page.locator('h1').last().innerText(), 'API 사용량');
    assert.equal(await page.locator('#known-cost').innerText(), '$0.375000');
    assert.equal(await page.locator('#unpriced-attempts').innerText(), '4');
    assert.equal(await page.locator('#model-list-calls').innerText(), '2');
    assert.equal(await page.locator('#generation-usage tbody tr').count(), 7);
    assert.equal(await page.locator('#model-list-usage tbody tr').count(), 2);
    assert.equal(await page.locator('#generation-usage #usage-row-8').count(), 0);
    assert.equal(await page.locator('#model-list-usage #usage-row-9').count(), 1);
    const zero = await cells(1);
    assert.deepEqual(zero.slice(1, 5), ['openai', 'observed-zero', '학생봇 (student)', 'student']);
    assert.deepEqual(zero.slice(8), ['0', '0', '0', '0', '0', '0', '$0.000000', '2026-10-01']);
    assert(zero[7].includes('첫 출력: 2026-10-01 09:00:01'));
    assert(zero[7].includes('재시도 대기 (ms): 0'));
    const failed = await cells(2), retry = await cells(3);
    assert.equal(failed[1], 'anthropic');
    assert.equal(failed[3], '사후 분석 (analysis)');
    assert(failed[5].includes('실패') && failed[5].includes('timeout'));
    assert.equal(failed[6], '1');
    assert.deepEqual(failed.slice(8), ['알 수 없음', '알 수 없음', '알 수 없음', '알 수 없음', '알 수 없음', '알 수 없음', '산정 불가', '알 수 없음']);
    assert(retry[5].includes('성공'));
    assert.equal(retry[6], '2');
    assert.deepEqual(retry.slice(8, 14), ['100', '20', '30', '10', '알 수 없음', '120']);
    assert(retry[7].includes('재시도 대기 (ms): 1000'));
    const legacy = await cells(4);
    assert.equal(legacy[0], '기존 기록');
    assert.equal(legacy[3], '멘토 (tutor)');
    assert.equal(legacy[4], '알 수 없음');
    assert(legacy[5].includes('알 수 없음') && !legacy[5].includes('성공'));
    assert.equal(legacy[6], '알 수 없음');
    assert(legacy[7].includes('시작: 2026-01-01 00:00:00'));
    assert.deepEqual(legacy.slice(8), ['10', '2', '알 수 없음', '알 수 없음', '알 수 없음', '12', '$0.125000', '알 수 없음']);
    const partial = await cells(5);
    assert.equal(partial[1], 'google');
    assert(partial[5].includes('일부 / 알 수 없음'));
    assert.deepEqual(partial.slice(8, 11), ['1000', '알 수 없음', '100']);
    assert.equal(partial[14], '산정 불가');
    assert((await cells(6))[5].includes('중단'));
    assert((await cells(7))[5].includes('취소'));
    assert.equal((await cells(8))[14], '산정 불가');
    assert.equal((await cells(9))[14], '$9.000000');
    assert((await page.locator('main').innerText()).includes('전체 과금액이 아닙니다'));
    assert.equal(await page.locator('th[scope="col"]').count(), 32);
    assert.equal(await page.locator('caption').count(), 2);
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'page fits viewport; only the tables scroll');
    assert(!(await page.content()).includes('PRIVATE'), 'no key, prompt or response is required by fixture');

    // Reach the first table through the actual Tab order, including mobile navigation.
    await page.locator('.skip-link').focus();
    for (let i = 0; i < 12; i++) {
      await page.keyboard.press('Tab');
      if (await page.evaluate(() => document.activeElement?.getAttribute('aria-labelledby') === 'generation-usage-caption')) break;
    }
    assert(await page.evaluate(() => document.activeElement?.getAttribute('aria-labelledby') === 'generation-usage-caption'), 'keyboard reaches labelled table');
    const region = page.getByRole('region', {name:'생성 호출 및 기존 기록', exact:true});
    assert.equal(await region.evaluate(el => getComputedStyle(el).outlineStyle), 'solid', 'visible keyboard focus');
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => document.querySelector('[aria-labelledby="generation-usage-caption"]').scrollLeft > 0);
    await page.keyboard.press('Tab');
    assert(await page.evaluate(() => document.activeElement?.getAttribute('aria-labelledby') === 'model-list-usage-caption'), 'keyboard reaches separate list table');
    checks.push(`${width}px: zero/NULL, failure/retry, preserved legacy, partial totals, keyboard scrolling`);
  }
  return {checks};
}
