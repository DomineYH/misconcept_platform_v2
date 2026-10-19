// Pure discovery, balanced planning and evidence validation. No browser dependency.
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';

const lexical = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const isModule = name => typeof name === 'string' && /^browser_[^/\\\r\n]+\.mjs$/.test(name);
const positive = value => typeof value === 'number' && Number.isFinite(value) && value > 0;

export function discoverModules(entries) {
  return entries.filter(name => /^browser_.*\.mjs$/.test(name)).sort(lexical);
}

export function parseRunnerArgs(args) {
  const options = {filters: [], list: false, shard: 1, shardCount: 1, sharded: false, report: undefined};
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--list') {
      assert(!options.list, 'Duplicate --list argument');
      options.list = true;
    } else if (arg === '--shard' || arg.startsWith('--shard=')) {
      assert(!options.sharded, 'Duplicate --shard argument');
      const value = arg === '--shard' ? args[++i] : arg.slice('--shard='.length);
      assert(typeof value === 'string' && /^[1-9]\d*\/[1-9]\d*$/.test(value), 'Invalid --shard argument; use N/COUNT');
      [options.shard, options.shardCount] = value.split('/').map(Number);
      assert(Number.isSafeInteger(options.shard) && Number.isSafeInteger(options.shardCount) &&
        options.shard <= options.shardCount, 'Invalid shard range');
      options.sharded = true;
    } else if (arg === '--report') {
      assert(options.report === undefined, 'Duplicate --report argument');
      options.report = args[++i];
      assert(options.report && !options.report.startsWith('--'), 'Missing --report path');
    } else {
      assert(!arg.startsWith('--'), `Unknown argument: ${arg}`);
      options.filters.push(arg);
    }
  }
  assert(!options.sharded || options.filters.length === 0, 'Substring filters cannot be combined with --shard (coverage must be complete)');
  return options;
}

export function makePlan(files, manifest, shardCount) {
  const inventory = [...files].sort(lexical);
  assert(inventory.length > 0 && inventory.every(isModule), 'Invalid or empty browser inventory');
  assert(new Set(inventory).size === inventory.length, 'Duplicate module in inventory');
  assert(Number.isSafeInteger(shardCount) && shardCount > 0 && shardCount <= inventory.length,
    'Invalid shard count; every shard must contain at least one module');
  assert(positive(manifest.defaultEstimateMs), 'Invalid default estimate');
  assert(Array.isArray(manifest.durations), 'Invalid duration estimates');
  const weights = new Map();
  for (const {file, ms} of manifest.durations) {
    assert(isModule(file) && positive(ms), 'Invalid module estimate');
    assert(!weights.has(file), `Duplicate module estimate: ${file}`);
    weights.set(file, ms);
  }
  const estimate = file => weights.get(file) ?? manifest.defaultEstimateMs;
  const partitions = Array.from({length: shardCount}, () => ({modules: [], estimatedMs: 0}));
  for (const file of [...inventory].sort((a, b) => estimate(b) - estimate(a) || lexical(a, b))) {
    const target = partitions.reduce((best, partition) => partition.estimatedMs < best.estimatedMs ? partition : best);
    target.modules.push(file);
    target.estimatedMs += estimate(file);
    assert(Number.isFinite(target.estimatedMs), 'Total estimate overflow');
  }
  for (const partition of partitions) {
    partition.modules.sort(lexical);
    assert(partition.modules.length > 0, 'Empty shard partition');
  }
  const inventoryId = digest(inventory);
  return {inventory, inventoryId, planId: digest({inventoryId, partitions}), partitions};
}

export function executionIdentity(env) {
  if (env.GITHUB_ACTIONS === 'true') {
    assert(/^[a-f0-9]{40}$/.test(env.GITHUB_SHA) && /^[1-9]\d*$/.test(env.GITHUB_RUN_ID) &&
      /^[1-9]\d*$/.test(env.GITHUB_RUN_ATTEMPT), 'Missing or invalid GitHub execution identity');
  }
  return {commit: env.GITHUB_SHA ?? 'local', runId: env.GITHUB_RUN_ID ?? 'local',
    runAttempt: env.GITHUB_RUN_ATTEMPT ?? 'local'};
}

export function validateFleet({reports, plan, identity, jobResult, downloadResult}) {
  assert(jobResult === 'success', `Browser shard job result is not success: ${jobResult}`);
  assert(downloadResult === 'success', `Artifact download is not success: ${downloadResult}`);
  assert(reports.length === plan.partitions.length, 'Missing or unexpected shard artifacts');
  const seenShards = new Set(), seenModules = new Set();
  for (const report of reports) {
    assert.equal(report.schemaVersion, 1, 'Invalid evidence schema');
    assert(Number.isSafeInteger(report.shard) && report.shard > 0 && report.shard <= plan.partitions.length,
      'Invalid evidence shard');
    assert(!seenShards.has(report.shard), 'Duplicate shard artifact');
    seenShards.add(report.shard);
    assert.equal(report.shardCount, plan.partitions.length, 'Incorrect shard count');
    for (const field of ['commit', 'runId', 'runAttempt']) assert.equal(report[field], identity[field], `Incorrect ${field}`);
    assert.equal(report.inventoryId, plan.inventoryId, 'Incorrect inventory identity');
    assert.equal(report.planId, plan.planId, 'Incorrect partition plan');
    assert.equal(report.status, 'PASS', 'Failed shard report');
    const expected = plan.partitions[report.shard - 1].modules;
    assert.deepEqual(report.plannedModules, expected, 'Planned modules do not match actual directory inventory partition');
    assert(Array.isArray(report.executedModules), 'Missing executed modules');
    assert.equal(report.executedModules.length, expected.length, 'Missing or unexpected executed module');
    for (const [index, execution] of report.executedModules.entries()) {
      assert.equal(execution.module, expected[index], 'Unexpected or duplicate executed module');
      assert(!seenModules.has(execution.module), 'Duplicate executed module across shards');
      seenModules.add(execution.module);
      assert.equal(execution.status, 'PASS', 'Failed module');
      assert(typeof execution.elapsedMs === 'number' && Number.isFinite(execution.elapsedMs) && execution.elapsedMs >= 0,
        'Invalid module timing');
    }
  }
  assert.deepEqual([...seenModules].sort(lexical), plan.inventory, 'Incomplete fleet union against actual directory inventory');
  return {moduleCount: seenModules.size, shardCount: seenShards.size, inventoryId: plan.inventoryId};
}
