import assert from 'node:assert/strict';

export default async function checkAICatalog(page) {
  page.setDefaultTimeout(8000);
  const base = new URL(page.url()).origin;
  const data = {master_key_available:true, models_available:true, probes_available:false, models:[],
    providers:['openai','anthropic','google'].map(provider => ({provider, connection_version:2,
      credential_revision:1, key_registered:provider === 'openai', enabled:provider === 'openai',
      status:provider === 'openai' ? 'ready' : 'unconfigured', masked_hint:provider === 'openai' ? '••••1234' : null,
      impact:[], catalog:{available:provider === 'openai', stale:true, fetched_at:null, models:[]}})),
    settings:{settings_version:1, defaults:{student:null,mentor:null,analysis:null},
      limits:{total:8,openai:4,anthropic:4,google:4,admin:3},
      timeouts:{connect:5,student_first_output:60,student_total:180,mentor_first_output:60,mentor_total:180,analysis_total:300,model_list_total:30}}};
  const writes = [];
  await page.context().addCookies([{name:'csrftoken',value:'catalog-csrf',url:base}]);
  await page.route('**/admin/ai/state', route => route.fulfill({json:data}));
  await page.route('**/admin/ai/providers/openai/catalog', route => {
    writes.push(route.request().postDataJSON());
    data.providers[0].catalog = {available:true,stale:false,fetched_at:'2026-10-09',models:[{model_id:'gpt-5.2'}]};
    return route.fulfill({json:{status:'saved'}});
  });
  await page.route('**/admin/ai/models', route => {
    const body = route.request().postDataJSON();
    writes.push(body);
    data.models.push({id:data.models.length+1,provider:body.provider,model_id:body.model_id,
      display_name:body.display_name,enabled:false,config_version:1,default_options:{},
      capabilities:body.model_id === 'gpt-5.2' ? {fields:[{name:'temperature',label:'다양성',type:'number',min:0,max:2}]} : null,
      verification_state:{student:{status:'unverified'},mentor:{status:'unverified'},analysis:{status:'unverified'}},probe_budgets:{}});
    return route.fulfill({json:{status:'saved'}});
  });
  await page.route('**/admin/ai/models/1/update', route => {
    writes.push(route.request().postDataJSON());
    return route.fulfill({status:409,json:{input:'PRIVATE-SENTINEL'}});
  });
  await page.route('**/admin/ai/settings/update', route => {
    const body = route.request().postDataJSON();
    writes.push(body);
    Object.assign(data.settings,{settings_version:2,limits:body.limits,timeouts:body.timeouts});
    return route.fulfill({json:{status:'saved'}});
  });
  await page.goto(`${base}/admin/ai`);
  await page.getByRole('button',{name:'작성 기본값·호출 설정 수정'}).waitFor();
  assert.equal(writes.length,0,'screen read never calls providers');
  await page.locator('[data-provider=openai]').getByRole('button',{name:'비생성 확인·목록 갱신'}).click();
  await page.locator('[data-provider=openai]').getByRole('button',{name:'gpt-5.2',exact:true}).click();
  await page.getByRole('button',{name:'모델 등록',exact:true}).click();
  await page.locator('[data-model="1"]').getByRole('button',{name:'학생봇 시험',exact:true}).waitFor();
  assert(await page.locator('[data-model="1"]').getByRole('button',{name:'학생봇 시험',exact:true}).isDisabled(),'A3 cannot generate probes');
  await page.getByRole('button',{name:'닫기',exact:true}).click();
  await page.locator('[data-provider=openai]').getByRole('button',{name:'모델 ID 직접 입력'}).click();
  await page.getByLabel('모델 ID',{exact:true}).fill('  custom-direct  ');
  await page.getByLabel('표시명',{exact:true}).fill('Unknown');
  await page.getByRole('button',{name:'모델 등록',exact:true}).click();
  await page.locator('[data-model="2"]').getByRole('heading',{name:/Unknown/}).waitFor();
  assert((await page.locator('[data-model="2"]').innerText()).includes('기능 정의 필요'));
  await page.getByRole('button',{name:'닫기',exact:true}).click();
  await page.locator('[data-model="1"]').getByRole('button',{name:'표시명·활성·옵션 수정'}).click();
  await page.getByLabel('다양성',{exact:true}).fill('0');
  await page.getByRole('button',{name:'모델 설정 저장',exact:true}).click();
  await page.getByRole('alert').filter({hasText:'충돌'}).waitFor();
  assert(!(await page.content()).includes('PRIVATE-SENTINEL'));
  assert.equal(writes.at(-1).default_options.temperature,0);
  await page.getByRole('button',{name:'닫기',exact:true}).click();
  Object.assign(data.models[0],{enabled:true,capabilities:{...data.models[0].capabilities,metadata_conflict:true},
    verification_state:{student:{status:'succeeded'},mentor:{status:'unverified'},analysis:{status:'unverified'}}});
  await page.reload();
  await page.getByRole('button',{name:'작성 기본값·호출 설정 수정'}).click();
  assert.equal(await page.locator('[name="default_student"] option[value="1"]').count(),0,'shutdown metadata excludes new defaults');
  await page.getByLabel('전체 호출 한도',{exact:true}).fill('10');
  await page.getByRole('button',{name:'설정 저장',exact:true}).click();
  await page.getByRole('status').filter({hasText:'완료'}).waitFor();
  assert.equal(writes.at(-1).defaults.student,null);
  assert.equal(writes.at(-1).expected_version,1);
  for (const width of [1280,390]) {
    await page.setViewportSize({width,height:900});
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  }
  assert.equal(writes.length,5,'only explicit catalog/register/edit/settings actions');
  return {pageErrors:[]};
}
