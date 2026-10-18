import assert from 'node:assert/strict';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {state} from './browser_ai_connections.mjs';
import {colorMath} from './uiux_color_math.mjs';
import {settle} from './browser_uiux_scenario_layout.mjs';

const evidence = process.env.BROWSER_173_ARTIFACT_DIR || '/tmp/mp2-browser-173';
const baseline = process.env.BROWSER_173_BASELINE_CSS_DIR;
const phase = baseline ? 'before' : 'after';

function measure(root, colorMath) {
  const {rgba, over, background, contrast} = colorMath();
  const visible = el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
  const rect = el => { const r = el.getBoundingClientRect(); return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height}; };
  const ratio = el => contrast(over(rgba(getComputedStyle(el).color),background(el)),background(el));
  const controls = [...root.querySelectorAll('input:not([type=hidden]):not([type=file]):not([type=range]),select,textarea')].filter(visible).map(el => {
    const s = getComputedStyle(el), box = rect(el), target = el.type === 'checkbox' ? el.closest('label') || el : el;
    const label = el.previousElementSibling?.tagName === 'LABEL' ? el.previousElementSibling : null;
    const help = el.nextElementSibling?.matches('small,[role=status]:not(:empty)') ? el.nextElementSibling : null;
    return {name:el.id || el.name,type:el.type,box,target:rect(target),disabled:el.disabled,value:el.value,
      textContrast:ratio(el),borderInside:contrast(rgba(s.borderTopColor),background(el)),
      borderOutside:contrast(rgba(s.borderTopColor),background(el.parentElement)),resize:s.resize,
      labelGap:label && visible(label) ? box.y-rect(label).bottom : null,
      helpGap:help && visible(help) ? rect(help).y-box.bottom : null,
      overflow:el.type !== 'checkbox' && box.right > rect(el.closest('.form-group,fieldset,article') || root).right+1};
  });
  const groups = [...root.querySelectorAll('.form-group')].filter(visible);
  const repeated = groups.flatMap(el => {
    const next = el.nextElementSibling;
    return next?.matches('.form-group') && visible(next) && rect(next).y > rect(el).y
      ? [{before:el.querySelector('input,textarea,select')?.id,after:next.querySelector('input,textarea,select')?.id,gap:rect(next).y-rect(el).bottom}] : [];
  });
  const text = [...root.querySelectorAll('label,small,.scenario-error,.modal-error,[role=alert]')].filter(el => visible(el) && el.textContent.trim()).map(el => ({
    text:el.textContent.trim().slice(0,60),ratio:ratio(el),overflow:el.scrollWidth > el.clientWidth+1,
  }));
  const errors = [...root.querySelectorAll('.scenario-error:not([hidden]),.form-error:not([hidden])')].filter(visible).map(el => {
    let previous = el.previousElementSibling;
    while (previous && !previous.textContent.trim() && !previous.matches('input,textarea,select,.form-group')) previous = previous.previousElementSibling;
    const anchor = previous?.matches('.form-group') ? [...previous.querySelectorAll('input,textarea,select,small:not(:empty)')].filter(visible).at(-1) : previous;
    return {name:el.id,gap:anchor ? rect(el).y-rect(anchor).bottom : null};
  });
  return {controls,repeated,text,errors,overflow:document.documentElement.scrollWidth > innerWidth,
    density:root.closest('.form-density--comfortable,.form-density--compact')?.className || null};
}

