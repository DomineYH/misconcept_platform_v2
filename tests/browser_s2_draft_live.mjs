import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';

export default async function check() {
  const rehearsal = spawn('uv', ['run', '--frozen', 'python', 'tests/check_s2_draft_browser.py'], {
    stdio:['ignore', 'pipe', 'pipe'], env:process.env
  });
  let output = '';
  rehearsal.stdout.on('data', chunk => { output += chunk; });
  rehearsal.stderr.on('data', chunk => { output += chunk; });
  const code = await new Promise((resolve, reject) => {
    rehearsal.on('error', reject);
    rehearsal.on('close', resolve);
  });
  assert.equal(code, 0, output);
  assert(output.includes('PASS: real draft form/HTTP/CSRF/SQLite/reopen'), output);
  return {pageErrors:[]};
}
