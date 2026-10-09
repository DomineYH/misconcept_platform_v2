import assert from 'node:assert/strict';

export const state = () => ({
  master_key_available: true,
  probes_available: true,
  probe_roles: ['student', 'mentor', 'analysis'],
  providers: [
    {provider:'openai', connection_version:3, credential_revision:2, key_registered:true,
      masked_hint:'••••1234', enabled:true, status:'ready', verified_at:'2026-10-01',
      error_code:null, impact:['학생봇 기본 모델', '진행 중 호출 1건'],
      catalog:{stale:true, fetched_at:'2026-10-01', models:[{model_id:'gpt-5-mini'}]}},
    {provider:'anthropic', connection_version:1, key_registered:false, enabled:false,
      status:'unconfigured', impact:[], catalog:{stale:false, fetched_at:null, models:[]}},
    {provider:'google', connection_version:2, key_registered:true, masked_hint:'••••5678',
      enabled:true, status:'decryption_failed', impact:[],
      catalog:{stale:true, fetched_at:null, models:[]}}
  ],
  models: [
    {id:1, provider:'openai', model_id:'gpt-5-mini', display_name:'기본 모델', enabled:true,
      config_version:2, capabilities:{fields:[{name:'max_output_tokens', label:'최대 출력 토큰', type:'integer', min:1, max:4096}]},
      default_options:{max_output_tokens:2048},
      verification_state:{student:{status:'succeeded'}, mentor:{status:'failed', error_code:'invalid_output'}, analysis:{status:'stale'}},
      probe_budgets:{student:1024, mentor:1500, analysis:2048}},
    {id:2, provider:'openai', model_id:'custom-model', display_name:'직접 입력 모델', enabled:false,
      config_version:1, capabilities:null, default_options:{},
      verification_state:{student:{status:'unverified'}, mentor:{status:'verifying', probe_request_id:'existing-probe'}, analysis:{status:'unverified'}},
      probe_budgets:{}}
  ],
  settings:{settings_version:1, defaults:{student:{model_config_id:2, available:false}, mentor:null, analysis:null},
    limits:{total:8, openai:4, anthropic:4, google:4, admin:3},
    timeouts:{connect:5, student_first_output:60, student_total:180, mentor_first_output:60, mentor_total:180, analysis_total:300, model_list_total:30}}
});

export default async function checkAIConnections(page) {
  const base = new URL(page.url()).origin;
  await page.route('**/admin/ai/state', route => route.fulfill({json:state()}));
  await page.goto(`${base}/admin/ai`);
  await page.getByRole('heading', {name:'AI 연결·모델', exact:true}).waitFor();
  for (const text of ['미설정', '복호화 실패', '오래된 목록', '기능 정의 필요', '미검증', '검증 중', '성공', '실패', '재검증 필요', '기본 모델 사용 불가']) {
    assert((await page.locator('#ai-content').innerText()).includes(text), `distinct state: ${text}`);
  }
  assert(await page.getByText('••••1234', {exact:false}).count() === 1, 'only masked hint is shown');
  return {pageErrors:[]};
}