export default async function check(page) {
  const base = new URL(page.url()).origin, rows = [], failures = [];
  const expect = (condition,message) => { if (!condition) failures.push(message); };
  await mkdir(evidence,{recursive:true});
  await page.emulateMedia({reducedMotion:'reduce'});
  if (baseline) await page.route('**/static/css/*.css',async route => {
    const name = new URL(route.request().url()).pathname.split('/').at(-1);
    if (!['styles.css','scenario-editor.css','scenario-comparison.css'].includes(name)) return route.continue();
    await route.fulfill({contentType:'text/css',body:await readFile(`${baseline}/${name}`,'utf8')});
  });
  await page.route('**/admin/ai/state',route => route.fulfill({json:state()}));
  await page.route('**/admin/comparison-conversations**',route => route.fulfill({json:{items:[],next_cursor:null}}));
  await page.route('**/admin/scenarios/1/update',route => route.fulfill({status:422,json:{detail:[
    {path:'title',message:'173 validation error: '+ '긴 오류 long error '.repeat(12)},
    {path:'analysis.rubric.0.criteria',message:'173 rubric validation error'},
    {path:'student.resolved_model_config.options.max_output_tokens',message:'173 model option validation error'},
  ]}}));
  async function capture(meta,screen,selector,density=null,filter=false) {
    await settle(page);
    const root = page.locator(selector);
    const row = {...meta,screen,...await root.evaluate(new Function('root',`return (${measure})(root,(${colorMath}));`))};
    rows.push(row);
    const name = `${meta.language}/${meta.theme}/${meta.width}/${screen}`;
    expect(!row.overflow,`${name}: document overflow`);
    if (density) expect(row.density?.includes(`form-density--${density}`),`${name}: explicit ${density} density`);
    for (const control of row.controls) {
      expect(control.target.width >= 43.99 && control.target.height >= 43.99,`${name}/${control.name}: target ${control.target.width}×${control.target.height}`);
      expect(!control.overflow,`${name}/${control.name}: control stays inside form`);
      if (control.type !== 'checkbox' && !control.disabled) {
        expect(control.textContrast >= 4.5,`${name}/${control.name}: text contrast ${control.textContrast}`);
        expect(control.borderInside >= 3 && control.borderOutside >= 3,`${name}/${control.name}: input boundary contrast`);
      }
      if (density && control.labelGap !== null) expect(Math.abs(control.labelGap-(density === 'comfortable' ? 8 : 4)) < .1,`${name}/${control.name}: label gap ${control.labelGap}`);
      if (density && control.helpGap !== null) expect(Math.abs(control.helpGap-4)<.1,`${name}/${control.name}: help gap ${control.helpGap}`);
      if (density && control.type === 'textarea') expect(control.resize === 'vertical',`${name}/${control.name}: native vertical textarea resize`);
    }
    for (const text of row.text) {
      expect(text.ratio >= 4.5,`${name}/${text.text}: text contrast ${text.ratio}`);
      expect(!text.overflow,`${name}/${text.text}: text wraps without clipping`);
    }
    for (const group of row.repeated) if (density) expect(Math.abs(group.gap-(filter ? 8 : density === 'comfortable' ? 24 : 16)) < .1,`${name}/${group.before}: repeated gap ${group.gap}`);
    for (const error of row.errors) expect(error.gap === null || Math.abs(error.gap-4)<.1,`${name}/${error.name}: error gap ${error.gap}`);
    await writeFile(`${evidence}/${phase}-density-measurements.json`,JSON.stringify({rows,failures},null,2)+'\n');
    if (meta.language === 'ko' && meta.theme === 'light' && [320,390,1180].includes(meta.width)) {
      await page.evaluate(() => scrollTo(0,0));
      await page.screenshot({path:`${evidence}/${phase}-density-${screen}-${meta.width}.png`,fullPage:true});
    }
    if (baseline) assert.equal(failures.length,0,failures.slice(0,20).join('; '));
  }
  for (const language of ['ko','en']) {
    await page.context().addCookies([{name:'ui_lang',value:language,url:base}]);
    for (const theme of ['light','dark']) {
      await page.emulateMedia({colorScheme:theme});
      for (const width of [320,390,768,1024,1180,1440]) {
        const meta = {language,theme,width};
        console.log(`173 density ${phase}: ${language}/${theme}/${width}`);
        await page.setViewportSize({width,height:900});
        await page.goto(`${base}/login?error=1`);
        await page.locator('#username-help').evaluate(el => { el.textContent += ' 긴 도움말 long help '.repeat(20); });
        await capture(meta,'login','.login-form','comfortable');
        await page.goto(`${base}/fixtures/s2/editor`);
        await page.waitForFunction(() => document.querySelector('#editor-status').textContent);
        const checkbox = page.locator('#scenario-groups input[type=checkbox]').first();
        assert.equal(await checkbox.count(),1,'fixture includes a real group-assignment checkbox');
        const checked = await checkbox.isChecked();
        await checkbox.focus(); await page.keyboard.press('Space');
        expect(await checkbox.isChecked() !== checked,'native checkbox toggles with Space');
        await page.keyboard.press('Space');
        await page.locator('#title').fill('Preserved 173 title');
        await page.locator('#save-draft').click();
        await page.locator('[id="title-error"]').waitFor({state:'visible'});
        expect(await page.locator('#title').inputValue() === 'Preserved 173 title','validation preserves editor input');
        for (let step=0;step<5;step++) {
          await page.locator(`[data-step="${step}"]`).click();
          if (step === 1) await page.locator('[data-field="problem.public_text"]').fill('Multiline preserved\nSecond line '.repeat(20));
          await capture(meta,`editor-${step}`,'.scenario-editor','compact');
        }
        await page.locator('#open-comparison').click();
        await page.locator('[data-comparison-panel]').first().waitFor();
        await capture(meta,'comparison-editor','#scenario-comparison','compact');
        await page.goto(`${base}/fixtures/uiux/comparison`);
        await page.locator('#conversation-rounds article').first().waitFor();
        await capture(meta,'comparison','.comparison-page','compact');
        for (const [screen,path,root] of [
          ['filters-users','/fixtures/s6/users','.filter-bar'],
          ['filters-scenarios','/fixtures/s6/scenarios','.filter-bar'],
          ['filters-analysis','/fixtures/uiux/admin/analysis','#analysis-filter-form'],
          ['filters-sessions','/fixtures/uiux/admin/sessions','#filter-form'],
        ]) { await page.goto(base+path); await capture(meta,screen,root,'compact',true); }
        await page.goto(`${base}/admin/ai`);
        await page.locator('[data-provider="google"]').waitFor();
        await capture(meta,'ai','#ai-page','compact');
        await page.locator('#ai-content section').last().getByRole('button').click();
        await page.locator('#ai-editor input').first().waitFor();
        expect(await page.locator('#ai-editor').getAttribute('role') !== 'dialog','AI editor stays inline');
        await capture(meta,'ai-settings','#ai-editor','compact');
        for (const [screen,path,open,root] of [
          ['admin-users','/fixtures/s6/users','#open-create-modal','#create-form'],
          ['admin-user-edit','/fixtures/s6/users','.edit-btn','#edit-form'],
          ['admin-groups','/fixtures/uiux/admin/groups','#open-create-modal','#create-form'],
          ['admin-about','/fixtures/uiux/admin/about','#open-create-modal','#create-form'],
        ]) {
          await page.goto(base+path); await page.locator(open).first().click();
          await capture(meta,screen,root,'comfortable');
          if (screen === 'admin-user-edit') expect(await page.locator('#edit-username-display').isDisabled(),'user identifier remains disabled');
          await page.keyboard.press('Escape');
        }
        for (const [screen,path] of [['teacher-list','/scenarios'],['teacher-chat','/chat'],['teacher-analysis','/fixtures/s4/analysis']]) {
          await page.goto(base+path); await capture(meta,screen,'main');
        }
      }
    }
  }
  await writeFile(`${evidence}/${phase}-density-measurements.json`,JSON.stringify({rows,failures},null,2)+'\n');
  console.log(`173 density: ${rows.length} screen measurements, ${failures.length} failures`);
  assert.equal(failures.length,0,failures.slice(0,20).join('; '));
  return {pageErrors:[]};
}
