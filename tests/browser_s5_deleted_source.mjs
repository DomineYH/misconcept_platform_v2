import assert from 'node:assert/strict';
import {comparisonFixture, openComparison} from './s5_browser_fixtures.mjs';

export default async function checkDeletedSource(page) {
  page.setDefaultTimeout(3000);
  const fixture = await comparisonFixture(page, ['completed','completed','completed']);
  await openComparison(page);
  await page.getByRole('button', {name:'비교 실행',exact:true}).click();
  await page.getByRole('article',{name:'패널 1',exact:true}).getByText('완료',{exact:true}).waitFor();
  fixture.current().source_available = false;
  fixture.current().source_snapshot.scenario_id = 99;
  fixture.current().source_snapshot.title = '<img src=x onerror="window.s5xss=1">';
  await page.getByRole('button',{name:'비교 이력',exact:true}).click();
  await page.getByRole('button',{name:'과거 비교 상세',exact:true}).click();
  const detail = page.getByRole('region',{name:'비교 이력 상세',exact:true});
  const copy = detail.getByRole('link',{name:'이 조건으로 새 초안 열기',exact:true});
  await copy.waitFor();
  assert.match(await copy.getAttribute('href'), /^\/admin\/scenarios\/new\?comparison_id=comparison-1$/);
  assert.equal(await detail.getByRole('link',{name:'원본 시나리오 편집 폼 열기',exact:true}).count(),0);
  assert.equal(await detail.getByRole('button',{name:'이 모델 적용',exact:true}).count(),0);
  assert.equal(await detail.locator('img').count(),0);
  assert.equal(await page.evaluate(() => window.s5xss),undefined);
  await page.route('**/fixtures/s2/editor?new=1', async route => {
    const response = await route.fetch();
    const html = (await response.text()).replace(/(<script id="scenario-editor-data" type="application\/json">)([\s\S]*?)(<\/script>)/, (_, before, json, after) => {
      const data = JSON.parse(json);
      data.comparison_source_id = 'comparison-1';
      data.title = '고정 조건 사본';
      data.config.student.resolved_model_config = null;
      data.role_defaults.student = {available:true,model_config_id:data.model_choices[0].id};
      return before + JSON.stringify(data) + after;
    });
    await route.fulfill({response,body:html});
  });
  await page.goto(new URL('/fixtures/s2/editor?new=1',page.url()).href);
  assert.equal(await page.getByLabel('제목',{exact:true}).inputValue(),'고정 조건 사본');
  assert.equal(await page.getByLabel('학생봇 제공자 / 모델').inputValue(),'','copy preserves an unselected role instead of current defaults');
  return {pageErrors:[]};
}
