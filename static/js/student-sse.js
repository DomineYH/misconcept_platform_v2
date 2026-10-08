// Read lines before interpreting frames: CR and LF may arrive in different chunks.
export async function readSSE(stream, onEvent) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = '', event = '', data = [];
  let stopped = false;

  function line(value) {
    if (!value) {
      if (data.length) stopped = onEvent(event || 'message', JSON.parse(data.join('\n'))) === false;
      event = '';
      data = [];
      return;
    }
    if (value.startsWith(':')) return;
    const colon = value.indexOf(':');
    const field = colon < 0 ? value : value.slice(0, colon);
    let content = colon < 0 ? '' : value.slice(colon + 1);
    if (content.startsWith(' ')) content = content.slice(1);
    if (field === 'event') event = content;
    if (field === 'data') data.push(content);
  }

  function drain(eof = false) {
    let start = 0;
    for (let i = 0; i < buffer.length; i++) {
      if (buffer[i] !== '\r' && buffer[i] !== '\n') continue;
      if (buffer[i] === '\r' && i === buffer.length - 1 && !eof) break;
      line(buffer.slice(start, i));
      if (stopped) return;
      if (buffer[i] === '\r' && buffer[i + 1] === '\n') i++;
      start = i + 1;
    }
    buffer = buffer.slice(start);
    // An unterminated frame at EOF is not an event.
  }

  try {
    while (true) {
      const {value, done} = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, {stream:true});
      drain(done);
      if (done || stopped) break;
    }
  } finally {
    try {
      await reader.cancel();
    } finally {
      reader.releaseLock();
    }
  }
}
