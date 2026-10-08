import assert from 'node:assert/strict';
import {state} from './browser_ai_connections.mjs';

export default async function checkAISettings(page) {
  page.setDefaultTimeout(8000);
  const base = new URL(page.url()).origin;
  const requests = [];
  const data = state();
  let persistDefaults = false;
  await page.route('**/admin/ai/state', route => route.fulfill({json:data}));
  await page.route('**/admin/ai/settings/update', async route => {
    requests.push(route.request().postDataJSON());
    if (persistDefaults) {
      const defaults = requests.at(-1).defaults;
      for (const role of Object.keys(defaults)) data.settings.defaults[role] = defaults[role] == null ? null : {model_config_id:defaults[role], available:true};
      data.settings.settings_version++;
    }
    await route.fulfill({json:{status:'saved'}});
  });
  for (const width of [1280, 390]) {
    await page.setViewportSize({width, height:900});
    await page.goto(`${base}/admin/ai`);
    await page.getByRole('button', {name:'작성 기본값·호출 설정 수정'}).click();
    const student = page.getByLabel('학생봇 작성 기본 모델', {exact:true});
    assert.equal(await student.inputValue(), '2', 'unavailable reference remains selected');
    assert(await student.locator('option[value="2"]').isDisabled(), 'unavailable default marked');
    assert.equal(await page.getByLabel('멘토 작성 기본 모델', {exact:true}).locator('option[value="1"]').count(), 0, 'different role success is not eligible');
    await student.selectOption('1');
    assert.equal(await page.getByLabel('현재 비밀번호').count(), 0, 'operational settings do not require reauthentication');
    await page.getByLabel('학생봇 첫 본문 제한(초)', {exact:true}).fill('181');
    await page.getByRole('button', {name:'설정 저장', exact:true}).click();
    await page.getByRole('alert').filter({hasText:'첫 본문'}).waitFor();
    assert(await page.getByRole('alert').filter({hasText:'첫 본문'}).evaluate(el => el === document.activeElement), 'invalid settings focus their error');
    assert.equal(requests.length, width === 1280 ? 0 : 1, 'invalid deadline rejected before submission');
    await page.getByLabel('학생봇 첫 본문 제한(초)', {exact:true}).fill('60');
    await page.getByLabel('전체 호출 한도', {exact:true}).fill('10');
    await page.getByRole('button', {name:'설정 저장', exact:true}).click();
    await page.getByRole('status').filter({hasText:'완료'}).waitFor();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'settings fit viewport');
  }
  assert.equal(requests.length, 2);
  assert.equal(requests[0].expected_version, 1);
  assert.deepEqual(requests[0].defaults, {student:1, mentor:null, analysis:null});
  assert.equal(requests[0].limits.total, 10);
  assert.equal(requests[0].timeouts.connect, 5);
  data.settings.defaults.student = {model_config_id:1, available:false};
  data.models[0].default_options = {max_output_tokens:-1};
  await page.getByRole('button', {name:'설정 다시 불러오기'}).click();
  await page.getByText('학생봇: 기본 모델 · 기본 모델 사용 불가', {exact:true}).waitFor();
  await page.getByRole('button', {name:'작성 기본값·호출 설정 수정'}).click();
  const student = page.getByLabel('학생봇 작성 기본 모델', {exact:true});
  assert.equal(await student.inputValue(), '1');
  assert(await student.locator('option[value="1"]').isDisabled(), 'server-unavailable current reference cannot be selected again');
  await page.getByRole('button', {name:'설정 저장', exact:true}).click();
  await page.getByRole('status').filter({hasText:'완료'}).waitFor();
  assert.equal(requests.at(-1).defaults.student, 1, 'unchanged unavailable reference survives unrelated settings save');

  data.settings.defaults = {student:null, mentor:null, analysis:null};
  data.models[0].default_options = {max_output_tokens:2048};
  for (const [index, role] of [[1,'mentor'], [2,'analysis']]) {
    Object.assign(data.providers[index], {key_registered:true, enabled:true, status:'ready'});
    data.models.push({...data.models[0], id:index + 2, provider:data.providers[index].provider,
      display_name:role, verification_state:{student:{status:'unverified'}, mentor:{status:'unverified'}, analysis:{status:'unverified'}, [role]:{status:'succeeded'}}});
  }
  await page.getByRole('button', {name:'설정 다시 불러오기'}).click();
  for (const role of ['학생봇','멘토','사후 분석']) await page.getByText(`${role}: 기본값 없음`, {exact:true}).waitFor();
  await page.getByRole('button', {name:'작성 기본값·호출 설정 수정'}).click();
  const keyboardStudent = page.getByLabel('학생봇 작성 기본 모델', {exact:true});
  await keyboardStudent.focus();
  await keyboardStudent.press('Home');
  await keyboardStudent.press('ArrowDown');
  await keyboardStudent.press('Tab');
  assert.equal(await keyboardStudent.inputValue(), '1', 'default selectable by keyboard');
  await page.getByLabel('멘토 작성 기본 모델', {exact:true}).selectOption('3');
  await page.getByLabel('사후 분석 작성 기본 모델', {exact:true}).selectOption('4');
  persistDefaults = true;
  await page.getByRole('button', {name:'설정 저장', exact:true}).click();
  await page.getByRole('status').filter({hasText:'완료'}).waitFor();
  assert.deepEqual(requests.at(-1).defaults, {student:1, mentor:3, analysis:4});
  for (const text of ['학생봇: 기본 모델 · 사용 가능','멘토: mentor · 사용 가능','사후 분석: analysis · 사용 가능']) await page.getByText(text, {exact:true}).waitFor();
  await page.getByRole('button', {name:'설정 다시 불러오기'}).click();
  await page.getByRole('button', {name:'작성 기본값·호출 설정 수정'}).click();
  assert.equal(await page.getByLabel('멘토 작성 기본 모델', {exact:true}).inputValue(), '3');
  assert.equal(await page.getByLabel('사후 분석 작성 기본 모델', {exact:true}).inputValue(), '4');
  data.models[0].verification_state.student.status = 'stale';
  data.settings.defaults.student.available = false;
  await page.getByRole('button', {name:'설정 다시 불러오기'}).click();
  await page.getByText('학생봇: 기본 모델 · 기본 모델 사용 불가', {exact:true}).waitFor();
  data.models[0].verification_state.student.status = 'succeeded';
  data.settings.defaults.student.available = true;
  await page.getByRole('button', {name:'설정 다시 불러오기'}).click();
  await page.getByText('학생봇: 기본 모델 · 사용 가능', {exact:true}).waitFor();
  await page.getByRole('button', {name:'작성 기본값·호출 설정 수정'}).click();
  assert.equal(await keyboardStudent.inputValue(), '1', 'reverification restores the same reference');
  assert(!(await keyboardStudent.locator('option[value="1"]').isDisabled()));
  return {pageErrors:[]};
}
