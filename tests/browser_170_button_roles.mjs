import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import {state} from './browser_ai_connections.mjs';
import {comparisonFixture} from './s5_browser_fixtures.mjs';
import {colorMath} from './uiux_color_math.mjs';
import {settle} from './browser_uiux_scenario_layout.mjs';

const baseline = process.env.BUTTONS_170_BASELINE_ONLY === '1';
const evidence = process.env.BUTTONS_170_EVIDENCE_DIR;
const phase = baseline ? 'before' : 'after';
const roles = {
  primary:['replace_key','save_key','register_selected_model','submit_change','save_settings',
    'run_comparison','start_comparison','send_question','mentor_help','pending_automatic_check',
    'end_comparison_and_analyze','adopt_student_response','apply_this_model'],
  secondary:['disable','re_enable','verify_without_generation_and_refresh_catalog','enter_model_id_manually',
    'edit_display_name_activation_and_options','edit_authoring_defaults_and_call_settings','close',
    'close_comparison','add_completed_pair','add_panel','stop_all','refresh_state','reset_comparison_input',
    'comparison_history','next_20','stop_panel','retry_panel'],
  danger:['delete_key','delete_comparison_record'],
};

function measure({selector, expected, colorMath}) {
  const {rgba, background, contrast} = colorMath();
  const visible = el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
  const controls = [...document.querySelectorAll(selector)].filter(visible);
  const buttons = controls.map(el => {
    const style = getComputedStyle(el), box = el.getBoundingClientRect();
    const text = el.textContent.trim();
    const action = el.dataset.action;
    const role = expected[text] || (action ? {stop:'secondary',retry:'secondary',adopt:'primary',apply:'primary'}[action] : null)
      || (el.closest('.comparison-tabs') || /^(패널|Panel) \d+ (제거|Remove)$/.test(text) ? 'secondary' : null)
      || null;
    return {text,action,role,classes:[...el.classList],disabled:el.matches(':disabled'),
      width:box.width,height:box.height,ratio:contrast(rgba(style.color),background(el))};
  });
  const overlaps = [];
  for (let i=0;i<controls.length;i++) for (let j=i+1;j<controls.length;j++) {
    const a = controls[i].getBoundingClientRect(), b = controls[j].getBoundingClientRect();
    if (Math.min(a.right,b.right)-Math.max(a.left,b.left) > 1 && Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top) > 1)
      overlaps.push([buttons[i].text,buttons[j].text]);
  }
  return {buttons,overlaps,documentWidth:document.documentElement.scrollWidth};
}

