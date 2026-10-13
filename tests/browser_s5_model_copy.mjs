import assert from 'node:assert/strict';
import {comparisonFixture, openComparison} from './s5_browser_fixtures.mjs';

export default async function checkRoleModelCopy(page) {
  page.setDefaultTimeout(3000);
  for (const role of ['student','mentor','analysis']) {
    const fixture = await comparisonFixture(page, ['completed','completed','failed']);
    await openComparison(page);
    await page.getByLabel('제목', {exact:true}).fill('보존할 미저장 제목');
    await page.getByRole('button', {name:'완료 쌍 추가',exact:true}).click();
    await page.getByLabel('이력 1 교사').fill('이유?');
    await page.getByLabel('이력 1 학생').fill('분모가 커요.');
    await page.getByLabel('비교 대상').selectOption(role);
    await page.getByLabel('패널 2 제공자 / 모델', {exact:true}).selectOption('1');
    await page.getByLabel('패널 2 최대 출력 토큰').fill('777');
    const before = await page.evaluate(() => {
      const data = JSON.parse(document.getElementById('scenario-editor-data').textContent);
      return {config:data.config, groups:data.groups, status:data.status};
    });
    let validated = 0;
    await page.route('**/panels/2/model', route => {
      validated++;
      return route.fulfill({json:{target_role:role, model_config:fixture.current().panels[1].model_config}});
    });
    await page.getByRole('button', {name:'비교 실행',exact:true}).click();
    const panel = page.getByRole('article', {name:'패널 2',exact:true});
    await panel.getByRole('button', {name:'이 모델 적용',exact:true}).click();
    await page.getByText('대상 역할의 모델·옵션을 미저장 폼에 적용했습니다. 별도 저장 또는 게시가 필요합니다.', {exact:true}).waitFor();
    assert.equal(validated, 1, 'model copy checks live registration and frozen options');
    const select = page.locator(`[id="${role}.resolved_model_config"]`);
    assert(await page.getByLabel('시나리오 공통 모델').evaluate(el => document.activeElement === el));
    assert.equal(await page.locator(`[id="ai-${role}.resolved_model_config-max_output_tokens"]`).inputValue(), '777');
    let submitted;
    await page.route('**/admin/scenarios/1/update', route => {
      submitted = route.request().postDataJSON();
      return route.fulfill({status:409,json:{detail:{current_version:2}}});
    });
    assert.deepEqual(fixture.unexpected, [], 'copy never saves');
    await page.getByRole('button', {name:'초안으로 저장',exact:true}).click();
    await page.locator('#editor-conflict').waitFor();
    assert.equal(submitted.expected_version, 1);
    assert.equal(submitted.title, '보존할 미저장 제목');
    assert.deepEqual(submitted.config[role].resolved_model_config, fixture.current().panels[1].model_config);
    for (const other of ['student','mentor','analysis'].filter(value => value !== role)) {
      assert.equal(submitted.config[other].resolved_model_config.model_config_id, submitted.config[role].resolved_model_config.model_config_id, 'all active roles share the copied identity');
      for (const [key,value] of Object.entries(before.config[other])) if (key !== 'resolved_model_config') assert.deepEqual(submitted.config[other][key], value);
    }
    for (const key of ['problem','runtime']) assert.deepEqual(submitted.config[key], before.config[key]);
    assert.deepEqual(submitted.groups, before.groups);
    assert.equal(await page.locator(`[id="ai-${role}.resolved_model_config-max_output_tokens"]`).inputValue(), '777');
    page.once('dialog', dialog => dialog.dismiss());
    await page.getByRole('button', {name:'최신 내용 다시 불러오기',exact:true}).click();
    await page.route('**/panels/2/model', route => route.fulfill({status:422,json:{detail:{code:'configuration_unavailable'}}}));
    await page.locator(`[id="ai-${role}.resolved_model_config-max_output_tokens"]`).fill('778');
    await panel.getByRole('button', {name:'이 모델 적용',exact:true}).click();
    await page.getByText('현재 역할 검증·연결·옵션을 확인한 뒤 적용하세요.', {exact:true}).waitFor();
    assert.equal(await page.locator(`[id="ai-${role}.resolved_model_config-max_output_tokens"]`).inputValue(), '778');
    await page.unroute('**/panels/2/model');
    await page.unroute('**/admin/scenarios/1/update');
  }
  return {pageErrors:[]};
}
