import assert from 'node:assert/strict';

// Production templates at the isolated HTTP seam; no runtime pagination backend.
export default async function checkAdminPagination(page) {
  const base = new URL(page.url()).origin;
  for (const kind of ['users', 'scenarios']) {
    let total = 41;
    const listUrl = `${base}/admin/${kind}`;
    await page.route(new RegExp(`/admin/${kind}(\\?.*)?$`), async route => {
      const query = new URL(route.request().url()).searchParams;
      const response = await route.fetch({
        url:`${base}/fixtures/s6/${kind}?page=${query.get('page') || 1}&total=${total}`
      });
      await route.fulfill({response});
    });
    const rows = page.locator(kind === 'users' ? '#users-table tbody tr[data-user-id]' : '.scenario-card');
    const nav = page.getByRole('navigation', {name:'목록 페이지'});
    async function keyboardNavigate(name, pageNumber) {
      const link = nav.getByRole('link', {name, exact:true});
      await link.focus();
      assert(await link.evaluate(el => el === document.activeElement), `${kind}: navigation focus`);
      await Promise.all([
        page.waitForURL(`${listUrl}?page=${pageNumber}`), page.keyboard.press('Enter')
      ]);
      await page.waitForLoadState('load');
      assert((await nav.innerText()).includes(`현재 ${pageNumber} 페이지`), `${kind}: new page state`);
    }
    for (const width of [1280, 390]) {
      await page.setViewportSize({width, height:900});
      await page.goto(listUrl);
      assert(await rows.count() === 20, `${kind}: first page has 20 rows`);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${kind}: ${width}px populated list fits`);
      assert(await nav.evaluate(el => el.getBoundingClientRect().right <= innerWidth), `${kind}: populated navigation fits`);
      assert((await rows.first().innerText()).includes(kind === 'users' ? 'teacher_41' : '시나리오 41'), `${kind}: ID descending`);
      assert((await nav.innerText()).includes('현재 1 페이지'), `${kind}: current page`);
      assert((await nav.innerText()).includes('20개씩'), `${kind}: fixed page size`);
      assert(await nav.locator('[aria-current="page"]').count() === 1, `${kind}: accessible page state`);
      assert(await nav.getByRole('button', {name:'이전', exact:true}).isDisabled(), `${kind}: first page`);
      await keyboardNavigate('다음', 2);
      assert(await rows.count() === 20, `${kind}: middle page`);
      assert((await rows.first().innerText()).includes(kind === 'users' ? 'teacher_21' : '시나리오 21'), `${kind}: distinct middle page`);
      await keyboardNavigate('다음', 3);
      assert(await rows.count() === 1, `${kind}: last page`);
      assert(await nav.getByRole('button', {name:'다음', exact:true}).isDisabled(), `${kind}: no next page`);
      await keyboardNavigate('이전', 2);
      await page.goto(`${listUrl}?page=9`);
      assert(await rows.count() === 0, `${kind}: out of range is empty`);
      assert(await page.getByText('이 페이지에 결과가 없습니다.', {exact:true}).isVisible(), `${kind}: empty page explanation`);
      await keyboardNavigate('이전', 3);
      total = 0;
      await page.goto(listUrl);
      assert(await rows.count() === 0, `${kind}: empty list`);
      assert(await nav.getByRole('button', {name:'이전', exact:true}).isDisabled());
      assert(await nav.getByRole('button', {name:'다음', exact:true}).isDisabled());
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${kind}: ${width}px no page overflow`);
      assert(await nav.evaluate(el => el.getBoundingClientRect().right <= innerWidth), `${kind}: mobile navigation visible`);
      total = 41;
    }
  }
  return {pageErrors:[]};
}
