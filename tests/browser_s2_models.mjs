import assert from 'node:assert/strict';

export default async function checkScenarioModelSelection(page) {
  const base = new URL(page.url()).origin;
  await page.goto(`${base}/fixtures/s2/editor`);
  await page.getByRole('button', {name:'3. 학생봇', exact:true}).click();
  const model = page.getByLabel('시나리오 공통 모델', {exact:true});
  await model.waitFor({timeout:3000});
  assert.equal(await model.inputValue(), '1', 'saved selection is not overwritten by role default');
  assert.equal(await page.getByLabel('학생봇 최대 출력 토큰').inputValue(), '900', 'saved options are frozen');
  await model.selectOption('2');
  assert.equal(await page.getByLabel('학생봇 최대 출력 토큰').inputValue(), '2048', 'deliberate switch resets to defaults visibly');
  assert(await page.getByLabel('학생봇 다양성').isVisible(), 'Claude supported sampling');
  assert(await page.getByLabel('학생봇 추론 수준', {exact:true}).count() === 0, 'no unsupported OpenAI options');
  await model.selectOption('3');
  assert(await page.getByLabel('학생봇 사고 토큰 예산 (-1 자동, 0 끄기)').isVisible(), 'Gemini nested option');
  assert(await page.getByLabel('학생봇 다양성').isVisible());
  for (const [id, text] of [['4','미검증'], ['5','검증 중'], ['6','실패'], ['7','재검증 필요'], ['8','기능 정의 필요'], ['9','연결 사용 불가'], ['10','비활성']]) {
    const option = model.locator(`option[value="${id}"]`);
    assert(await option.evaluate(el => el.disabled), text);
    assert((await option.innerText()).includes(text), text);
  }
  await page.goto(`${base}/fixtures/s2/editor?new=1`);
  await page.getByRole('button', {name:'3. 학생봇', exact:true}).click();
  assert.equal(await model.inputValue(), '2', 'available new-authoring default copied once');
  await page.getByRole('button', {name:'4. 멘토', exact:true}).click();
  assert.equal(await page.locator('[id="mentor.resolved_model_config"]').inputValue(), '2', 'common default initializes active mentor');
  assert((await page.locator('#mentor-model-status').innerText()).includes('성공'));
  return {pageErrors:[]};
}
