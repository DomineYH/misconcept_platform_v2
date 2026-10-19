import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {mkdir, mkdtemp, readFile, readdir, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {test} from 'node:test';
import {discoverModules, executionIdentity, makePlan, parseRunnerArgs, validateFleet} from './ci_browser_sharding.mjs';

const files = ['browser_c.mjs', 'browser_a.mjs', 'browser_b.mjs', 'browser_d.mjs'];
const manifest = {defaultEstimateMs: 100, durations: [
  {file: 'browser_a.mjs', ms: 6}, {file: 'browser_b.mjs', ms: 4},
  {file: 'browser_c.mjs', ms: 3}, {file: 'browser_d.mjs', ms: 1}
]};
const identity = {commit: 'abc', runId: '42', runAttempt: '1'};
function reportsFor(plan) {
  return plan.partitions.map((partition, index) => ({
    schemaVersion: 1, ...identity, inventoryId: plan.inventoryId, planId: plan.planId,
    shard: index + 1, shardCount: plan.partitions.length, status: 'PASS',
    plannedModules: partition.modules,
    executedModules: partition.modules.map(module => ({module, status: 'PASS', elapsedMs: 1}))
  }));
}
const verify = (reports, plan = makePlan(files, manifest, 3), result = 'success') =>
  validateFleet({reports, plan, identity, jobResult: result, downloadResult: 'success'});

test('longest-estimated-first balances deterministically with lexical and shard-index ties', () => {
  const plan = makePlan(files, manifest, 3);
  assert.deepEqual(plan.partitions, [
    {modules: ['browser_a.mjs'], estimatedMs: 6},
    {modules: ['browser_b.mjs'], estimatedMs: 4},
    {modules: ['browser_c.mjs', 'browser_d.mjs'], estimatedMs: 4}
  ]);
  assert.deepEqual(makePlan([...files].reverse(), manifest, 3), plan);
  const tied = makePlan(files, {defaultEstimateMs: 1, durations: []}, 2);
  assert.deepEqual(tied.partitions.map(p => p.modules), [
    ['browser_a.mjs', 'browser_c.mjs'], ['browser_b.mjs', 'browser_d.mjs']
  ]);
  assert.notEqual(makePlan(files, manifest, 2).planId, plan.planId);
});

test('discovery retains every browser module, excludes helpers, rejects duplicate inventory', () => {
  assert.deepEqual(discoverModules([...files, 'helper.mjs', 'check_browser.mjs']), [...files].sort());
  assert.throws(() => makePlan([...files, files[0]], manifest, 3), /duplicate/i);
  assert.throws(() => makePlan([], manifest, 3), /inventory/i);
  for (const count of [0, -1, 1.5, NaN, Infinity, 5, '3']) {
    assert.throws(() => makePlan(files, manifest, count), /shard/i);
  }
});

test('new modules use the conservative default; stale weights never define or drop inventory', () => {
  const changed = makePlan([...files, 'browser_new.mjs'], {...manifest,
    durations: [...manifest.durations, {file: 'browser_removed.mjs', ms: 900}]}, 3);
  const union = changed.partitions.flatMap(p => p.modules).sort();
  assert.deepEqual(union, [...files, 'browser_new.mjs'].sort());
  assert.equal(changed.partitions[0].estimatedMs, 100);
  assert.deepEqual(changed.partitions[0].modules, ['browser_new.mjs']);
  assert.notEqual(changed.inventoryId, makePlan(files, manifest, 3).inventoryId);
});

test('every supplied estimate, including unused stale entries, is positive and finite', () => {
  for (const ms of [0, -1, NaN, Infinity, '1', null]) {
    assert.throws(() => makePlan(files, {...manifest, defaultEstimateMs: ms}, 3), /estimate/i);
    assert.throws(() => makePlan(files, {...manifest,
      durations: [...manifest.durations, {file: 'browser_stale.mjs', ms}]}, 3), /estimate/i);
  }
  assert.throws(() => makePlan(files, {...manifest,
    durations: [...manifest.durations, manifest.durations[0]]}, 3), /duplicate/i);
});

test('strict shard arguments preserve OR substring filters only without sharding', () => {
  assert.deepEqual(parseRunnerArgs(['student', 'mentor']).filters, ['student', 'mentor']);
  assert.deepEqual(parseRunnerArgs(['--list', '--shard', '2/3']), {
    filters: [], list: true, shard: 2, shardCount: 3, sharded: true, report: undefined
  });
  assert.equal(parseRunnerArgs(['--shard=1/3']).shard, 1);
  for (const args of [
    ['--shard'], ['--shard', '0/3'], ['--shard', '4/3'], ['--shard', '1/0'],
    ['--shard', '01/3'], ['--shard', '1.0/3'], ['--shard', '1/3x'],
    ['--shard', '1/3', '--shard', '2/3'], ['--shard', '1/3', 'student'],
    ['--unknown'], ['--report'], ['--list', '--list']
  ]) assert.throws(() => parseRunnerArgs(args), /argument|shard|filter|report|list/i);
});

test('fleet accepts only the complete successful union rebuilt from actual inventory', () => {
  const plan = makePlan(files, manifest, 3);
  assert.equal(verify(reportsFor(plan)).moduleCount, files.length);
  const actualNewModule = makePlan([...files, 'browser_new.mjs'], manifest, 3);
  assert.throws(() => verify(reportsFor(plan), actualNewModule));
});

for (const result of ['failure', 'cancelled', 'skipped', '', undefined]) {
  test('fleet rejects unsuccessful or absent matrix result: ' + result, () => {
    const plan = makePlan(files, manifest, 3);
    assert.throws(() => validateFleet({reports: reportsFor(plan), plan, identity,
      jobResult: result, downloadResult: 'success'}), /job/i);
  });
}

const corruptions = {
  'missing artifact': reports => reports.pop(),
  'duplicate artifact': reports => {reports[2] = structuredClone(reports[0]);},
  'extra artifact': reports => reports.push(structuredClone(reports[0])),
  'wrong schema': reports => {reports[0].schemaVersion = 2;},
  'wrong shard count': reports => {reports[0].shardCount = 2;},
  'invalid shard': reports => {reports[0].shard = 0;},
  'wrong inventory': reports => {reports[0].inventoryId = 'old';},
  'wrong plan': reports => {reports[0].planId = 'old';},
  'different commit': reports => {reports[0].commit = 'old';},
  'different run': reports => {reports[0].runId = 'old';},
  'different attempt': reports => {reports[0].runAttempt = 'old';},
  'failed report': reports => {reports[0].status = 'FAIL';},
  'zero partition': reports => {reports[0].plannedModules = []; reports[0].executedModules = [];},
  'unexpected planned module': reports => reports[0].plannedModules.push('browser_extra.mjs'),
  'duplicate planned module': reports => reports[0].plannedModules.push(reports[0].plannedModules[0]),
  'missing execution': reports => reports[0].executedModules.pop(),
  'unexpected execution': reports => {reports[0].executedModules[0].module = 'browser_extra.mjs';},
  'duplicate execution': reports => reports[0].executedModules.push(reports[0].executedModules[0]),
  'failed module': reports => {reports[0].executedModules[0].status = 'FAIL';},
  'missing timing': reports => {delete reports[0].executedModules[0].elapsedMs;},
  'negative timing': reports => {reports[0].executedModules[0].elapsedMs = -1;},
  'infinite timing': reports => {reports[0].executedModules[0].elapsedMs = Infinity;}
};
for (const [description, corrupt] of Object.entries(corruptions)) {
  test('fleet fails closed for ' + description, () => {
    const reports = reportsFor(makePlan(files, manifest, 3));
    corrupt(reports);
    assert.throws(() => verify(reports));
  });
}

test('fleet rejects unsuccessful artifact download even with complete reports', () => {
  const plan = makePlan(files, manifest, 3);
  assert.throws(() => validateFleet({reports: reportsFor(plan), plan, identity,
    jobResult: 'success', downloadResult: 'failure'}), /download/i);
});

test('checked-in estimates cover all baseline modules and document their estimated source', async () => {
  const checkedIn = JSON.parse(await readFile(new URL('./browser_duration_estimates.json', import.meta.url)));
  assert.match(checkedIn.kind, /estimated/i);
  assert.match(checkedIn.sourceRun, /39186456508$/);
  assert.match(checkedIn.sourceCommit, /^[a-f0-9]{40}$/);
  assert.ok(checkedIn.sourcePaths.length);
  const {readdir} = await import('node:fs/promises');
  const inventory = discoverModules(await readdir(new URL('./', import.meta.url)));
  const plan = makePlan(inventory, checkedIn, 3);
  assert.equal(plan.partitions.flatMap(p => p.modules).length, inventory.length);
  assert.ok(plan.partitions.every(p => p.modules.length > 0));
});

test('aggregate CLI validates actual inventory and rejects missing, unexpected, duplicate or malformed artifacts', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'browser-fleet-contract-'));
  t.after(() => rm(directory, {recursive: true, force: true}));
  const invoke = args => spawnSync(process.execPath,
    ['tests/aggregate_browser_checks.mjs', '--artifacts', directory, '--shard-count', '3', ...args],
    {cwd: new URL('../', import.meta.url), encoding: 'utf8', timeout: 5000,
      env: {...process.env, BROWSER_SHARD_RESULT: 'success', BROWSER_ARTIFACT_DOWNLOAD_RESULT: 'success'}});
  assert.notEqual(invoke([]).status, 0);
  const actualManifest = JSON.parse(await readFile(new URL('./browser_duration_estimates.json', import.meta.url)));
  const actualFiles = discoverModules(await readdir(new URL('./', import.meta.url)));
  const plan = makePlan(actualFiles, actualManifest, 3);
  const currentIdentity = executionIdentity(process.env);
  const paths = [];
  for (const report of reportsFor(plan)) {
    Object.assign(report, currentIdentity);
    const folder = join(directory, `browser-shard-${report.shard}-attempt-${currentIdentity.runAttempt}`);
    await mkdir(folder);
    const path = join(folder, `shard-${report.shard}-of-3.json`);
    paths.push(path);
    await writeFile(path, JSON.stringify(report));
  }
  const accepted = invoke([]);
  assert.equal(accepted.status, 0, accepted.stderr);
  assert.match(accepted.stdout, new RegExp(`Browser fleet PASS: ${actualFiles.length} modules across 3 shards`));
  await mkdir(join(directory, 'browser-shard-duplicate'));
  assert.notEqual(invoke([]).status, 0);
  await rm(join(directory, 'browser-shard-duplicate'), {recursive: true});
  await writeFile(join(directory, 'extra.json'), '{}');
  assert.notEqual(invoke([]).status, 0);
  await rm(join(directory, 'extra.json'));
  await writeFile(paths[0], '{');
  assert.notEqual(invoke([]).status, 0);
  assert.notEqual(invoke(['--unknown']).status, 0);
});

test('GitHub execution identity fails closed on absent or malformed configuration', () => {
  for (const env of [{GITHUB_ACTIONS: 'true'}, {GITHUB_ACTIONS: 'true', GITHUB_SHA: 'old',
    GITHUB_RUN_ID: '42', GITHUB_RUN_ATTEMPT: '1'}]) assert.throws(() => executionIdentity(env), /identity/i);
});
