import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';

export default async function check() {
  const child = spawn('uv', ['run', '--frozen', 'python', 'tests/check_student_live.py', '--mentor-browser'], {
    stdio:['ignore', 'pipe', 'pipe'], env:process.env
  });
  let output = '';
  child.stdout.on('data', chunk => { output += chunk; });
  child.stderr.on('data', chunk => { output += chunk; });
  const code = await new Promise((resolve, reject) => {
    child.on('error', reject);
    child.on('close', resolve);
  });
  assert.equal(code, 0, output);
  assert(output.includes('PASS: real authenticated S3 mentor browser HTTP'), output);
  assert(!output.includes('sk-live-db-key') && !output.includes('BROWSER-PASSWORD-SENTINEL'));
  return {pageErrors:[]};
}
