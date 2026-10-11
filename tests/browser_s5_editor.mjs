import assert from 'node:assert/strict';

export default async function checkComparisonDraft(page) {
  const base = new URL(page.url()).origin;
  for (const suffix of ['', '?new=1']) {
    await page.goto(`${base}/fixtures/s2/editor${suffix}`);
    await page.getByLabel('제목', {exact:true}).fill('미저장 비교 제목');
    const open = page.getByRole('button', {name:'모델 비교 열기', exact:true});
    await open.focus();
    await page.keyboard.press('Enter');
    const comparison = page.getByRole('region', {name:'동일 맥락 모델 비교', exact:true});
    await comparison.waitFor({timeout:3000});
    assert.match(await comparison.innerText(), /미저장 초안/);
    await page.getByRole('button', {name:'비교 닫기', exact:true}).click();
    assert.equal(await page.getByLabel('제목', {exact:true}).inputValue(), '미저장 비교 제목');
    assert(await open.evaluate(el => el === document.activeElement));
  }
  await page.getByRole('button', {name:'모델 비교 열기', exact:true}).click();
  assert.equal(await page.getByLabel('비교 대상').inputValue(), 'student');
  for (const [i, value] of [[1,'2'], [2,'1'], [3,'3']]) {
    assert.equal(await page.getByLabel(`패널 ${i} 제공자 / 모델`, {exact:true}).inputValue(), value);
  }
  await page.getByLabel('패널 2 제공자 / 모델', {exact:true}).selectOption('2');
  await page.getByLabel('패널 2 최대 출력 토큰').fill('700');
  assert.equal(await page.getByLabel('패널 1 최대 출력 토큰').inputValue(), '2048');
  await page.getByRole('button', {name:'패널 3 제거', exact:true}).click();
  await page.getByRole('button', {name:'패널 2 제거', exact:true}).click();
  assert.match(await page.locator('#scenario-comparison').innerText(), /단독 테스트/);
  await page.getByRole('button', {name:'완료 쌍 추가', exact:true}).click();
  await page.getByLabel('이력 1 교사').fill('왜?');
  await page.getByLabel('이력 1 학생').fill('분모가 커요.');
  await page.getByLabel('학생 현재 질문').fill('같은 전체일까요?');
  await page.getByLabel('패널 1 최대 출력 토큰').fill('700');
  await page.getByLabel('비교 대상').selectOption('mentor');
  assert.equal(await page.getByLabel('이력 1 학생').inputValue(), '분모가 커요.');
  assert(!(await page.getByLabel('학생 현재 질문').isVisible()));
  await page.getByLabel('비교 대상').selectOption('analysis');
  assert(await page.getByLabel('마지막 교사 메시지 (분석, 선택)').isVisible());
  await page.getByLabel('비교 대상').selectOption('student');
  assert.equal(await page.getByLabel('학생 현재 질문').inputValue(), '같은 전체일까요?');
  assert.equal(await page.getByLabel('패널 1 최대 출력 토큰').inputValue(), '700', 'explicit panel options survive role switching');
  return {pageErrors:[]};
}
