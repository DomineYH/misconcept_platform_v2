import assert from 'node:assert/strict';

export default async function checkHistory(page, base) {
  const login = async username => {
    await page.goto(base + '/login');
    await page.locator('[name=username]').fill(username);
    await page.locator('[name=password]').fill('s2-browser-password');
    await Promise.all([page.waitForURL(url => url.pathname !== '/login'), page.locator('button[type=submit]').click()]);
  };
  await login('draft_admin');
  const sessions = (await (await page.request.get(base + '/admin/sessions')).json()).sessions;
  const legacy = sessions.find(session => session.snapshot_provenance.snapshot_origin === 'legacy_reconstructed');
  const native = sessions.find(session => session.snapshot_provenance.snapshot_origin === 'native');
  assert(legacy && native);
  assert.equal(legacy.snapshot_provenance.source_scenario_version, null);
  const adminCsv = await page.request.get(base + '/admin/sessions/export');
  assert.equal(adminCsv.status(), 200);
  assert((await adminCsv.text()).includes('legacy_reconstructed'));
  await page.goto(base + '/admin/sessions-page');
  assert(await page.getByText(/Live real conversion.*읽기 전용/).isVisible());
  assert(await page.getByText('Frozen lesson title', {exact: true}).isVisible());
  await login('draft_teacher');
  for (const width of [1280, 390]) {
    await page.setViewportSize({width, height: 900});
    await page.goto(`${base}/sessions/${legacy.id}`);
    await page.locator('[data-analysis-result]').getByRole('heading', {name: '채택된 보고서: 과거 분석'}).waitFor();
    const provenance = await page.locator('#snapshot-provenance').innerText();
    assert(provenance.includes('읽기 전용') && provenance.includes('실제 시작 시점'));
    assert(provenance.includes('학생봇 소개·지시·모델·옵션'));
    assert(await page.getByText('Original history answer', {exact: true}).isVisible());
    assert.equal(await page.locator('textarea, #request-mentor, #end-session-btn').count(), 0);
    assert.equal(await page.evaluate(() => window.historyLeaked), undefined);
    assert(!(await page.content()).includes('INTERNAL_CONVERSION_SENTINEL'));
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.getByRole('link', {name: '기존 분석 보기'}).click();
    await page.locator('#snapshot-provenance').waitFor();
    assert(await page.locator('#snapshot-provenance').isVisible());
    assert(await page.getByText('Original history feedback', {exact: true}).isVisible());
    const result = page.locator('[data-analysis-result]');
    await result.locator('details > summary').click();
    assert(await result.getByText('분류: Original label · 평가: 우수', {exact: true}).isVisible());
    assert(await result.getByText('판정 이유: Original history evidence', {exact: true}).isVisible());
    assert(await result.getByText(/원문 근거·분석 범위: 알 수 없음/).isVisible());
    assert((await page.content()).includes('Original history evidence'));
    assert(!(await page.content()).includes('PRIVATE RUBRIC'));
    await page.evaluate(() => scrollTo(0, 0));
  }
  const csv = await page.request.get(`${base}/sessions/${legacy.id}/export.csv`);
  assert.equal(csv.status(), 200);
  assert((await csv.text()).includes('Original label'));
  assert((await csv.text()).includes('reconstruction_only'));
  assert(!(await csv.text()).includes('INTERNAL_CONVERSION'));
  const csrf = (await page.context().cookies(base)).find(cookie => cookie.name === 'csrftoken').value;
  for (const action of ['analyze', 'turns/stream']) {
    const refused = await page.request.post(`${base}/sessions/${legacy.id}/${action}`, {
      data: {request_id: '11111111-1111-4111-8111-111111111111', content: 'Why?'}, headers: {'x-csrf-token': csrf}
    });
    assert.equal(refused.status(), 409);
    assert.equal((await refused.json()).detail.code, 'legacy_read_only');
  }
  await page.goto(`${base}/sessions/${native.id}`);
  assert(await page.getByRole('heading', {name: 'Frozen lesson title · 대화 기록'}).isVisible());
  assert.equal(await page.locator('#snapshot-provenance').count(), 0);
  console.log('PASS: mixed native/reconstructed history preserves dialogue and results on desktop/mobile');
}
