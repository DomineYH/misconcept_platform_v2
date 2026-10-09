import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {chromium} from 'playwright';

const base = `http://127.0.0.1:${process.argv[2]}`;
const fixture = JSON.parse(await readFile(new URL('./fixtures/s2_draft.json', import.meta.url), 'utf8'));
const browser = await chromium.launch({headless:true});
const page = await browser.newPage({viewport:{width:390, height:900}});
const errors = [];
page.on('pageerror', error => errors.push(error.message));
await page.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());

try {
  await page.goto(`${base}/login`);
  await page.locator('[name=username]').fill('draft_admin');
  await page.locator('[name=password]').fill('s2-browser-password');
  await Promise.all([page.waitForURL(url => url.pathname !== '/login'), page.locator('button[type=submit]').click()]);
  await page.goto(`${base}/admin/scenarios`);
  await page.getByRole('link', {name:'통합 초안 작성', exact:true}).click();
  await page.getByText('현재 상태: 초안 · 버전 1', {exact:true}).waitFor();
  await page.getByLabel('제목', {exact:true}).fill(fixture.title);
  await page.getByLabel('Draft browser group', {exact:true}).check();
  await page.locator('[data-step="1"]').click();
  await page.getByLabel('공개 문제 상황', {exact:true}).fill(fixture.config.problem.public_text);
  await page.locator('[data-step="2"]').click();
  await page.getByLabel('내부 학생 프로필 (선택)', {exact:true}).fill(fixture.config.student.internal_profile);
  await page.getByLabel('학생봇 제공자 / 모델', {exact:true}).selectOption('1');
  await page.locator('[data-step="3"]').click();
  await page.getByLabel('멘토 행동 지시', {exact:true}).fill(fixture.config.mentor.behavior_instruction);
  await page.locator('[id="mentor.mode"]').selectOption('off');
  await page.locator('[data-step="4"]').click();
  await page.getByRole('button', {name:'분류 기준 추가', exact:true}).click();
  await page.getByLabel('분류 1 판정 기준', {exact:true}).fill('숨긴 기준');
  await page.getByLabel('발화 분류 사용', {exact:true}).uncheck();
  assert(await page.getByRole('button', {name:'게시', exact:true}).isEnabled());
  const [created] = await Promise.all([
    page.waitForResponse(response => response.url() === `${base}/admin/scenarios` && response.request().method() === 'POST'),
    page.getByRole('button', {name:'초안으로 저장', exact:true}).click()
  ]);
  assert.equal(created.status(), 201, await created.text());
  await page.getByText('초안을 저장했습니다.', {exact:true}).waitFor();
  const savedUrl = new URL(page.url());
  assert.match(savedUrl.pathname, /^\/admin\/scenarios\/\d+\/edit$/);
  await page.reload();
  await page.getByText('현재 상태: 초안 · 버전 1', {exact:true}).waitFor();
  assert.equal(await page.getByLabel('제목', {exact:true}).inputValue(), fixture.title);
  await page.goto(`${base}/admin/scenarios`);
  assert.equal(await page.locator('[name="framework_id"], [name="student_template_id"], [name="tutor_template_id"], #create-form, #edit-form').count(), 0);
  assert(!(await page.content()).includes(fixture.config.student.internal_profile));
  await page.goto(`${base}/admin/ai`);
  await page.getByRole('heading', {name:'AI 연결·모델', exact:true}).waitFor();
  await page.locator('[data-provider="openai"]').getByRole('button', {name:'키 교체', exact:true}).click();
  const impact = page.getByText(`시나리오 #${savedUrl.pathname.split('/')[3]} ${fixture.title} · student`, {exact:false});
  await impact.waitFor();
  assert((await impact.innerText()).includes('초안'));
  assert((await impact.innerText()).includes('새 수업 실행 제외'));
  await page.goto(`${base}/admin/scenarios`);
  await page.locator(`a[href="${savedUrl.pathname}"]`).click();
  await page.getByText('현재 상태: 초안 · 버전 1', {exact:true}).waitFor();
  await page.locator('[data-step="2"]').click();
  assert.equal(await page.getByLabel('내부 학생 프로필 (선택)', {exact:true}).inputValue(), fixture.config.student.internal_profile);
  assert.equal(await page.getByLabel('학생봇 최대 출력 토큰', {exact:true}).inputValue(), '900');
  await page.locator('[data-step="3"]').click();
  await page.locator('[id="mentor.mode"]').selectOption('manual');
  assert.equal(await page.getByLabel('멘토 행동 지시', {exact:true}).inputValue(), fixture.config.mentor.behavior_instruction);
  await page.locator('[data-step="4"]').click();
  await page.getByLabel('발화 분류 사용', {exact:true}).check();
  assert.equal(await page.getByLabel('분류 1 판정 기준', {exact:true}).inputValue(), '숨긴 기준');
  await page.getByRole('button', {name:'초안으로 저장', exact:true}).click();
  await page.getByText('초안을 저장했습니다.', {exact:true}).waitFor();
  const path = savedUrl.pathname.replace('/edit', '');
  const stored = await (await page.request.get(base + path)).json();
  assert.equal(stored.config_version, 2);
  assert.equal(stored.config.mentor.mode, 'manual');
  assert.equal(stored.config.problem.public_text, fixture.config.problem.public_text);
  assert.equal(stored.config.analysis.rubric[0].criteria, '숨긴 기준');
  await page.locator('[data-step="0"]').click();
  await page.getByLabel('제목', {exact:true}).fill('');
  const [invalid] = await Promise.all([
    page.waitForResponse(response => response.url() === base + path + '/update'),
    page.getByRole('button', {name:'초안으로 저장', exact:true}).click()
  ]);
  assert.equal(invalid.status(), 422);
  await page.locator('#editor-error-links button').click();
  assert(await page.getByLabel('제목', {exact:true}).evaluate(el => el === document.activeElement));
  await page.getByLabel('제목', {exact:true}).fill('내 입력');
  const remote = await page.evaluate(async ({path, stored}) => {
    const response = await fetch(path + '/update', {method:'POST', headers:{'content-type':'application/json',
      'x-csrf-token':document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/)[1]},
      body:JSON.stringify({title:'Remote', subject:stored.subject, target_grade:stored.target_grade,
        groups:stored.groups, config:stored.config, config_schema_version:1, action:'save_draft', expected_version:2})});
    return response.status;
  }, {path, stored});
  assert.equal(remote, 200);
  await page.getByRole('button', {name:'초안으로 저장', exact:true}).click();
  await page.getByText('다른 관리자가 변경했습니다. 내 입력을 보존했습니다. 최신 버전: 3', {exact:true}).waitFor();
  assert.equal(await page.getByLabel('제목', {exact:true}).inputValue(), '내 입력');
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', {name:'최신 내용 다시 불러오기', exact:true}).click();
  await page.getByText('현재 상태: 초안 · 버전 3', {exact:true}).waitFor();
  assert.equal(await page.getByLabel('제목', {exact:true}).inputValue(), 'Remote');
  await page.getByLabel('활성', {exact:true}).uncheck();
  await page.getByRole('button', {name:'초안으로 저장', exact:true}).click();
  await page.getByText('초안을 저장했습니다.', {exact:true}).waitFor();
  const inactive = await (await page.request.get(base + path)).json();
  assert.equal(inactive.config_version, 4);
  assert.equal(inactive.is_active, false);
  await page.goto(`${base}/admin/scenarios`);
  await page.goto(`${base}/admin/scenarios`);
  await page.locator(`a[href="${savedUrl.pathname}"]`).click();
  await page.getByText('현재 상태: 초안 · 버전 4', {exact:true}).waitFor();
  assert.equal(await page.getByLabel('제목', {exact:true}).inputValue(), 'Remote');
  assert.equal(await page.locator('#public-preview script').count(), 0);
  const {default: checkPublication} = await import('./check_s2_publication_browser.mjs');
  await checkPublication(page, base);
  const {default: checkConversion} = await import('./check_s2_conversion_browser.mjs');
  await checkConversion(page, base);
  const {default: checkSnapshots} = await import('./check_s2_snapshots_browser.mjs');
  await checkSnapshots(page, base);
  const {default: checkHistory} = await import('./check_s2_history_browser.mjs');
  await checkHistory(page, base);
  assert.deepEqual(errors, []);
  console.log('PASS: real draft browser saved and reopened hidden values and model options');
} finally {
  await browser.close();
}
