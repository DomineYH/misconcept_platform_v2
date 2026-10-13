import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
export default async function check() {
  const process = spawn('uv',['run','--frozen','python','tests/check_conversation_browser.py'], {stdio:['ignore','pipe','pipe'],env:globalThis.process.env});
  let output = '';
  process.stdout.on('data', chunk => { output += chunk; });
  process.stderr.on('data', chunk => { output += chunk; });
  const code = await new Promise((resolve,reject) => { process.on('error',reject); process.on('close',resolve); });
  assert.equal(code,0,output);
  assert(output.includes('PASS: saved conversation HTTP/SSE/browser/SQLite'),output);
  return {pageErrors:[]};
}
