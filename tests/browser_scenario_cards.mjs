import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {colorMath} from './uiux_color_math.mjs';
import {measure, settle} from './browser_uiux_scenario_layout.mjs';

export default async function checkScenarioCards(page) {
  const base = new URL(page.url()).origin;
  const evidence = resolve(process.env.SCENARIO_LAYOUT_EVIDENCE_DIR || 'test-results/scenario-cards');
  await mkdir(evidence, {recursive:true});
  await page.emulateMedia({reducedMotion:'reduce'});
  const measurements = [];
  const cases = [{total:0,page:1}, {total:1,page:1}, {total:20,page:1}, {total:21,page:2}, {total:41,page:3}];
  for (const theme of ['light','dark']) {
    await page.emulateMedia({colorScheme:theme});
    for (const width of [1440,320,390,767,768,769,1024]) {
      await page.setViewportSize({width,height:900});
      for (const fixture of cases) {
        await page.goto(`${base}/fixtures/s6/scenarios?total=${fixture.total}&page=${fixture.page}&uiux123=1`);
        for (const stress of [false,true]) {
          if (stress) await page.evaluate(() => {
            const cards = [...document.querySelectorAll('.scenario-card')];
            const legacy = cards[1]?.innerHTML;
            cards.forEach((card, index) => {
              if (index >= 16) card.innerHTML = legacy;
              card.querySelector('.card-title').textContent = '긴한글시나리오제목'.repeat(20);
              const meta = card.querySelector('.card-meta');
              for (let group = 0; group < 8; group++) {
                const tag = document.createElement('span');
                tag.className = 'meta-tag';
                tag.textContent = `여러그룹의아주긴이름${group}`.repeat(3);
                meta.append(tag);
              }
              // Exercise all native activation/publication/review combinations.
              if (index >= 2) {
                const active = index % 2 === 0;
                const published = index < 16 && Math.floor(index / 2) % 2 === 0;
                card.classList.toggle('inactive', !active);
                const activation = card.querySelector('.status-badge');
                activation.textContent = active ? '활성' : '비활성';
                const publication = card.querySelector('.card-status .meta-tag');
                publication.classList.toggle('badge-success', published);
                publication.textContent = published ? '게시됨' : '초안';
                if (index >= 16 ? index % 4 >= 2 : Math.floor(index / 4) % 2 === 0) {
                  const review = document.createElement('span');
                  review.className = 'meta-tag badge-warning';
                  review.textContent = '검토 필요';
                  card.querySelector('.card-status').append(review);
                }
              }
            });
          });
          await settle(page);
          const badges = await page.locator('.card-status > span').evaluateAll(elements => elements.map(el => {
            const text = el.textContent.trim();
            const kind = text === '게시됨' ? 'success' : ['검토 필요','변환 필요'].includes(text) ? 'warning' : 'neutral';
            const probe = document.createElement('span');
            probe.style.color = `var(--color-${kind === 'neutral' ? 'text-secondary' : `${kind}-text`})`;
            probe.style.backgroundColor = `var(--color-${kind === 'neutral' ? 'bg-secondary' : `${kind}-bg`})`;
            el.parentElement.append(probe);
            const actual = getComputedStyle(el), expected = getComputedStyle(probe);
            const result = {text,kind,classes:[...el.classList],color:actual.color,background:actual.backgroundColor,expectedColor:expected.color,expectedBackground:expected.backgroundColor};
            probe.remove();
            return result;
          }));
          for (const badge of badges) {
            assert.equal(badge.color,badge.expectedColor,`${theme}: ${badge.text} foreground token`);
            assert.equal(badge.background,badge.expectedBackground,`${theme}: ${badge.text} background token`);
            assert.equal(badge.classes.includes('badge-success'),badge.kind === 'success');
            assert.equal(badge.classes.includes('badge-warning'),badge.kind === 'warning');
            if (badge.classes.includes('status-badge')) assert(!badge.classes.includes('active'),'activation stays neutral');
          }
          const layout = await page.evaluate(() => {
            const box = el => {
              const r = el.getBoundingClientRect();
              return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};
            };
            const header = document.querySelector('.page-header-admin');
            return {
              header:box(header),
              toolbar:[...header.querySelectorAll('a')].map(box),
              cards:[...document.querySelectorAll('.scenario-card')].map(card => ({
                box:box(card),body:box(card.querySelector('.card-body')),actions:box(card.querySelector('.card-actions')),
                heading:card.querySelector('.card-heading')?.textContent,
                title:card.querySelector('.card-title').textContent,
                status:card.querySelector('.card-status').textContent,
                active:!card.classList.contains('inactive'),
                meta:card.querySelector('.card-meta').textContent,
                leaves:[...card.querySelectorAll('.card-title, .status-badge, .meta-tag, .card-description, .btn')].map(el => ({text:el.textContent,clipped:el.scrollWidth > el.clientWidth+1 || el.scrollHeight > el.clientHeight+1,...box(el)})),
                actionNames:[...card.querySelectorAll('.card-actions .btn')].map(el => el.textContent.trim()),
              })),
            };
          });
          const label = `${theme}/${width}/${fixture.total}/page${fixture.page}/stress${stress}`;
          const [compare, history, create] = layout.toolbar;
          assert(Math.abs(compare.left-layout.header.left) < 1, `${label}: secondary group at left edge`);
          if (width >= 769) {
            assert(Math.abs(compare.top-history.top) < 1, `${label}: secondary actions share a row`);
            assert(history.left-compare.right >= 8 && history.left-compare.right <= 24, `${label}: secondary actions grouped with 8–24px gap`);
            assert(Math.abs(create.right-layout.header.right) < 1, `${label}: create at right edge`);
          }
          const overlaps = (a,b) => a.left < b.right-0.5 && b.left < a.right-0.5 && a.top < b.bottom-0.5 && b.top < a.bottom-0.5;
          for (const [index, control] of layout.toolbar.entries()) {
            assert(control.left >= 0 && control.right <= width, `${label}: toolbar fits`);
            for (const other of layout.toolbar.slice(index+1)) {
              assert(!overlaps(control,other), `${label}: toolbar does not overlap`);
              assert(other.left-control.right >= 8 || other.top-control.bottom >= 8, `${label}: toolbar gap at least 8px`);
            }
          }
          assert.equal(layout.cards.length, Math.min(20,fixture.total-(fixture.page-1)*20));
          for (const card of layout.cards) {
            assert(card.heading?.includes(card.title) && card.heading.includes(card.status), `${label}: title and statuses share top area`);
            assert(!/활성|초안|게시|검토 필요|변환 필요/.test(card.meta), `${label}: metadata separate from status`);
            if (layout.cards.length === 1 && width >= 769) {
              assert(card.actions.left >= card.body.right+8, `${label}: single wide card has horizontal information/actions`);
            } else assert(card.actions.top >= card.body.bottom, `${label}: narrow cards use vertical information/actions`);
            const legacy = card.status.includes('변환 필요');
            assert.deepEqual(card.actionNames,legacy ? ['대화목록'] : ['대화목록','모델 비교','수정','삭제']);
            for (const [index,leaf] of card.leaves.entries()) {
              assert(!leaf.clipped, `${label}: content not clipped: ${leaf.text}`);
              assert(leaf.left >= card.box.left && leaf.right <= card.box.right+0.5 && leaf.top >= card.box.top && leaf.bottom <= card.box.bottom+0.5, `${label}: content inside card: ${leaf.text}`);
              for (const other of card.leaves.slice(index+1)) assert(!overlaps(leaf,other), `${label}: card content does not overlap: ${leaf.text}/${other.text}`);
            }
          }
          if (fixture.total === 20) {
            assert(layout.cards[0].status.includes('검토 필요'), `${label}: review preserved`);
            assert(layout.cards[1].status.includes('변환 필요'), `${label}: conversion preserved`);
            const columns = new Set(layout.cards.map(card => card.box.left)).size;
            assert(width <= 768 ? columns === 1 : columns > 1, `${label}: existing grid columns preserved`);
            if (stress) {
              const combinations = legacy => new Set(layout.cards.filter(card => card.status.includes('변환 필요') === legacy)
                .map(card => [card.active,card.status.includes('게시'),card.status.includes('검토 필요')].join('/'))).size;
              assert.equal(combinations(false),8, `${label}: all native status combinations`);
              assert.equal(combinations(true),4, `${label}: all legacy status combinations`);
            }
          }
          const colors = await page.evaluate(`(${measure})(${colorMath})`);
          assert(!colors.overflow, `${label}: no document overflow`);
          assert(colors.targets.every(target => target.width >= 44 && target.height >= 44), `${label}: 44px targets`);
          assert(colors.text.every(text => text.ratio >= text.minimum), `${label}: text contrast`);
          measurements.push({theme,width,...fixture,stress,...layout,...colors});
          if (stress && [320,390,768,769,1440].includes(width) && [0,1,20,21,41].includes(fixture.total)) {
            await page.screenshot({path:resolve(evidence,`${theme}-${width}-total${fixture.total}-page${fixture.page}.png`),fullPage:fixture.total !== 20});
          }
        }
      }
    }
  }
  await writeFile(resolve(evidence,'measurements.json'),JSON.stringify(measurements,null,2)+'\n');
  return {pageErrors:[]};
}
