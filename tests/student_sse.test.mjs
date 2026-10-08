import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {test} from 'node:test';

const source = await readFile(new URL('../static/js/student-sse.js', import.meta.url), 'utf8');
const {readSSE} = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('SSE preserves split UTF-8, CRLF, multiline data and ignores heartbeat', async () => {
  const bytes = new TextEncoder().encode(
    ': ping\r\n\r\nevent: output.delta\r\ndata: {"text":\r\ndata: "학생🙂"}\r\n\r\n' +
    'event: output.completed\ndata: {"status":"completed"}\n\n'
  );
  // Every byte boundary includes multibyte characters and the CR/LF boundary.
  const stream = new ReadableStream({start(controller) {
    for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
    controller.close();
  }});
  const events = [];
  await readSSE(stream, (type, data) => events.push([type, data]));
  assert.deepEqual(events, [
    ['output.delta', {text:'학생🙂'}],
    ['output.completed', {status:'completed'}]
  ]);
});

test('SSE does not deliver an unfinished frame at EOF', async () => {
  const stream = new ReadableStream({start(controller) {
    controller.enqueue(new TextEncoder().encode('event: output.completed\ndata: {"status":"completed"}\n'));
    controller.close();
  }});
  const events = [];
  await readSSE(stream, (type, data) => events.push([type, data]));
  assert.deepEqual(events, []);
});

test('SSE consumer can stop at a terminal event and release the reader', async () => {
  const stream = new ReadableStream({start(controller) {
    controller.enqueue(new TextEncoder().encode(
      'event: output.completed\ndata: {"status":"completed"}\n\n' +
      'event: run.failed\ndata: {"status":"failed"}\n\n'
    ));
    controller.close();
  }});
  const events = [];
  await readSSE(stream, (type, data) => {
    events.push([type, data]);
    return false;
  });
  assert.deepEqual(events, [['output.completed', {status:'completed'}]]);
  assert.equal(stream.locked, false);
});
