import assert from 'node:assert/strict';
import {state} from './browser_ai_connections.mjs';

export default async function check(page) {
  page.setDefaultTimeout(8000);
  const base = new URL(page.url()).origin;
  let data = state();
  let writes = 0, pending, failRead = false;
  await page.route('**/admin/ai/state', async route => {
    if (pending) await pending;
    await route.fulfill({status:failRead ? 503 : 200, json:failRead ? {detail:'PRIVATE-SENTINEL'} : data});
  });
  await page.route('**/admin/ai/models/1/update', async route => {
    writes++;
    await route.fulfill({status:409, json:{detail:'PRIVATE-SENTINEL'}});
  });
  await page.route('**/admin/ai/providers/openai/catalog', route => route.fulfill({status:503, json:{detail:'PRIVATE-SENTINEL'}}));
  await page.goto(`${base}/admin/ai`);
  const unknown = page.locator('[data-model="2"]');
  await unknown.waitFor();
  const trial = unknown.getByRole('button', {name:'학생봇 시험', exact:true});
  assert(await trial.isDisabled());
  assert.match(await trial.getAttribute('aria-describedby') || '', /\S+/, 'disabled trial has a nearby explanation');
  const description = await trial.evaluate(el => document.getElementById(el.getAttribute('aria-describedby'))?.textContent);
  assert.match(description, /기능 정의/, 'unknown capability explains why trial is unavailable');
  assert.equal(writes, 0, 'opening settings never starts a paid trial');
  assert(await page.locator('[data-provider="anthropic"]').getByText('모델 목록: 조회 기록 없음', {exact:true}).isVisible(), 'no catalog check is distinct from a recent successful empty list');
  const edit = page.locator('[data-model="1"]').getByRole('button', {name:'표시명·활성·옵션 수정'});
  await edit.click();
  await page.getByLabel('표시명', {exact:true}).fill('내 수정값');
  await page.getByRole('button', {name:'모델 설정 저장', exact:true}).click();
  await page.getByRole('alert').filter({hasText:'충돌'}).waitFor();
  assert(await page.locator('#ai-editor #ai-error').isVisible(), 'conflict stays beside the active form');
  assert.equal(await page.getByLabel('표시명', {exact:true}).inputValue(), '내 수정값', 'conflict preserves input');
  assert(!(await page.content()).includes('PRIVATE-SENTINEL'));
  await page.keyboard.press('Escape');
  assert(await edit.evaluate(el => el === document.activeElement), 'Escape restores caller focus');
  await page.locator('[data-provider="openai"]').getByRole('button', {name:'비생성 확인·목록 갱신'}).click();
  await page.getByRole('alert').waitFor();
  assert(await page.locator('[data-provider="openai"] #ai-error').isVisible(), 'catalog failure stays with its provider');
  assert.equal(await page.getByLabel('OpenAI 모델 ID 선택', {exact:true}).locator('option[value="gpt-5-mini"]').count(), 1, 'failed catalog refresh preserves the previous list');
  for (const [change, reason] of [
    [d => { d.models[0].capabilities.metadata_conflict = true; }, /목록 불일치/],
    [d => { d.master_key_available = false; }, /마스터 키/],
    [d => { d.providers[0].status = 'decryption_failed'; }, /복호화 실패/],
    [d => { d.providers[0].key_registered = false; d.providers[0].enabled = false; d.providers[0].status = 'unconfigured'; }, /키를 등록/],
    [d => { d.providers[0].enabled = false; }, /재활성화/],
    [d => { d.models[0].probe_budgets = {}; }, /출력 상한/],
    [d => { d.models[0].verification_state.student = {status:'verifying', probe_request_id:'existing'}; }, /진행 중/],
    [d => { d.probe_roles = ['mentor']; }, /아직 제공/]
  ]) {
    data = state();
    change(data);
    await page.getByRole('button', {name:'설정 다시 불러오기'}).click();
    await page.getByRole('status').filter({hasText:'불러왔습니다'}).waitFor();
    const blocked = page.locator('[data-model="1"]').getByRole('button', {name:'학생봇 시험', exact:true});
    assert(await blocked.isDisabled());
    assert.match(await blocked.evaluate(el => document.getElementById(el.getAttribute('aria-describedby'))?.textContent), reason);
  }
  let release;
  pending = new Promise(resolve => { release = resolve; });
  await page.getByRole('button', {name:'설정 다시 불러오기'}).click();
  assert.match(await page.locator('#ai-status').innerText(), /불러오는 중/, 'refresh announces loading while preserving the previous state');
  failRead = true;
  release();
  await page.getByRole('status').filter({hasText:'불러올 수 없습니다'}).waitFor();
  assert(await page.locator('[data-model="1"]').isVisible(), 'read failure retains valid previous settings');
  assert(!(await page.content()).includes('PRIVATE-SENTINEL'));
  return {pageErrors:[]};
}