export default async function checkButtonRoles(page) {
  page.setDefaultTimeout(8000);
  const base = new URL(page.url()).origin;
  const rows = [], failures = [];
  const expect = (condition,message) => { if (!condition) failures.push(message); };
  if (evidence) await mkdir(evidence,{recursive:true});
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.route('**/admin/ai/state',route => route.fulfill({json:state()}));
  await page.route('**/admin/comparison-conversations**',route => route.fulfill({json:{items:[{
    conversation_id:'retained-170',title:'Synthetic retained comparison',created_at:'2026-10-01',status:'ended',
  }],next_cursor:null}}));
  let newState = 'mixed', newSnapshot;
  await page.route('**/admin/comparison-conversations/visual-only',route => route.fulfill({json:newSnapshot}));
  await page.route('**/fixtures/uiux/comparison',async route => {
    const response = await route.fetch();
    const html = (await response.text()).replace(/(<script id="comparison-conversation-data" type="application\/json">)(.*?)(<\/script>)/s,(_,open,json,close) => {
      const data = JSON.parse(json), snapshot = data.snapshot;
      snapshot.pairs = [{turn_id:'turn-170',teacher:'공통 교사 질문 common teacher question',student:'채택한 학생 응답 adopted student response'}];
      snapshot.children[0].panels.forEach((panel,index) => Object.assign(panel,{
        run_id:`generation-${index}`,status:newState === 'running' ? 'running' : newState === 'normal' ? 'completed' : ['completed','failed','interrupted'][index],
      }));
      if (newState === 'disabled') {
        snapshot.allowed_actions = {turn:false,mentor:false,end:false,adopt:false};
        snapshot.pending_student_child_id = null;
      } else if (newState === 'running') snapshot.allowed_actions = {turn:false,mentor:false,end:false,adopt:false};
      newSnapshot = structuredClone(snapshot);
      // Enter running state through refresh so recovery polling cannot replace measured controls.
      if (newState === 'running') snapshot.children[0].panels.forEach(panel => { panel.status = 'completed'; });
      return open+JSON.stringify(data)+close;
    });
    await route.fulfill({response,body:html});
  });
  const old = await comparisonFixture(page);

  async function capture(meta,screen,selector,override={}) {
    await page.mouse.move(0,0);
    await page.locator('body').click({position:{x:1,y:1}});
    await settle(page);
    const expected = await page.evaluate(roles => {
      const result = Object.fromEntries(Object.entries(roles).flatMap(([role,keys]) => keys.map(key => [uiLanguage.t('admin.'+key),role])));
      for (const label of ['student','mentor','post_session_analysis']) for (const action of ['test','check_progress','cancel_test'])
        result[`${uiLanguage.t('admin.'+label)} ${uiLanguage.t('admin.'+action)}`] = action === 'test' ? 'primary' : 'secondary';
      for (const index of [1,2,3]) result[`${uiLanguage.t('admin.panel')} ${index} ${uiLanguage.t('admin.remove')}`] = 'secondary';
      return result;
    },roles);
    Object.assign(expected,override);
    // The static refresh label differs from the comparison refresh label.
    const refresh = page.locator('#ai-refresh');
    if (await refresh.count()) expected[(await refresh.innerText()).trim()] = 'secondary';
    const row = {...meta,screen,...await page.evaluate(`(${measure})({selector:${JSON.stringify(selector)},expected:${JSON.stringify(expected)},colorMath:(${colorMath})})`)};
    rows.push(row);
    const label = `${meta.language}/${meta.theme}/${meta.width}/${screen}`;
    expect(row.buttons.length > 0,`${label}: rendered action buttons`);
    expect(row.documentWidth <= meta.width,`${label}: document overflow ${row.documentWidth}`);
    expect(row.overlaps.length === 0,`${label}: buttons overlap ${JSON.stringify(row.overlaps)}`);
    for (const button of row.buttons) {
      expect(button.role,`${label}: unclassified action ${button.text}`);
      expect(button.classes.includes(`btn-${button.role}`),`${label}: ${button.text} needs btn-${button.role}; actual ${button.classes.join(' ') || '(none)'}`);
      expect(button.width >= 44 && button.height >= 44,`${label}: ${button.text} target ${button.width}x${button.height}`);
      expect(button.ratio >= 4.5,`${label}: ${button.text} default contrast ${button.ratio}`);
    }
    if (evidence && [320,390,1180].includes(meta.width)) {
      await page.evaluate(() => scrollTo(0,0));
      await page.screenshot({path:`${evidence}/${phase}-${screen}-${meta.language}-${meta.theme}-${meta.width}.png`,fullPage:true});
    }
    // Each role shares its variant CSS; exercise real hover and keyboard focus once per enabled role/state.
    row.interactions = [];
    for (const role of ['primary','secondary','danger']) {
      const index = row.buttons.findIndex(button => button.role === role && !button.disabled);
      if (index < 0) continue;
      const control = page.locator(selector).filter({visible:true}).nth(index);
      await control.hover();
      await settle(page);
      for (const interaction of ['hover','focus']) {
        if (interaction === 'focus') {
          await page.mouse.move(0,0);
          await page.keyboard.press('Tab');
          await control.focus();
          await settle(page);
        }
        const result = await control.evaluate(new Function('el',`const {rgba,background,contrast}=(${colorMath})(); const s=getComputedStyle(el), bg=background(el); return {ratio:contrast(rgba(s.color),bg),color:s.color,backgroundColor:s.backgroundColor,background:bg,connected:el.isConnected,hovered:el.matches(':hover'),focused:el===document.activeElement,keyboard:el.matches(':focus-visible')};`));
        row.interactions.push({role,interaction,...result});
        expect(result.ratio >= 4.5,`${label}: ${role} ${interaction} contrast ${result.ratio}`);
        expect(result.connected,`${label}: ${role} ${interaction} stays connected`);
        if (interaction === 'hover') expect(result.hovered,`${label}: ${role} receives real pointer hover`);
        if (interaction === 'focus') expect(result.focused && result.keyboard,`${label}: ${role} keyboard focus visible`);
      }
    }
    if (!baseline && failures.length) {
      if (evidence) await writeFile(`${evidence}/${phase}-measurements.json`,JSON.stringify({rows,failures},null,2)+'\n');
      assert.equal(failures.length,0,`${failures.length} button failures: ${failures.slice(0,12).join('; ')}`);
    }
    return row;
  }

  for (const language of baseline ? ['ko'] : ['ko','en']) {
    await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
    for (const theme of baseline ? ['light'] : ['light','dark']) {
      await page.emulateMedia({colorScheme:theme});
      for (const width of baseline ? [320,390,1180] : [320,390,768,1024,1180,1440]) {
        const meta = {language,theme,width};
        console.log(`170 ${phase}: ${language}/${theme}/${width}`);
        await page.setViewportSize({width,height:900});
        await page.goto(`${base}/admin/ai`);
        await page.locator('[data-provider="google"]').waitFor();
        await capture(meta,'ai-filled','#ai-content button, #ai-refresh');
        await page.locator('#ai-content section').last().getByRole('button').click();
        await page.locator('#ai-editor form').waitFor();
        await capture(meta,'ai-settings','#ai-editor button');
        await page.locator('#ai-editor button[type=button]').last().click();
        await page.locator('[data-provider="openai"] .ai-actions button').nth(2).click();
        const deleteSubmit = page.locator('#ai-editor button[type=submit]');
        await deleteSubmit.waitFor();
        await capture(meta,'ai-delete','#ai-editor button',{[(await deleteSubmit.innerText()).trim()]:'danger'});
        expect(await page.locator('#ai-editor input[type=password]').count() === 1,`${language}: deletion retains password confirmation`);

        for (const variant of ['normal','running','mixed','disabled']) {
          newState = variant;
          await page.goto(`${base}/fixtures/uiux/comparison`);
          await page.locator('#conversation-rounds article').first().waitFor();
          if (variant === 'running') {
            const refreshLabel = await page.evaluate(() => uiLanguage.t('admin.refresh_state'));
            await page.locator('#conversation-controls').getByRole('button',{name:refreshLabel,exact:true}).click();
            await page.waitForFunction(() => {
              const panels = [...document.querySelectorAll('#conversation-rounds [data-status]')];
              return panels.length === 3 && panels.every(panel => panel.dataset.status === 'running');
            });
          }
          const row = await capture(meta,`comparison-new-${variant}`,'#conversation-controls button, #conversation-rounds button, #conversation-history button');
          const actions = row.buttons.filter(button => button.action);
          expect(actions.length === 12,`${variant}: all three new panels retain four action controls`);
          if (variant === 'running') expect(actions.filter(button => button.action==='stop').every(button => !button.disabled) && actions.filter(button => button.action!=='stop').every(button => button.disabled),'running new panels keep stop enabled and retry/adopt/apply locked');
          if (variant === 'disabled') expect(actions.filter(button => ['retry','adopt'].includes(button.action)).every(button => button.disabled),'server disallowed new adoption/retry stays disabled');
        }

        await page.goto(`${base}/fixtures/s2/editor`);
        await page.locator('#open-comparison').click();
        await page.locator('[data-comparison-panel]').first().waitFor();
        await page.locator('#comparison-question').fill('같은 전체에서 비교하면? Common teacher question');
        await capture(meta,'comparison-old-normal','#scenario-comparison button');
        const runLabel = await page.evaluate(() => uiLanguage.t('admin.run_comparison'));
        await page.locator('#scenario-comparison').getByRole('button',{name:runLabel,exact:true}).click();
        await page.locator('[data-action="retry"]').first().waitFor();
        await capture(meta,'comparison-old-mixed','#scenario-comparison button');
        const refreshLabel = await page.evaluate(() => uiLanguage.t('admin.refresh_state'));
        for (const [variant,statuses] of [['running',['running','running','running']],['disabled',['completed','running','failed']]]) {
          old.finish(statuses);
          await page.locator('#scenario-comparison').getByRole('button',{name:refreshLabel,exact:true}).click();
          const row = await capture(meta,`comparison-old-${variant}`,'#scenario-comparison button');
          expect(row.buttons.filter(button => ['retry','adopt','apply'].includes(button.action)).every(button => button.disabled),`${variant}: old comparison preserves locked result actions`);
        }
      }
    }
  }
  assert.equal(old.unexpected.length,0,'synthetic fixture makes no teacher/session/external requests');
  if (evidence) await writeFile(`${evidence}/${phase}-measurements.json`,JSON.stringify({rows,failures},null,2)+'\n');
  assert.equal(rows.length,baseline ? 33 : 264,'all requested screen states and viewport combinations recorded');
  if (!baseline) assert.equal(failures.length,0,`${failures.length} button failures: ${failures.slice(0,12).join('; ')}; evidence ${evidence || '(not requested)'}`);
  console.log(`${baseline ? 'BASELINE' : 'CHECKED'} ${rows.length} screen states; ${failures.length} contract failures recorded`);
  return {pageErrors:[]};
}
