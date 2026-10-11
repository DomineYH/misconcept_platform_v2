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
  const p1 = page.getByRole('article',{name:'패널 1',exact:true});
  const p2 = page.getByRole('article',{name:'패널 2',exact:true});
  const p3 = page.getByRole('article',{name:'패널 3',exact:true});
  async function configure(count, stalled) {
    await page.getByRole('button',{name:'비교 입력 초기화',exact:true}).click();
    const model = await page.getByLabel('패널 1 제공자 / 모델',{exact:true}).inputValue();
    for (let i = 2; i <= count; i++) {
      await page.getByRole('button',{name:'패널 추가',exact:true}).click();
      await page.getByLabel(`패널 ${i} 제공자 / 모델`,{exact:true}).selectOption(model);
    }
    for (let i = 1; i <= count; i++) await page.getByLabel(`패널 ${i} 최대 출력 토큰`,{exact:true}).fill(String(stalled ? 1799+i : 1500));
    await page.getByLabel('학생 현재 질문').fill('Concurrent comparison');
    await page.getByRole('button',{name:'비교 실행',exact:true}).click();
  }
  await configure(3,true);
  await p1.getByText('Fast final',{exact:true}).waitFor();
  await p2.getByText('Panel 2 partial',{exact:true}).waitFor();
  await p3.getByText('Panel 3 partial',{exact:true}).waitFor();
  assert(await p1.getByText('완료',{exact:true}).isVisible());
  assert(await p2.getByText('스트리밍',{exact:true}).isVisible());
  assert(await page.getByRole('button',{name:'비교 실행',exact:true}).isDisabled());
  await p2.getByRole('button',{name:'패널 중지',exact:true}).click();
  await p2.getByText('취소',{exact:true}).waitFor();
  assert(await p2.getByText('Panel 2 partial',{exact:true}).isVisible());
  assert(await p3.getByText('Panel 3 partial',{exact:true}).isVisible());
  assert(await p3.getByText('스트리밍',{exact:true}).isVisible());
  await page.getByRole('button',{name:'전체 중지',exact:true}).click();
  await p3.getByText('취소',{exact:true}).waitFor();
  assert(await p3.getByText('Panel 3 partial',{exact:true}).isVisible());
  assert(await p1.getByText('Fast final',{exact:true}).isVisible());
  assert(await page.getByRole('button',{name:'비교 실행',exact:true}).isEnabled());
  await p2.getByRole('button',{name:'패널 재시도',exact:true}).click();
  await p2.getByText('Live final <img src=x onerror="window.s5xss=1">',{exact:true}).waitFor();
  assert(await p1.getByText('Fast final',{exact:true}).isVisible());
  assert.match(await p2.innerText(), /이전 시도 1회/);
  const retryState = await (await page.request.get(`${base}/admin/comparisons/${new URL(page.url()).searchParams.get('comparison_id')}`)).json();
  assert.equal(retryState.panels[1].status,'completed');
  assert.equal(retryState.panels[1].attempts[0].status,'cancelled');
  assert.equal(retryState.panels[1].attempts[0].text,'Panel 2 partial');
  assert.equal(retryState.panels[1].model_config.options.max_output_tokens,1801);
  await configure(2,false);
  await p1.getByText('완료',{exact:true}).waitFor();
  await p2.getByText('완료',{exact:true}).waitFor();
  await configure(3,true);
  await p1.getByText('Fast final',{exact:true}).waitFor();
  await p2.getByText('Panel 2 partial',{exact:true}).waitFor();
  await p3.getByText('Panel 3 partial',{exact:true}).waitFor();
  await page.reload();
  await p2.getByText('중단',{exact:true}).waitFor();
  assert(await p1.getByText('Fast final',{exact:true}).isVisible());
  assert(await p2.getByRole('button',{name:'패널 재시도',exact:true}).isEnabled());
  // The saved-state replay remains read-only after the product's GET recovery.
  const replay = await page.request.post(`${base}/admin/comparisons`,{data:requests.at(-1),headers:{'x-csrf-token':await page.evaluate(() => document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/)[1])}});
  const recovered = await replay.json();
  assert.equal(recovered.status,'finished');
  assert.deepEqual(recovered.panels.map(panel => panel.status),['completed','interrupted','interrupted']);
  assert.deepEqual(errors,[]);
} finally { await browser.close(); }
