import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdir, readFile, readdir, writeFile} from 'node:fs/promises';
import {dirname, join} from 'node:path';
import {createServer} from 'node:net';
import {performance} from 'node:perf_hooks';
import {setTimeout as delay} from 'node:timers/promises';
import {fileURLToPath} from 'node:url';
import {discoverModules, executionIdentity, makePlan, parseRunnerArgs} from './ci_browser_sharding.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const tests = new URL('./', import.meta.url);
const options = parseRunnerArgs(process.argv.slice(2));
const manifest = JSON.parse(await readFile(new URL('./browser_duration_estimates.json', tests), 'utf8'));
const inventory = discoverModules(await readdir(tests));
const plan = makePlan(inventory, manifest, options.shardCount);
const files = plan.partitions[options.shard - 1].modules.filter(name =>
  options.filters.length === 0 || options.filters.some(filter => name.includes(filter))
);
assert(files.length > 0, 'No browser checks matched');
const evidence = {
  schemaVersion: 1, ...executionIdentity(process.env), inventoryId: plan.inventoryId, planId: plan.planId,
  shard: options.shard, shardCount: options.shardCount, status: 'FAIL',
  plannedModules: files, executedModules: []
};

if (options.list) {
  console.log(JSON.stringify({...evidence, status: 'NOT_RUN', estimatedMs: options.filters.length ? undefined : plan.partitions[options.shard - 1].estimatedMs}, null, 2));
} else {
  const report = options.report ?? (options.sharded ? join(root, 'browser-results', `shard-${options.shard}-of-${options.shardCount}.json`) : undefined);
  try {
    await runChecks();
    evidence.status = process.exitCode ? 'FAIL' : 'PASS';
  } finally {
    if (report) {
      await mkdir(dirname(report), {recursive: true});
      await writeFile(report, JSON.stringify(evidence, null, 2) + '\n');
    }
  }
}

async function runChecks() {
  // --list never imports Playwright, starts a server, or opens a browser.
  const {chromium} = await import('playwright');
  const listener = createServer();
  await new Promise((resolve, reject) => {
    listener.once('error', reject);
    listener.listen(0, '127.0.0.1', resolve);
  });
  const port = listener.address().port;
  await new Promise((resolve, reject) => listener.close(error => error ? reject(error) : resolve()));

  const server = spawn('uv', ['run', '--frozen', 'python', 'tests/browser_server.py'], {
    cwd: root,
    env: {...process.env, BROWSER_TEST_PORT: String(port)},
    detached: process.platform !== 'win32',
    stdio: ['ignore', 'ignore', 'pipe']
  });
  let serverError, serverLog = '', browser;
  server.on('error', error => { serverError = error; });
  server.stderr.on('data', chunk => { serverLog = (serverLog + chunk).slice(-8000); });
  const stopped = new Promise(resolve => server.once('close', resolve));

  try {
    const url = `http://127.0.0.1:${port}/chat`;
    let ready = false;
    const deadline = Date.now() + 30000;
    while (Date.now() < deadline) {
      if (serverError) throw serverError;
      assert(server.exitCode === null && server.signalCode === null, `Browser server exited: ${serverLog}`);
      try {
        const response = await fetch(url, {signal: AbortSignal.timeout(1000)});
        await response.text();
        if (response.ok) { ready = true; break; }
      } catch {}
      await delay(100);
    }
    assert(ready, `Browser server did not serve /chat within 30s: ${serverLog}`);
    browser = await chromium.launch({headless: true});
    for (const file of files) {
      const startedAt = performance.now();
      let status = 'FAIL';
      console.log(`START ${file}`);
      let context, page;
      try {
        context = await browser.newContext();
        page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(url);
        const {default: check} = await import(new URL(file, tests));
        const result = await check(page);
        assert(!result?.pageErrors?.length, `pageErrors: ${JSON.stringify(result.pageErrors)}`);
        assert(errors.length === 0, `Uncaught page errors: ${errors.join('; ')}`);
        status = 'PASS';
        console.log(`PASS ${file}`);
      } catch (error) {
        console.error(`FAIL ${file}: ${String(error.message ?? error).replace(/\s+/g, ' ')}`);
        process.exitCode = 1;
      } finally {
        try {
          await context?.close();
        } catch (error) {
          status = 'FAIL';
          throw error;
        } finally {
          const elapsedMs = performance.now() - startedAt;
          evidence.executedModules.push({module: file, status, elapsedMs});
          console.log(`END ${file} status=${status} elapsed_ms=${elapsedMs.toFixed(1)}`);
        }
      }
    }
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  } finally {
    try {
      await browser?.close();
    } finally {
      if (server.pid && server.exitCode === null && server.signalCode === null) {
        const kill = signal => process.platform === 'win32'
          ? server.kill(signal) : process.kill(-server.pid, signal);
        kill('SIGTERM');
        const exited = await Promise.race([stopped.then(() => true), delay(5000).then(() => false)]);
        if (!exited) kill('SIGKILL');
      }
      await stopped;
    }
  }
}
