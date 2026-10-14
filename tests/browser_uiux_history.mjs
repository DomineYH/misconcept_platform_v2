import assert from 'node:assert/strict';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  for (const state of ['', '?reconstructed=1']) {
    await page.goto(`${base}/fixtures/uiux122/history${state}`);
    const native = !state;
    await page.getByRole('heading', {name: native ? '채택된 보고서: 정상 분석' : '채택된 보고서: 과거 분석', exact:true}).waitFor();
    assert(await page.getByText(native ? '수업 시작 시 확정한 설정 · 원래 대화 기록' : '과거 자료 · 재구성된 수업 설정 · 읽기 전용', {exact:true}).isVisible());
    if (!native) {
      assert(await page.getByText(/실제 시작 시점의 설정을 증명하지 않습니다/).isVisible());
      assert(await page.getByText(/원문 근거·분석 범위: 알 수 없음/).isVisible());
    }
    assert.equal(await page.getByRole('link', {name:'CSV 다운로드',exact:true}).getAttribute('href'), '/sessions/1/export.csv');
    assert.equal(await page.getByRole('link', {name:'기존 분석 보기',exact:true}).getAttribute('href'), '/sessions/1/analysis_page');
    assert.equal(await page.locator('#teacher-input').count(),0,'history does not resume conversation');
    assert.equal(await page.getByRole('button', {name:/분석 재생성|멘토 도움|전송/}).count(),0);
  }
  return {pageErrors:[]};
}
