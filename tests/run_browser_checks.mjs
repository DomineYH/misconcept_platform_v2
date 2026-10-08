import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {readdir} from 'node:fs/promises';
import {createServer} from 'node:net';
import {setTimeout as delay} from 'node:timers/promises';
import {fileURLToPath} from 'node:url';
import {chromium} from 'playwright';

const root = fileURLToPath(new URL('../', import.meta.url));
const tests = new URL('./', import.meta.url);
const filters = process.argv.slice(2);
const files = (await readdir(tests)).filter(name =>
  /^browser_.*\.mjs$/.test(name) &&
  (filters.length === 0 || filters.some(filter => name.includes(filter)))
).sort();
assert(files.length > 0, 'No browser checks matched');

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
    let page;
    try {
      page = await browser.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(url);
      const {default: check} = await import(new URL(file, tests));
      const result = await check(page);
      assert(!result?.pageErrors?.length, `pageErrors: ${JSON.stringify(result.pageErrors)}`);
      assert(errors.length === 0, `Uncaught page errors: ${errors.join('; ')}`);
      console.log(`PASS ${file}`);
    } catch (error) {
      console.error(`FAIL ${file}: ${String(error.message ?? error).replace(/\s+/g, ' ')}`);
      process.exitCode = 1;
    } finally {
      await page?.close();
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
