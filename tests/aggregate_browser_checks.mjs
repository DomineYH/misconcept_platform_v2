import assert from 'node:assert/strict';
import {readFile, readdir} from 'node:fs/promises';
import {join} from 'node:path';
import {discoverModules, executionIdentity, makePlan, validateFleet} from './ci_browser_sharding.mjs';

const args = process.argv.slice(2), options = {};
for (let i = 0; i < args.length; i += 2) {
  const key = args[i], value = args[i + 1];
  assert(['--artifacts', '--shard-count'].includes(key) && !(key in options) && value && !value.startsWith('--'),
    'Expected --artifacts PATH --shard-count COUNT');
  options[key] = value;
}
assert(options['--artifacts'] && /^[1-9]\d*$/.test(options['--shard-count']), 'Missing or invalid aggregate arguments');
const shardCount = Number(options['--shard-count']);
const tests = new URL('./', import.meta.url);
const manifest = JSON.parse(await readFile(new URL('./browser_duration_estimates.json', tests), 'utf8'));
// Re-discover the directory checked out at this commit. Never trust a report's expected list.
const plan = makePlan(discoverModules(await readdir(tests)), manifest, shardCount);
const identity = executionIdentity(process.env);
const expectedArtifacts = plan.partitions.map((_, index) => `browser-shard-${index + 1}-attempt-${identity.runAttempt}`).sort();
const artifactNames = (await readdir(options['--artifacts'])).sort();
assert.deepEqual(artifactNames, expectedArtifacts, 'Missing, duplicate or unexpected artifacts');
const reports = await Promise.all(plan.partitions.map(async (_, index) => {
  const directory = join(options['--artifacts'], `browser-shard-${index + 1}-attempt-${identity.runAttempt}`);
  const filename = `shard-${index + 1}-of-${shardCount}.json`;
  assert.deepEqual(await readdir(directory), [filename], 'Missing or unexpected artifact files');
  const report = JSON.parse(await readFile(join(directory, filename), 'utf8'));
  assert.equal(report.shard, index + 1, 'Artifact name disagrees with shard');
  return report;
}));
const result = validateFleet({reports, plan, identity,
  jobResult: process.env.BROWSER_SHARD_RESULT, downloadResult: process.env.BROWSER_ARTIFACT_DOWNLOAD_RESULT});
console.log(`Browser fleet PASS: ${result.moduleCount} modules across ${result.shardCount} shards; inventory=${result.inventoryId}`);
