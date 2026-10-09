import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';

export default async function check() {
  const process = spawn('uv', ['run', '--frozen', 'python', 'tests/check_student_live.py', '--browser'], {
    stdio: ['ignore', 'pipe', 'pipe'],
    env: globalThis.process.env
  });
  let output = '';
  process.stdout.on('data', chunk => { output += chunk; });
  process.stderr.on('data', chunk => { output += chunk; });
  const code = await new Promise((resolve, reject) => {
    process.on('error', reject);
    process.on('close', resolve);
  });
  assert.equal(code, 0, output);
  assert(output.includes('PASS: real administrator/teacher browser HTTP'), output);
  assert(!output.includes('sk-live-db-key') && !output.includes('BROWSER-PASSWORD-SENTINEL'), 'no secrets in app/browser logs');
  return {pageErrors: []};
}
