import assert from 'node:assert/strict';

export default async function checkScenarioSaveFailures(page) {
  const base = new URL(page.url()).origin;
  const submissions = [];
  let response = {status:422, json:{detail:[
    {path:'problem.public_text', code:'required', message:'공개 문제 상황을 입력하세요.'},
    {path:'analysis.rubric.0.criteria', code:'required', message:'판정 기준을 입력하세요.'}
  ]}};
  await page.context().addCookies([{name:'csrftoken', value:'s2-token', url:base}]);
  await page.route('**/admin/scenarios/1/update', async route => {
    submissions.push({body:route.request().postDataJSON(), csrf:route.request().headers()['x-csrf-token']});
    await route.fulfill(response);
  });
  await page.goto(`${base}/fixtures/s2/editor?published=1`);
  await page.setViewportSize({width:390, height:900});
  const publish = page.getByRole('button', {name:'수정 후 게시', exact:true});
  await publish.waitFor({timeout:3000});
  await page.getByLabel('제목', {exact:true}).fill('내 입력 {"x":1}');
  await publish.click();
  await page.locator('#editor-errors').waitFor();
  assert.equal(await page.locator('[data-errors="1"]').innerText(), '1');
  assert.equal(await page.locator('[data-errors="4"]').innerText(), '1');
  await page.getByRole('button', {name:'공개 문제 상황을 입력하세요.', exact:true}).click();
  assert(await page.getByLabel('공개 문제 상황').evaluate(el => el === document.activeElement));
  assert.equal(await page.getByLabel('공개 문제 상황').getAttribute('aria-invalid'), 'true');
  await page.getByRole('button', {name:'판정 기준을 입력하세요.', exact:true}).click();
  assert(await page.getByLabel('분류 1 판정 기준').evaluate(el => el === document.activeElement));
  assert((await page.locator('#editor-status').innerText()).includes('게시됨'), 'failed publication preserves prior status');
  assert.equal(submissions[0].body.expected_version, 1);
  assert.equal(submissions[0].body.action, 'publish');
  assert.equal(submissions[0].csrf, 's2-token');
  assert.equal(submissions[0].body.config.student.resolved_model_config.options.max_output_tokens, 900);
  response = {status:409, json:{detail:{code:'version_conflict', current_version:2}}};
  await publish.click();
  await page.getByText('다른 관리자가 변경했습니다. 내 입력을 보존했습니다. 최신 버전: 2', {exact:true}).waitFor();
  await page.getByRole('button', {name:'1. 기본 정보', exact:true}).click();
  assert.equal(await page.getByLabel('제목', {exact:true}).inputValue(), '내 입력 {"x":1}');
  assert.equal(submissions.length, 2, 'no automatic retry or overwrite');
  page.once('dialog', dialog => { assert(dialog.message().includes('입력')); return dialog.dismiss(); });
  await page.getByRole('button', {name:'최신 내용 다시 불러오기', exact:true}).click();
  assert.equal(await page.getByLabel('제목', {exact:true}).inputValue(), '내 입력 {"x":1}');
  page.once('dialog', dialog => { assert(dialog.message().includes('새 세션')); return dialog.dismiss(); });
  await page.getByRole('button', {name:'초안으로 저장', exact:true}).click();
  assert.equal(submissions.length, 2, 'cancelling unpublish does not submit');
  response = {status:200, json:{version:2, status:'draft'}};
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', {name:'초안으로 저장', exact:true}).click();
  await page.getByText('초안을 저장했습니다.', {exact:true}).waitFor();
  assert.equal(submissions.at(-1).body.action, 'save_draft');
  assert.equal(submissions.at(-1).body.expected_version, 1, 'conflict never silently updates expected version');
  response = {status:403, json:{detail:'UNSAFE SECRET DETAIL'}};
  await page.getByRole('button', {name:'게시', exact:true}).click();
  await page.getByText('권한 또는 보안 토큰을 확인하세요.', {exact:true}).waitFor();
  assert(!(await page.content()).includes('UNSAFE SECRET DETAIL'));
  return {pageErrors:[]};
}
