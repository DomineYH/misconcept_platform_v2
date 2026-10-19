import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {copyFile, mkdir, mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {test} from 'node:test';
import {runInNewContext} from 'node:vm';

const runner = new URL('./run_browser_checks.mjs', import.meta.url);
const workflow = await readFile(new URL('../.github/workflows/tests.yml', import.meta.url), 'utf8');

test('workflow keeps all PR events, limits pushes to main and preserves job names', () => {
  assert.match(workflow, /^on:\n  push:\n    branches: \[main\]\n  pull_request:\n(?=\S)/m);
  assert.deepEqual([...workflow.matchAll(/^  (\w+):\n    runs-on:/gm)].map(match => match[1]), ['test', 'browser']);
  assert.match(workflow, /run: uv run --frozen python -m pytest -q --durations=30$/m);
  assert.match(workflow, /run: npm run test:browser$/m);
  assert.match(workflow, /run: uv run --frozen python tests\/check_student_live.py$/m);
  assert.match(workflow.split('\n  browser:')[0], /uses: actions\/setup-node@v4\n        with:\n          node-version: 24\n/);
});

test('only superseded runs for the same PR share a cancelable concurrency group', () => {
  const group = workflow.match(/^  group: (.+)$/m)?.[1];
  const cancel = workflow.match(/^  cancel-in-progress: \$\{\{ (.+) \}\}$/m)?.[1];
  assert.ok(group);
  assert.ok(cancel);
  const evaluate = (template, github) => template.replace(/\$\{\{ (.+?) \}\}/g,
    (_, expression) => String(runInNewContext(expression, {github})));
  const event = (event_name, number, run_id) => ({workflow: 'Regression tests', event_name,
    event: {pull_request: number === null ? undefined : {number}}, run_id});
  const firstPR = event('pull_request', 42, 100);
  const nextPR = event('pull_request', 42, 101);
  const otherPR = event('pull_request', 43, 102);
  const firstMain = event('push', null, 42);
  const nextMain = event('push', null, 101);
  assert.equal(evaluate(group, firstPR), evaluate(group, nextPR));
  assert.notEqual(evaluate(group, firstPR), evaluate(group, otherPR));
  assert.notEqual(evaluate(group, firstPR), evaluate(group, firstMain));
  assert.notEqual(evaluate(group, firstMain), evaluate(group, nextMain));
  for (const github of [firstPR, nextPR, otherPR]) {
    assert.equal(runInNewContext(cancel, {github}), true);
  }
  for (const github of [firstMain, nextMain]) {
    assert.equal(runInNewContext(cancel, {github}), false);
  }
});

// Import the real runner in a fresh process. Only external boundaries are mocked:
// discovery, imports, assertions, logging and failure handling stay executable.
const bootstrap = `
import {EventEmitter} from 'node:events';
import {registerHooks} from 'node:module';
import {pathToFileURL} from 'node:url';
const mode = process.env.CI_RUNNER_TEST_MODE;
const trace = [];
globalThis.__runnerTrace = trace;
let clock = 100;
const server = new EventEmitter();
server.stderr = new EventEmitter();
server.exitCode = null;
server.signalCode = null;
const browser = {
  async newContext() {
    trace.push('context');
    if (mode === 'context') throw new Error('context failed');
    return {
      async newPage() {
        if (mode === 'page') throw new Error('page failed');
        const page = new EventEmitter();
        page.goto = async () => {
          trace.push('goto');
          if (mode === 'goto') throw new Error('goto failed');
        };
        return page;
      },
      async close() {
        trace.push('context-close');
        if (mode === 'cleanup') throw new Error('cleanup failed');
      }
    };
  },
  async close() {
    trace.push('browser-close');
    server.exitCode = 0;
    server.emit('close', 0);
  }
};
globalThis.__runnerMocks = {
  spawn(command, args) {trace.push([command, args]); return server;},
  createServer() {
    const listener = new EventEmitter();
    listener.listen = (_port, _host, callback) => callback();
    listener.address = () => ({port: 12345});
    listener.close = callback => callback();
    return listener;
  },
  performance: {now() {const value = clock; clock += 25; return value;}},
  delay: async () => {},
  chromium: {async launch() {return browser;}}
};
globalThis.fetch = async () => ({ok: true, async text() {return '';}});
const mocks = {
  'node:child_process': 'export const {spawn} = globalThis.__runnerMocks;',
  'node:net': 'export const {createServer} = globalThis.__runnerMocks;',
  'node:perf_hooks': 'export const {performance} = globalThis.__runnerMocks;',
  'node:timers/promises': 'export const {delay: setTimeout} = globalThis.__runnerMocks;',
  'playwright': 'export const {chromium} = globalThis.__runnerMocks;'
};
registerHooks({
  resolve(specifier, context, nextResolve) {
    return specifier in mocks ? {url: 'mock:' + specifier, shortCircuit: true}
      : nextResolve(specifier, context);
  },
  load(url, context, nextLoad) {
    return url.startsWith('mock:') ? {format: 'module', source: mocks[url.slice(5)], shortCircuit: true}
      : nextLoad(url, context);
  }
});
process.argv = [process.argv[0], process.argv[2], ...process.argv.slice(3)];
try {await import(pathToFileURL(process.argv[1]));}
finally {console.log('TRACE ' + JSON.stringify(trace));}
`;

async function runChecks(t, {mode = '', files, filters = []}) {
  const directory = await mkdtemp(join(tmpdir(), 'ci-runner-contract-'));
  t.after(() => rm(directory, {recursive: true, force: true}));
  const tests = join(directory, 'tests');
  await mkdir(tests);
  await copyFile(runner, join(tests, 'run_browser_checks.mjs'));
  for (const [name, source] of Object.entries(files)) await writeFile(join(tests, name), source);
  const entry = join(directory, 'bootstrap.mjs');
  await writeFile(entry, bootstrap);
  const result = spawnSync(process.execPath, [entry, join(tests, 'run_browser_checks.mjs'), ...filters], {
    encoding: 'utf8', timeout: 5000, env: {...process.env, CI_RUNNER_TEST_MODE: mode}
  });
  assert.ifError(result.error);
  result.trace = JSON.parse(result.stdout.match(/^TRACE (.+)$/m)?.[1] ?? 'null');
  return result;
}

const passing = `export default async () => ({});`;
const record = name => `export default async () => {globalThis.__runnerTrace.push('${name}'); return {};};`;

test('runner preserves sorted discovery and filters and logs monotonic elapsed time', async t => {
  const result = await runChecks(t, {files: {
    'browser_z.mjs': record('z'), 'browser_a.mjs': record('a'),
    'browser_skip.mjs': record('skip'), 'check_a.mjs': record('not-a-browser-check')
  }, filters: ['_z', '_a']});
  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(result.stdout.split('\n').slice(0, 6), [
    'START browser_a.mjs', 'PASS browser_a.mjs', 'END browser_a.mjs status=PASS elapsed_ms=25.0',
    'START browser_z.mjs', 'PASS browser_z.mjs', 'END browser_z.mjs status=PASS elapsed_ms=25.0'
  ]);
  assert.deepEqual(result.trace, [
    ['uv', ['run', '--frozen', 'python', 'tests/browser_server.py']],
    'context', 'goto', 'a', 'context-close', 'context', 'goto', 'z', 'context-close', 'browser-close'
  ]);
});

for (const [failure, source] of [
  ['check', `export default async () => {throw new Error('check\\nfailed');};`],
  ['import', `throw new Error('import failed');`],
  ['returned page errors', `export default async () => ({pageErrors: ['returned failure']});`],
  ['uncaught page errors', `export default async page => {page.emit('pageerror', new Error('page failure')); return {};};`]
]) {
  test('runner logs failure timing and continues after ' + failure, async t => {
    const result = await runChecks(t, {files: {'browser_a.mjs': source, 'browser_b.mjs': passing}});
    assert.equal(result.status, 1);
    assert.match(result.stderr, /^FAIL browser_a\.mjs: .+$/m);
    assert.match(result.stdout, /END browser_a\.mjs status=FAIL elapsed_ms=25\.0/);
    assert.match(result.stdout, /START browser_b\.mjs\nPASS browser_b\.mjs\nEND browser_b\.mjs status=PASS elapsed_ms=25\.0/);
    assert.equal(result.trace.filter(item => item === 'context-close').length, 2);
    assert.equal(result.trace.at(-1), 'browser-close');
  });
}

for (const mode of ['context', 'page', 'goto', 'cleanup']) {
  test('runner logs failure timing for ' + mode + ' errors with original abort behavior', async t => {
    const result = await runChecks(t, {mode, files: {'browser_a.mjs': passing, 'browser_b.mjs': passing}});
    assert.equal(result.status, 1);
    assert.match(result.stdout, /START browser_a\.mjs/);
    assert.match(result.stdout, /END browser_a\.mjs status=FAIL elapsed_ms=25\.0/);
    assert.equal(result.stdout.includes('START browser_b.mjs'), mode !== 'cleanup');
    assert.equal(result.trace.at(-1), 'browser-close');
    if (mode === 'cleanup') assert.match(result.stderr, /cleanup failed/);
    else assert.match(result.stderr, /^FAIL browser_a\.mjs: /m);
  });
}

test('unmatched filters still fail before server startup', async t => {
  const result = await runChecks(t, {files: {'browser_a.mjs': passing}, filters: ['absent']});
  assert.equal(result.status, 1);
  assert.match(result.stderr, /No browser checks matched/);
  assert.doesNotMatch(result.stdout, /START|END/);
  assert.deepEqual(result.trace, []);
});
