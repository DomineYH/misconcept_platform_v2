import assert from 'node:assert/strict';
import {chromium} from 'playwright';

const base = `http://127.0.0.1:${process.argv[2]}`;
const browser = await chromium.launch({headless:true});
const page = await browser.newPage();
const errors = [], requests = [];
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => {
  if (request.method() === 'POST' && request.url() === `${base}/admin/comparisons`) requests.push(request.postDataJSON());
});
await page.route('**/*', route => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
try {
  await page.goto(`${base}/login`);
  await page.locator('[name=username]').fill('comparison_admin');
  await page.locator('[name=password]').fill('comparison-password');
  await Promise.all([page.waitForURL(url => url.pathname !== '/login'), page.locator('button[type=submit]').click()]);
  await page.goto(`${base}/admin/scenarios/${process.argv[3]}/edit`);
  await page.getByRole('button', {name:'모델 비교 열기',exact:true}).click();
  await page.getByLabel('비교 대상').selectOption('analysis');
  await page.getByRole('button', {name:'완료 쌍 추가',exact:true}).click();
  const teacher = 'Earlier analysis question <img src=x onerror="window.s5xss=1">';
  await page.getByLabel('이력 1 교사', {exact:true}).fill(teacher);
  await page.getByLabel('이력 1 학생', {exact:true}).fill('Earlier analysis answer');
  await page.getByLabel('마지막 교사 메시지 (분석, 선택)').fill('Last analysis question');
  const model = await page.getByLabel('패널 1 제공자 / 모델', {exact:true}).inputValue();
  for (let i = 2; i <= 3; i++) {
    await page.getByRole('button', {name:'패널 추가',exact:true}).click();
    await page.getByLabel(`패널 ${i} 제공자 / 모델`, {exact:true}).selectOption(model);
  }
  for (let i = 1; i <= 3; i++) await page.getByLabel(`패널 ${i} 최대 출력 토큰`, {exact:true}).fill(String(8191+i));
  const panels = [1,2,3].map(i => page.getByRole('article', {name:`패널 ${i}`,exact:true}));
  await page.getByRole('button', {name:'비교 실행',exact:true}).click();
  await panels[0].getByText('진행 중', {exact:true}).waitFor();
  assert(!(await panels[0].innerText()).includes('스트리밍'));
  await panels[0].getByRole('heading', {name:'검증된 비교 분석: 정상 분석',exact:true}).waitFor();
  await panels[1].getByRole('heading', {name:'검증된 비교 분석: 부분 분석',exact:true}).waitFor();
  await panels[2].getByText('실패', {exact:true}).waitFor();
  assert.match(await panels[1].innerText(), /검증 제외: 1개/);
  assert.match(await panels[0].innerText(), /첫 본문 N\/A/);
  assert(!(await panels[2].innerText()).includes('검증된 비교 분석'));
  const link = panels[0].getByRole('link', {name:/원문 1:/}).first();
  await link.click();
  assert(await panels[0].evaluate(el => el.contains(document.activeElement)));
  assert((await panels[0].innerText()).includes(teacher));
  assert.equal(await page.evaluate(() => window.s5xss), undefined);
  const body = requests[0];
  const headers = {'x-csrf-token':await page.evaluate(() => document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/)[1])};
  const replay = await page.request.post(`${base}/admin/comparisons`, {data:body,headers});
  assert.equal(replay.status(), 200);
  const state = await replay.json();
  assert.deepEqual(state.panels.map(p => p.status), ['ok','degraded','failed']);
  assert.deepEqual(state.panels[0].result.messages.map(m => m.id), [1,2,3]);
  assert.equal(state.panels[0].result.messages[0].content, teacher);
  assert.equal(body.target_role, 'analysis');
  await page.getByLabel('패널 2 최대 출력 토큰', {exact:true}).fill('1500');
  const refused = page.waitForResponse(r => r.url() === `${base}/admin/comparisons` && r.request().method() === 'POST');
  await page.getByRole('button', {name:'비교 실행',exact:true}).click();
  assert.equal((await refused).status(), 422);
  assert.equal(await page.getByLabel('이력 1 교사', {exact:true}).inputValue(), teacher);
  assert(await panels[0].getByRole('heading', {name:'검증된 비교 분석: 정상 분석',exact:true}).isVisible());
  assert(!(await page.locator('#scenario-comparison').innerText()).includes('PRIVATE_UNVERIFIED'));
  assert.deepEqual(errors, []);
  console.log('PASS: real analysis comparison HTTP/SSE/CSRF/SQLite/evidence/zero-call preflight');
} finally {
  await browser.close();
}
