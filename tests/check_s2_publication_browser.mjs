import assert from 'node:assert/strict';

export default async function checkPublication(page, base) {
  await page.goto(`${base}/admin/scenarios/new`);
  await page.getByText('현재 상태: 초안 · 버전 1', {exact:true}).waitFor();
  await page.getByLabel('제목', {exact:true}).fill('실제 게시');
  await page.locator('[data-step="2"]').click();
  assert.equal(await page.getByLabel('학생봇 제공자 / 모델', {exact:true}).inputValue(), '2');
  assert.equal(await page.getByLabel('학생봇 최대 출력 토큰', {exact:true}).inputValue(), '1600');
  const [invalid] = await Promise.all([
    page.waitForResponse(r => r.url() === `${base}/admin/scenarios` && r.request().method() === 'POST'),
    page.getByRole('button', {name:'게시', exact:true}).click()
  ]);
  assert.equal(invalid.status(), 422);
  await page.getByRole('button', {name:'학습목표 보완 필요', exact:true}).click();
  assert(await page.getByLabel('학습목표', {exact:true}).evaluate(el => el === document.activeElement));
  assert.equal(await page.getByLabel('제목', {exact:true}).inputValue(), '실제 게시');
  assert(Number(await page.locator('[data-errors="1"]').innerText()) > 0);
  await page.getByLabel('공개 문제 상황', {exact:true}).fill('1/3과 1/5을 비교한다 {"literal":true}');
  await page.getByLabel('학습목표', {exact:true}).fill('같은 전체에서 비교한다');
  await page.locator('[data-step="2"]').click();
  await page.getByLabel('학생봇 이름', {exact:true}).fill('민수');
  await page.getByLabel('목표 오개념', {exact:true}).fill('분모가 크면 크다');
  await page.getByLabel('학생봇 행동 지시', {exact:true}).fill('생각을 설명한다');
  await page.locator('[data-step="3"]').click();
  await page.locator('[id="mentor.mode"]').selectOption('off');
  await page.locator('[data-step="4"]').click();
  await page.getByLabel('분석 맥락', {exact:true}).fill('교사 질문');
  await page.getByLabel('정답·기대 이해', {exact:true}).fill('같은 전체');
  await page.getByLabel('분석 지시·강조점', {exact:true}).fill('근거 확인');
  await page.getByLabel('발화 분류 사용', {exact:true}).uncheck();
  assert.equal(await page.getByLabel('사후 분석 제공자 / 모델', {exact:true}).inputValue(), '2');
  const changedDefaults = await page.evaluate(async () => {
    const response = await fetch('/admin/ai/models/2/update', {
      method:'POST', headers:{'content-type':'application/json', 'x-csrf-token':document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/)[1]},
      body:JSON.stringify({expected_version:1, display_name:'New defaults', enabled:true,
        default_options:{max_output_tokens:2048, reasoning:{effort:'none'}, temperature:0}})
    });
    return response.status;
  });
  assert.equal(changedDefaults, 200);
  const [published] = await Promise.all([
    page.waitForResponse(r => r.url() === `${base}/admin/scenarios` && r.request().method() === 'POST'),
    page.getByRole('button', {name:'게시', exact:true}).click()
  ]);
  assert.equal(published.status(), 201, await published.text());
  assert.equal((await published.json()).status, 'published');
  await page.getByText('게시했습니다.', {exact:true}).waitFor();
  const path = new URL(page.url()).pathname.replace('/edit', '');
  const saved = await (await page.request.get(base + path)).json();
  assert.deepEqual(saved.config.student.resolved_model_config.options, {max_output_tokens:1600, reasoning:{effort:'none'}, temperature:0.7});
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('button', {name:'초안으로 저장', exact:true}).click();
  assert.equal((await (await page.request.get(base + path)).json()).status, 'published');
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', {name:'초안으로 저장', exact:true}).click();
  await page.getByText('초안을 저장했습니다.', {exact:true}).waitFor();
  assert.equal((await (await page.request.get(base + path)).json()).config_version, 2);
  await page.reload();
  await page.getByText('현재 상태: 초안 · 버전 2', {exact:true}).waitFor();
  await page.locator('[data-step="2"]').click();
  assert.equal(await page.getByLabel('학생봇 최대 출력 토큰', {exact:true}).inputValue(), '1600');
  await page.goto(`${base}/admin/scenarios/new`);
  await page.getByText('현재 상태: 초안 · 버전 1', {exact:true}).waitFor();
  await page.locator('[data-step="2"]').click();
  assert.equal(await page.getByLabel('학생봇 최대 출력 토큰', {exact:true}).inputValue(), '2048');
  await page.goto(`${base}/admin/scenarios`);
  await page.locator('.scenario-card', {hasText:'Live conversion review'}).getByRole('link', {name:'수정', exact:true}).click();
  await page.getByText('현재 상태: 초안 · 버전 1', {exact:true}).waitFor();
  await page.getByLabel('이 버전의 변환 경고를 검토했습니다.').check();
  assert(await page.getByRole('button', {name:'게시', exact:true}).isDisabled());
  await page.getByRole('button', {name:'공개 문제 상황 보완 필요', exact:true}).click();
  await page.getByLabel('공개 문제 상황', {exact:true}).fill('보완한 공개 문제');
  await page.getByRole('button', {name:'초안으로 저장', exact:true}).click();
  await page.getByText('초안을 저장했습니다.', {exact:true}).waitFor();
  assert(!(await page.getByLabel('이 버전의 변환 경고를 검토했습니다.').isChecked()), 'a new revision needs a new acknowledgement');
  await page.getByLabel('이 버전의 변환 경고를 검토했습니다.').check();
  assert(await page.getByRole('button', {name:'게시', exact:true}).isEnabled(), 'server revalidation resolves the corrected blocker');
  await page.getByRole('button', {name:'게시', exact:true}).click();
  await page.getByText('게시했습니다.', {exact:true}).waitFor();
  const reviewed = await (await page.request.get(base + new URL(page.url()).pathname.replace('/edit', ''))).json();
  assert.equal(reviewed.config_version, 3);
  assert.equal(reviewed.review_required, false);
  assert.deepEqual(reviewed.review_reasons, []);
  assert(await page.locator('#conversion-review').isHidden());
}
