import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';

export default async function check() {
  const run = spawn('uv',['run','--frozen','python','tests/check_s5_student_browser.py'],{stdio:['ignore','pipe','pipe'],env:process.env});
  let output = '';
  run.stdout.on('data',chunk => { output += chunk; });
  run.stderr.on('data',chunk => { output += chunk; });
  const code = await new Promise((resolve,reject) => { run.on('error',reject); run.on('close',resolve); });
  assert.equal(code,0,output);
  assert(output.includes('PASS: real student comparison HTTP/SSE/CSRF/SQLite/recovery'),output);
  assert(output.includes('PASS: real mentor comparison HTTP/SSE/CSRF/SQLite/final-only'),output);
  return {pageErrors:[]};
}
