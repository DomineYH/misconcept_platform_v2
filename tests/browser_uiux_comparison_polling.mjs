import assert from 'node:assert/strict';

export default async function check(page) {
  page.setDefaultTimeout(15000);
  const base = new URL(page.url()).origin;
  let snapshot, posts = 0, reads = 0;
  await page.route('**/admin/comparison-conversations', route => route.fulfill({json:{items:[],next_cursor:null}}));
  await page.route('**/admin/comparison-conversations/visual-only**', route => {
    if (route.request().method() === 'POST') { posts++; return route.abort('failed'); }
    reads++;
    return route.fulfill({json:snapshot});
  });
  await page.goto(`${base}/fixtures/uiux/comparison?panels=2`);
  snapshot = await page.locator('#comparison-conversation-data').evaluate(el => JSON.parse(el.textContent).snapshot);
  snapshot.children[0].panels[1].run_id = 'failed-generation';
  await page.getByRole('button',{name:'상태 새로고침',exact:true}).click();
  snapshot.children[0].panels[1].status = 'running';
  snapshot.children[0].panels[1].run_id = 'current-generation';
  snapshot.phase = 'generating';
  reads = 0;
  await page.getByRole('article',{name:'패널 2',exact:true}).getByRole('button',{name:'패널 재시도',exact:true}).click();
  await page.getByText('복구 조회가 끝났습니다. 요청 결과는 확인되지 않았습니다. 상태 새로고침 또는 허용된 중지를 사용하세요.',{exact:true}).waitFor();
  assert(reads >= 9 && reads <= 11, `bounded recovery GETs: ${reads}`);
  const finalReads = reads;
  await page.waitForTimeout(1200);
  assert.equal(reads,finalReads,'recovery stops after ten seconds');
  assert.equal(posts,1,'uncertain retry never creates another POST');
  const b = page.getByRole('article',{name:'패널 2',exact:true});
  assert(await b.getByRole('button',{name:'패널 중지',exact:true}).isEnabled());
  assert(await page.getByRole('article',{name:'패널 1',exact:true}).getByRole('button',{name:'학생 응답 채택',exact:true}).isDisabled());
  assert(await b.getByText('스트리밍',{exact:true}).isVisible(),'unknown transport outcome does not turn active panel into failed');
  return {pageErrors:[]};
}
