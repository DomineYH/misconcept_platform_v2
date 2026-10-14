import assert from 'node:assert/strict';

export default async function check(page) {
  page.setDefaultTimeout(5000);
  const base = new URL(page.url()).origin;
  let mode = 'empty';
  await page.route('**/admin/comparison-conversations**', route => {
    if (route.request().method() === 'DELETE') return route.abort('failed');
    if (mode === 'failed') return route.fulfill({status:500,json:{detail:'Unavailable'}});
    return route.fulfill({json:{items:mode === 'empty' ? [] : [{conversation_id:'history-1',title:'보존된 비교 대화',created_at:'2026-10-14',status:'open'}],next_cursor:mode === 'empty' ? null : 'next'}});
  });
  await page.goto(`${base}/fixtures/uiux/comparison`);
  await page.getByText('보존 중인 비교 대화가 없습니다.',{exact:true}).waitFor();
  mode = 'populated';
  await page.reload();
  await page.getByRole('link',{name:'보존된 비교 대화 · 2026-10-14 · open',exact:true}).waitFor();
  mode = 'failed';
  await page.getByRole('button',{name:'다음 20개',exact:true}).first().click();
  await page.getByText('비교 이력을 불러오지 못했습니다. 다시 확인하세요.',{exact:true}).waitFor();
  assert(await page.getByRole('link',{name:'보존된 비교 대화 · 2026-10-14 · open',exact:true}).isVisible(), 'failed history page does not discard visible records');
  page.on('dialog',dialog => dialog.accept());
  await page.locator('#conversation-history').getByRole('button',{name:'비교 기록 삭제',exact:true}).click();
  await page.getByText('비교 기록 삭제 결과를 확인할 수 없습니다. 현재 목록은 유지됩니다.',{exact:true}).waitFor();
  assert(await page.getByRole('link',{name:'보존된 비교 대화 · 2026-10-14 · open',exact:true}).isVisible(), 'uncertain deletion preserves visible record');
  return {pageErrors:[]};
}
