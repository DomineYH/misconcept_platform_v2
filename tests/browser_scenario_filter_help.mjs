import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {colorMath} from './uiux_color_math.mjs';
import {settle} from './browser_uiux_scenario_layout.mjs';

function measureFilter(colorMath) {
  const box = el => {
    const r = el.getBoundingClientRect();
    return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};
  };
  const help = document.querySelector('#scenario-filter-help');
  const style = getComputedStyle(help);
  const {rgba,over,background,contrast} = colorMath();
  const controls = [...document.querySelectorAll('#search-input, .filter-group select')].map(box);
  const filter = box(document.querySelector('.filter-bar'));
  const title = box(document.querySelector('h2.section-title'));
  const rect = box(help);
  const status = document.querySelector('#scenario-filter-empty');
  return {
    filter,help:rect,title,controls,
    helpGap:rect.top-Math.max(...controls.map(box => box.bottom)),
    titleGap:title.top-Math.max(filter.bottom,rect.bottom),
    fontSize:style.fontSize,lineHeight:style.lineHeight,color:style.color,
    secondaryColor:style.getPropertyValue('--color-text-secondary').trim(),
    contrast:contrast(over(rgba(style.color),background(help)),background(help)),
    searchBox:box(document.querySelector('.search-box')),
    describedBy:document.querySelector('#search-input').getAttribute('aria-describedby'),
    statusRole:status.getAttribute('role'),status:status.hidden ? null : box(status),
    cards:[...document.querySelectorAll('.scenario-card')].filter(el => el.getClientRects().length).map(box),
    overflow:document.documentElement.scrollWidth > innerWidth,
  };
}

function assertLayout(layout, label, width) {
  assert(layout.helpGap >= 6 && layout.helpGap <= 8, `${label}: help follows controls by 6–8px: ${layout.helpGap}`);
  assert(layout.titleGap >= 24 && layout.titleGap <= 32, `${label}: title follows filter/help by 24–32px: ${layout.titleGap}`);
  assert.equal(layout.fontSize,'14px',`${label}: secondary font size`);
  assert.equal(layout.lineHeight,'21px',`${label}: secondary line height`);
  assert(layout.contrast >= 4.5,`${label}: help contrast ${layout.contrast}`);
  assert.equal(layout.describedBy,'scenario-filter-help');
  assert.equal(layout.statusRole,'status');
  assert(!layout.overflow,`${label}: no document overflow`);
  assert.equal(layout.searchBox.height,layout.controls[0].height,`${label}: no empty search space`);
  for (const control of layout.controls) {
    assert(control.width >= 44 && control.height >= 44,`${label}: 44px targets`);
    assert(control.left >= layout.filter.left && control.right <= layout.filter.right,`${label}: controls fit filter`);
  }
  if (width >= 769) assert(layout.controls.every(control => control.top === layout.controls[0].top),`${label}: wide controls share a row`);
  for (const card of layout.cards) assert(card.top >= layout.title.bottom,`${label}: cards follow title`);
  if (layout.status) assert(layout.status.top >= layout.title.bottom,`${label}: no-results status follows title`);
}

export default async function checkScenarioFilterHelp(page) {
  const base = new URL(page.url()).origin;
  const evidence = resolve(process.env.SCENARIO_FILTER_EVIDENCE_DIR || 'test-results/scenario-filter-help');
  await mkdir(evidence,{recursive:true});
  await page.emulateMedia({reducedMotion:'reduce'});
  const measurements = [], requests = [];
  page.on('request',request => requests.push(request.url()));
  for (const theme of ['light','dark']) {
    await page.emulateMedia({colorScheme:theme});
    for (const width of [1440,320,390,767,768,769,1024]) {
      await page.setViewportSize({width,height:900});
      for (const fixture of [{total:41,page:1},{total:41,page:3},{total:0,page:1}]) {
        const url = `${base}/fixtures/s6/scenarios?total=${fixture.total}&page=${fixture.page}`;
        await page.goto(url);
        await settle(page);
        const count = await page.locator('.scenario-card').count();
        // Synthetic statuses exercise every publication/activation combination.
        await page.locator('.scenario-card').evaluateAll(cards => cards.forEach((card,index) => {
          const published = index % 2 === 0, active = index % 4 < 2;
          card.dataset.publication = published ? 'published' : 'draft';
          card.classList.toggle('inactive',!active);
          const badge = card.querySelector('.status-badge');
          badge.classList.toggle('active',active);
          badge.textContent = active ? '활성' : '비활성';
          card.querySelector('.card-status .meta-tag').textContent = published ? '게시' : '초안';
        }));
        const requestCount = requests.length;
        for (const longHelp of [false,true]) {
          if (longHelp) await page.locator('#scenario-filter-help').evaluate(el => { el.textContent = '검색과 필터는 현재 페이지에 있는 시나리오에만 적용됩니다. '.repeat(12); });
          for (const search of ['', '시나리오', 'no_matching_scenario_132']) {
            await page.locator('#search-input').fill(search);
            for (const publication of ['', 'draft', 'published']) {
              await page.locator('#filter-publication').selectOption(publication);
              for (const activation of ['', 'active', 'inactive']) {
                await page.locator('#filter-status').selectOption(activation);
                const label = `${theme}/${width}/page${fixture.page}/total${fixture.total}/long${longHelp}/${search}/${publication}/${activation}`;
                const layout = await page.evaluate(`(${measureFilter})(${colorMath})`);
                assertLayout(layout,label,width);
                assert.equal(Boolean(layout.status),count > 0 && layout.cards.length === 0,`${label}: no-results status`);
                const matches = count === 20 ? 20/(publication ? 2 : 1)/(activation ? 2 : 1)
                  : publication === 'draft' || activation === 'inactive' ? 0 : count;
                assert.equal(layout.cards.length,search.startsWith('no_matching') ? 0 : matches,`${label}: current-page combination results`);
                measurements.push({theme,width,...fixture,longHelp,search,publication,activation,...layout});
                if (longHelp && [390,1440].includes(width) && search.startsWith('no_matching') && !publication && !activation && count) {
                  await page.evaluate(() => scrollTo(0,0));
                  await page.screenshot({path:resolve(evidence,`${theme}-${width}-page${fixture.page}-no-results.png`)});
                }
              }
            }
          }
          await page.locator('#search-input').fill('');
          await page.locator('#filter-publication').selectOption('');
          await page.locator('#filter-status').selectOption('');
          assert.equal(await page.locator('.scenario-card:visible').count(),count,'clearing restores current page');
          if ([390,1440].includes(width)) {
            await page.evaluate(() => scrollTo(0,0));
            await page.screenshot({path:resolve(evidence,`${theme}-${width}-total${fixture.total}-page${fixture.page}-long${longHelp}.png`)});
          }
        }
        assert.equal(page.url(),url,'filters keep current page URL');
        assert.equal(requests.length,requestCount,'filters make no server requests');
      }
    }
  }
  await writeFile(resolve(evidence,'measurements.json'),JSON.stringify(measurements,null,2)+'\n');
  return {pageErrors:[]};
}
