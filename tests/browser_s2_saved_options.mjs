import assert from 'node:assert/strict';

export default async function checkSavedScenarioOptions(page) {
  const base = new URL(page.url()).origin;
  await page.route('**/fixtures/s2/editor',async route => {
    const response = await route.fetch();
    let html = await response.text();
    const pattern = /(<script id="scenario-editor-data" type="application\/json">)([\s\S]*?)(<\/script>)/;
    const data = JSON.parse(html.match(pattern)[2]);
    Object.assign(data.config.student.resolved_model_config.options,{reasoning:{effort:'removed'},unknown:{keep:0}});
    html = html.replace(pattern,`$1${JSON.stringify(data)}$3`);
    await route.fulfill({response,body:html});
  });
  const writes = [];
  await page.route('**/admin/scenarios/1/update',route => {
    writes.push(route.request().postDataJSON());
    return route.fulfill({status:409,json:{detail:{current_version:2}}});
  });
  await page.goto(`${base}/fixtures/s2/editor`);
  await page.getByRole('button',{name:'3. 학생봇',exact:true}).click();
  const effort = page.getByLabel('학생봇 추론 수준',{exact:true});
  assert.equal(await effort.inputValue(),'removed');
  await page.getByLabel('학생봇 최대 출력 토큰').fill('901');
  await page.getByRole('button',{name:'초안으로 저장',exact:true}).click();
  await page.locator('#editor-conflict').waitFor();
  assert.deepEqual(writes[0].config.student.resolved_model_config.options,{max_output_tokens:901,reasoning:{effort:'removed'},unknown:{keep:0}});
  assert.equal(await effort.inputValue(),'removed','failed save preserves original enum');
  await page.getByRole('button',{name:'모델 비교 열기',exact:true}).click();
  const panelEffort = page.getByLabel('패널 1 추론 수준',{exact:true});
  assert.equal(await panelEffort.inputValue(),'removed','comparison shares saved option renderer');
  await page.getByLabel('패널 1 최대 출력 토큰').fill('902');
  await page.getByLabel('비교 대상').selectOption('mentor');
  await page.getByLabel('비교 대상').selectOption('student');
  assert.equal(await panelEffort.inputValue(),'removed','role switching preserves enum');
  assert.equal(await page.getByLabel('패널 1 최대 출력 토큰').inputValue(),'902');
  assert.equal(await page.getByLabel('학생봇 최대 출력 토큰').inputValue(),'901','comparison does not rewrite scenario');
  await page.getByRole('button',{name:'비교 닫기',exact:true}).click();
  assert.equal(await effort.inputValue(),'removed');
  return {pageErrors:[]};
}
