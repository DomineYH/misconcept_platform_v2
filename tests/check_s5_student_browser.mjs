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
  await Promise.all([page.waitForURL(url => url.pathname !== '/login'),page.locator('button[type=submit]').click()]);
  for (const [path, saved] of [['/admin/scenarios/new',false],[`/admin/scenarios/${process.argv[3]}/edit`,true]]) {
    await page.goto(base+path);
    if (!saved) {
      await page.locator('[data-step="1"]').click();
      await page.getByLabel('공개 문제 상황',{exact:true}).fill('Public comparison problem');
      await page.getByLabel('학습목표',{exact:true}).fill('Compare fractions');
      await page.locator('[data-step="2"]').click();
      await page.getByLabel('학생봇 이름',{exact:true}).fill('Student');
      await page.getByLabel('목표 오개념',{exact:true}).fill('Larger denominator');
      await page.getByLabel('학생봇 행동 지시',{exact:true}).fill('Explain');
    }
    await page.getByRole('button',{name:'모델 비교 열기',exact:true}).click();
    await page.getByText('단독 테스트',{exact:true}).waitFor();
    await page.getByLabel('학생 현재 질문').fill('Same full context?');
    await page.getByRole('button',{name:'완료 쌍 추가',exact:true}).click();
    await page.getByLabel('이력 1 교사',{exact:true}).fill('Earlier question');
    await page.getByLabel('이력 1 학생',{exact:true}).fill('Earlier answer');
    const responsePromise = page.waitForResponse(response => response.url() === `${base}/admin/comparisons` && response.request().method() === 'POST');
    await page.getByRole('button',{name:'비교 실행',exact:true}).click();
    const response = await responsePromise;
    assert.equal(response.status(),200, response.status() === 200 ? '' : await response.text());
    await page.getByText('Live partial',{exact:true}).waitFor();
    assert(await page.getByRole('button',{name:'비교 실행',exact:true}).isDisabled());
    await page.getByText('Live final <img src=x onerror="window.s5xss=1">',{exact:true}).waitFor();
    assert.equal(await page.evaluate(() => window.s5xss),undefined);
    assert(await page.getByRole('button',{name:'비교 실행',exact:true}).isEnabled());
    const body = requests.at(-1);
    assert.equal(body.source_snapshot.scenario_id,saved ? Number(process.argv[3]) : null);
    await page.getByRole('button',{name:'상태 새로고침',exact:true}).click();
    const stateResponse = await page.request.post(`${base}/admin/comparisons`,{data:body,headers:{'x-csrf-token':await page.evaluate(() => document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/)[1])}});
    assert.equal(stateResponse.status(),200);
    const state = await stateResponse.json();
    assert.equal(state.status,'finished');
    assert.deepEqual(state.canonical_context.map(message => message.id),[1,2]);
    assert.equal(state.panels[0].text,'Live final <img src=x onerror="window.s5xss=1">');
    assert((await page.locator('#scenario-comparison').innerText()).includes(state.context_hash));
    assert.equal(await page.getByLabel('학생 현재 질문').inputValue(),'Same full context?');
  }
  assert.deepEqual(errors,[]);
} finally { await browser.close(); }
