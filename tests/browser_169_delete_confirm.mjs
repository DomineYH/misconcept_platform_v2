import assert from 'node:assert/strict';

export default async function check(page) {
  const base = new URL(page.url()).origin;
  const wording = {
    ko: '이 대화를 삭제할까요? 목록과 다운로드에서 사라지며 되돌릴 수 없습니다. 대화 기록은 시스템에 보관됩니다.',
    en: 'Delete this conversation? It will be removed from lists and downloads and cannot be undone. The conversation record is retained in the system.'
  };
  const notice = {
    ko: '삭제된 대화 · 내용은 표시하지 않습니다',
    en: 'Deleted conversation · content is not shown'
  };
  let deletes = 0;
  await page.route('**/admin/sessions/1/delete', async route => {
    assert.equal(route.request().method(), 'POST');
    assert.equal(route.request().headers()['x-csrf-token'], 'delete-169');
    deletes++;
    await route.fulfill({status: 200, body: ''});
  });
  for (const language of ['ko', 'en']) {
    await page.context().addCookies([
      {name: 'ui_lang', value: language, url: base},
      {name: 'csrftoken', value: 'delete-169', url: base}
    ]);
    for (const source of ['page', 'row']) {
      await page.goto(`${base}/fixtures/uiux/admin/sessions`);
      if (source === 'row') {
        const row = await page.request.get(`${base}/fixtures/uiux/admin/row`);
        assert.equal(row.status(), 200);
        await page.evaluate(html => {
          const body = document.getElementById('sessions-tbody');
          body.innerHTML = html;
          htmx.process(body);
        }, await row.text());
      }
      const rows = page.locator('#sessions-tbody tr');
      const button = rows.locator('[hx-swap="delete"]');
      assert.equal(await button.getAttribute('hx-confirm'), wording[language]);
      const before = deletes;
      const cancel = page.waitForEvent('dialog').then(async dialog => {
        assert.equal(dialog.message(), wording[language]);
        await dialog.dismiss();
      });
      await button.click();
      await cancel;
      assert.equal(await rows.count(), 1, `${language} ${source}: cancel retains row`);
      assert.equal(deletes, before, 'cancel sends no delete request');
      const confirm = page.waitForEvent('dialog').then(async dialog => {
        assert.equal(dialog.message(), wording[language]);
        await dialog.accept();
      });
      await button.click();
      await confirm;
      await rows.waitFor({state: 'detached'});
      assert.equal(deletes, before + 1, `${language} ${source}: confirm deletes once`);
    }
    await page.goto(`${base}/fixtures/uiux/admin/sessions`);
    for (const origin of ['native', 'legacy_unconverted']) {
      for (const analysis of ['0', '1']) {
        const response = await page.request.get(`${base}/fixtures/169/deleted-detail?origin=${origin}&analysis=${analysis}`);
        assert.equal(response.status(), 200);
        await page.evaluate(html => {
          document.getElementById('analysis-modal-body').innerHTML = html;
          showModal('analysis-modal');
        }, await response.text());
        const modal = page.locator('#analysis-modal');
        assert(await modal.isVisible());
        assert((await modal.innerText()).includes(notice[language]));
        assert.equal(await modal.locator('.badge-danger').count(), 1);
        assert(!(await modal.innerText()).includes('DELETED MESSAGE BODY 169'));
        assert.equal(await modal.locator('[data-analysis-result], [data-result-url]').count(), 0);
        assert.equal(await modal.locator('[href*="/analysis"]').count(), 0);
        if (origin === 'native' && analysis === '0') {
          await page.screenshot({path: `/tmp/169-deleted-detail-${language}.png`});
        }
        await page.evaluate(() => hideModal('analysis-modal'));
      }
    }
  }
  return {pageErrors: []};
}
