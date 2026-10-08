# 조사: FastAPI fetch POST + SSE 스트리밍, 연결 종료 감지·상위 호출 취소

- 티켓: #13 (map #23, blocking #16) · 작성 2026-10-08
- 대상 버전(uv.lock): fastapi 0.121.0, starlette 0.49.3, uvicorn 0.38.0 (`[standard]` → httptools 0.7.1, uvloop 0.22.1), starlette-csrf 3.0.0, anyio 4.11.0, httpx 0.28.1, openai 2.7.1
- 근거 범례: **[S]** 설치된 패키지 소스(경로는 `site-packages/` 기준) · **[E]** 로컬 실험(같은 버전 scratch venv, 실제 uvicorn 서버 + httpx/Node 클라이언트, 커밋 안 함) · **[W]** 웹 1차 문서

## 결론 요약

1. **`StreamingResponse` + async generator로 충분하다.** 추가 의존성(sse-starlette)은 필요 없다. FastAPI 0.121에는 SSE 전용 응답 클래스가 없다 [S: `fastapi/` 전체에 `text/event-stream` 없음].
2. **`starlette_csrf`는 순수 ASGI 미들웨어라 스트림을 버퍼링하지 않는다.** 앱 전체에 `BaseHTTPMiddleware`도 없다. 프레임이 생성 즉시 도착하는 것을 실측했다 [S][E].
3. **연결 종료는 Starlette가 자동 감지해 generator에 `CancelledError`를 던진다.** uvicorn 0.38은 ASGI `spec_version` "2.3"을 광고하므로 Starlette가 `listen_for_disconnect` 태스크를 같이 돌린다. 따라서 `request.is_disconnected()` 폴링은 불필요하다 [S][E].
4. **취소 후 `finally` 안의 `await`(SDK `close()`, DB 상태 저장)는 다시 취소된다.** anyio cancel scope가 level-triggered이기 때문이다. 정리 코드는 반드시 `anyio.CancelScope(shield=True)`로 감싸야 한다 [W][E].
5. Heartbeat는 SSE 주석 프레임 `: ping\n\n`을 약 15초마다 보낸다. 파서가 무시하므로 TTFT·`seq`와 섞이지 않는다 [W].
6. 테스트용 `TestClient`와 `httpx.ASGITransport`는 **응답 전체를 버퍼링한다.** 유한한 fake 스트림의 내용 검증에만 쓰고, 종료·취소는 ASGI 앱을 직접 호출하는 방식(`receive`가 `http.disconnect` 주입)으로 검증한다 [S][E].

## 1. 응답 헤더·heartbeat

| 헤더 | 값 | 이유 |
|---|---|---|
| `Content-Type` | `text/event-stream` (Starlette가 `; charset=utf-8` 부가) | SSE MIME, UTF-8 필수 [W: WHATWG HTML §9.2] |
| `Cache-Control` | `no-store, no-transform` | PRD 9.2 캐시 금지. `no-transform`은 중간자의 변형(압축 등)을 금지한다 [W: RFC 9111 §5.2.2.6] |
| `X-Accel-Buffering` | `no` | nginx `proxy_buffering`을 응답별로 끈다 [W: nginx proxy module] |

- `Connection: keep-alive`는 넣지 않는다. HTTP/1.1 기본값이고 HTTP/2에서는 금지 헤더다. uvicorn이 `Transfer-Encoding: chunked`를 자동 설정한다 [E].
- 압축: 앱에 `GZipMiddleware`가 없다. 나중에 추가해도 Starlette GZip은 `DEFAULT_EXCLUDED_CONTENT_TYPES = ("text/event-stream",)`로 SSE를 제외한다 [S: `starlette/middleware/gzip.py:8`].
- Heartbeat: WHATWG가 "comment line … every 15 seconds or so"를 권고하고 legacy 프록시의 짧은 타임아웃을 경고한다 [W]. nginx `proxy_read_timeout` 기본값 60s는 "두 read 사이" 기준이라 15s ping이면 끊기지 않는다 [W].
- PRD "첫 본문 시간과 heartbeat 구분": ping은 `:` 주석이라 클라이언트 파서가 버린다. 서버의 TTFT는 첫 `output.delta`를 yield하는 시점에 측정한다.
- 운영 리버스 프록시 종류·설정은 저장소에 없다 → **UNCONFIRMED**(nginx가 아니면 해당 제품의 버퍼링·idle timeout을 별도로 점검해야 한다).

## 2. 연결 종료 감지와 SDK 스트림 취소 전파

**동작 경로 [S]**
- `starlette/responses.py` `StreamingResponse.__call__`: `spec_version >= (2,4)`이면 `send`에서 `OSError`가 날 때만 감지한다. 그 밖에는 task group에서 `stream_response`와 `listen_for_disconnect(receive)`를 병렬 실행하고, `http.disconnect`를 받으면 `task_group.cancel_scope.cancel()`한다.
- uvicorn 0.38 `h11_impl.py:203` / `httptools_impl.py:225`: `"spec_version": "2.3"` → **항상 task group 경로**를 탄다. `connection_lost` 시 `receive()`가 `{"type": "http.disconnect"}`를 반환하고, 이후 `send()`는 조용히 return한다 (`if self.disconnected: return`).
- 결과: generator가 `await` 중이면 그 자리에서 `CancelledError`가 발생한다. task group이 취소를 흡수하므로 endpoint 쪽에는 예외가 전파되지 않는다.
- 중간 미들웨어(Session, CSRF, Logging, SecurityHeaders)는 모두 `receive`를 그대로 넘기므로 disconnect 전달을 막지 않는다 [S: `src/main.py`].

**실측 [E]** (h11·httptools 모두 동일)
- 클라이언트 close 후 약 1ms 안에 `gen.cancelled → upstream.close → finally`가 실행됐다. 느린 upstream(await 중)과 클라이언트 정지로 인한 backpressure(write paused) 두 경우 모두 generator 안에서 `CancelledError`가 났다.
- `request.is_disconnected()` 폴링 루프는 한 번도 True를 보지 못했다. 그보다 먼저 generator가 취소되기 때문이며, 그래서 불필요하다.
- `finally`에서 shield 없이 `await asyncio.sleep(0.1)`을 하면 `CancelledError`로 중단됐다. `with anyio.CancelScope(shield=True):` 안에서는 완료됐다. anyio 문서도 "performing shutdown procedures on asynchronous resources"에 shield를 쓰라고 한다 [W: anyio cancellation].
- 주의: `await`가 전혀 없는 generator는 disconnect 후 `send()`가 즉시 return하므로 이벤트 루프를 점유한 채 무한 루프에 빠진다. 루프마다 반드시 `await`가 있어야 한다(아래 패턴은 `asyncio.wait`가 항상 있다).

**SDK 취소**: openai 2.7.1 `AsyncStream.close()`는 `await self.response.aclose()`로 HTTP 연결을 해제한다 [S: `openai/_streaming.py:214`]. 진행 중인 `anext()` 태스크를 cancel한 뒤 shield 안에서 `close()`를 호출하면 상위 요청이 끊겨 토큰 생성·과금이 멈춘다. 제공자 측에서 즉시 생성이 중단되는지는 **UNCONFIRMED**(PRD 9.3: 정확히 한 번 과금은 보장하지 않음).

**anyio 제약**: generator 안에서 `yield`를 cancel scope나 task group **안에** 두면 cancel scope 스택이 손상된다 [W: anyio cancellation]. 그래서 heartbeat는 `move_on_after`로 yield를 감싸지 않고, 아래처럼 `asyncio.wait(timeout=)`으로 구현한다.

**`cancelled` vs `interrupted`**: 서버는 disconnect만 보고 사용자 취소와 네트워크 단절을 구분할 수 없다. 기본값은 `interrupted`로 둔다. 사용자 취소는 별도 `POST /runs/{id}/cancel`(또는 그에 준하는 신호)로 플래그를 세운 뒤 `cancelled`로 기록하는 것을 제안한다(설계 판단이며 #16에서 확정).

**DB 세션 수명 [S]**: FastAPI 0.121 `routing.py:101-115`는 기본(`scope="request"`) yield 의존성을 `await response(...)` **이후**, 즉 스트림이 끝난 뒤 정리한다. 따라서 `get_db_session`을 스트리밍 endpoint에서 쓰면 생성 내내 커넥션을 점유하고, commit도 스트림이 끝난 뒤에야 일어난다. `run.accepted` 전 실행 시작 기록과 종료 기록은 generator 안에서 `AsyncSessionLocal()`로 짧은 세션을 열어 각각 commit할 것을 권장한다.

## 3. `starlette_csrf`와 스트리밍

- `starlette_csrf/middleware.py`의 `CSRFMiddleware`는 `__call__(scope, receive, send)` 순수 ASGI 구현이다 [S]. 검증은 헤더(`x-csrf-token`)와 쿠키만 보고 **body를 읽지 않으므로** `receive`를 소비하지 않는다. `send`는 `functools.partial`로 감싸 쿠키가 없을 때만 `set-cookie`를 덧붙이고 메시지를 그대로 전달한다. 즉 버퍼링하지 않는다.
- 실측: 토큰 없이 POST하면 403, 토큰을 넣으면 200이고 프레임이 0.3s 간격으로 그대로 도착했다 [E].
- 앱의 커스텀 미들웨어 2개도 의도적으로 순수 ASGI다 (`src/main.py` `LoggingMiddleware`, `SecurityHeadersMiddleware`). `BaseHTTPMiddleware`와 `@app.middleware("http")`는 사용되지 않는다 [S: grep]. 앞으로 BaseHTTPMiddleware를 추가하지 않는 것을 #16 수용 기준에 넣을 것을 권장한다.
- 테스트 모드(`config.TESTING`)에서는 CSRF 미들웨어가 빠지므로, CSRF와 스트림의 공존은 단위 테스트로 보장되지 않는다. `tests/browser_server.py`도 `TESTING=true`인 stdlib `http.server` fixture라 확인 수단이 못 된다. 이 실험처럼 `CSRFMiddleware`를 명시적으로 단 앱을 실제 uvicorn에 띄우는 별도 테스트가 필요하다.

## 4. 권장 서버 패턴 (실험 [E]에서 그대로 검증)

```python
import asyncio, json
import anyio
from fastapi.responses import StreamingResponse

SSE_HEADERS = {"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"}
HEARTBEAT_S = 15

def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

@router.post("/sessions/{session_id}/runs")
async def create_run(...):
    # 권한·요청 ID·입력 해시 검증, 실행 레코드 생성(짧은 세션, commit)은 여기서 → 실패 시 일반 4xx/409 JSON
    upstream = await client.responses.create(..., stream=True)   # SDK 재시도 0 (PRD 9.4)

    async def event_stream():
        chunks, nxt, seq, status = aiter(upstream), None, 0, "running"
        try:
            yield sse("run.accepted", {"run_id": run_id, "turn_id": turn_id, "seq": seq})
            while True:
                if nxt is None:
                    nxt = asyncio.ensure_future(anext(chunks))
                done, _ = await asyncio.wait({nxt}, timeout=HEARTBEAT_S)
                if not done:
                    yield ": ping\n\n"          # heartbeat: seq 증가 없음, TTFT 아님
                    continue
                task, nxt = nxt, None
                try:
                    ev = task.result()
                except StopAsyncIteration:
                    break
                if ev.type == "response.output_text.delta":
                    seq += 1
                    yield sse("output.delta", {"run_id": run_id, "seq": seq, "text": ev.delta})
            # 최종 텍스트 저장(짧은 세션) 후
            status = "completed"
            yield sse("output.completed", {"run_id": run_id, "seq": seq + 1})
        except Exception as exc:                 # 제공자 오류 → 배타적 종료 이벤트
            status = "failed"
            yield sse("run.failed", {"run_id": run_id, "seq": seq + 1, "code": type(exc).__name__})
        finally:                                 # CancelledError(연결 종료) 포함 모든 경로
            with anyio.CancelScope(shield=True):
                if nxt is not None:
                    nxt.cancel()
                    await asyncio.gather(nxt, return_exceptions=True)
                await upstream.close()
                if status == "running":
                    status = "interrupted"       # 사용자 취소 플래그가 있으면 "cancelled"
                # await save_run_status(run_id, status, partial_text)  # 짧은 세션, 최선 노력

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)
```

- `CancelledError`는 `Exception`이 아니므로 `except Exception`에 잡히지 않고 `finally`만 거친다. 취소 경로에서 `yield`하지 않는다(연결이 이미 끊겼고 yield는 금지).
- 검증 실패·409처럼 스트림 시작 전에 판단할 수 있는 오류는 `StreamingResponse`를 만들기 전에 일반 HTTP 응답으로 돌려준다. 일단 200과 헤더를 보내면 상태 코드를 바꿀 수 없다.
- 위 실험에서 `run.accepted → delta → ping, ping → delta×2 → completed` 순서, 정상 종료 시 `persist completed`, 중간 종료 시 `sdk.iter cancelled → sdk.close → persist interrupted`를 확인했다 [E]. OpenAI 이벤트 타입명(`response.output_text.delta`)은 어댑터 정규화 대상이며 여기서는 예시일 뿐이다.

## 5. 권장 클라이언트 패턴 (`fetch` + `ReadableStream`)

`EventSource`는 GET 전용이고 커스텀 헤더를 넣을 수 없다. 그래서 PRD 9.2대로 `fetch` POST를 쓴다. `chat.js`의 `getCsrfHeaders()`(`x-csrf-token` 쿠키 → 헤더)와 `fetchWithAuthGuard()`를 재사용한다.

```js
async function streamRun(url, body, { signal, onEvent }) {
  const res = await fetchWithAuthGuard(url, {
    method: 'POST', signal, body: JSON.stringify(body),
    headers: { ...getCsrfHeaders(), Accept: 'text/event-stream' },
  });
  if (!res.ok || !res.headers.get('content-type')?.startsWith('text/event-stream')) {
    throw new Error(`HTTP ${res.status}`);                 // 409/4xx JSON 경로
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;                                        // completed 없이 끝나면 = 실패(PRD 9.2)
    buf += value.replace(/\r\n?/g, '\n');
    let i;
    while ((i = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, i); buf = buf.slice(i + 2);
      let event = 'message'; const data = [];
      for (const line of frame.split('\n')) {
        if (line === '' || line.startsWith(':')) continue;  // 주석 = heartbeat
        const c = line.indexOf(':');
        const field = c === -1 ? line : line.slice(0, c);
        const val = c === -1 ? '' : line.slice(c + 1).replace(/^ /, '');
        if (field === 'event') event = val;
        else if (field === 'data') data.push(val);
      }
      if (data.length) onEvent(event, JSON.parse(data.join('\n')));
    }
  }
}
// 취소: const ac = new AbortController(); streamRun(url, body, { signal: ac.signal, ... }); ac.abort();
```

- 파싱 규칙은 WHATWG 기준이다: 줄바꿈 CRLF/LF/CR 모두 허용, `:`로 시작하는 줄은 무시, 빈 줄이 dispatch, 여러 `data:` 줄은 `\n`으로 연결, 값 앞 공백 1개 제거 [W]. 이 코드는 `id`·`retry`를 무시한다. fetch에는 자동 재연결이 없으므로 필요 없다.
- `TextDecoderStream`은 Baseline Widely available(2022-09부터)이다 [W: MDN]. `for await (… of res.body)`보다 `getReader()` 루프가 MDN의 정식 패턴이다 [W: MDN Using readable streams].
- `AbortController.abort()` → 브라우저가 연결을 닫음 → 서버 generator `CancelledError`. Node 24 fetch로 end-to-end 실측했다(`AbortError`, 서버 `persist interrupted`) [E].
- `done`인데 `output.completed`/`run.failed`가 없었다면 성공으로 처리하지 않는다. GET 상태 조회로 복구하고, 그 조회 중에만 한시적으로 폴링한다(PRD 9.3). 기존 2s `hx-trigger="every 2s"` 폴링(`chat.js:279`)은 이 경로로 대체될 대상이다.

## 6. 테스트 방법

- **내용 검증 (유한 스트림)**: `TestClient`(starlette `testclient.py:348-366`, `portal.call(self.app, …)`로 앱이 끝날 때까지 실행한 뒤 `httpx.ByteStream`으로 반환)와 `httpx.ASGITransport`(`httpx/_transports/asgi.py`, `body_parts`를 모아 `response_complete` 후 반환)는 **둘 다 전체 버퍼링**이다 [S]. httpx 문서도 스트리밍 제약을 따로 언급하지 않는다 [W]. 따라서 LLM 클라이언트를 기존 주입 방식(fake)으로 바꾸고 유한 이벤트를 내게 한 뒤 다음과 같이 검증한다. 무한 스트림이나 heartbeat 대기는 테스트를 멈추게 한다(HEARTBEAT_S 주입 권장).
  ```python
  with client.stream("POST", url, json=body) as r:
      assert r.headers["content-type"].startswith("text/event-stream")
      events = [l.removeprefix("event: ") for l in r.iter_lines() if l.startswith("event: ")]
  assert events[0] == "run.accepted" and events[-1] == "output.completed"
  ```
- **연결 종료·취소 검증**: ASGI 앱을 직접 호출한다. `receive`는 첫 호출에 `http.request`를 주고, 첫 `output.delta`를 `send`한 뒤 `http.disconnect`를 반환한다. 그 뒤 fake upstream의 `close()` 호출 여부와 저장 상태(`interrupted`)를 assert한다. scope에 `"asgi": {"spec_version": "2.3"}`를 넣어 uvicorn과 같은 경로를 태운다. 실험에서 `sdk.iter cancelled → sdk.close → persist interrupted`를 확인했다 [E].
- **실제 증분 전달·CSRF 공존**: 스레드에서 띄운 실제 `uvicorn.Server`(`Config(app, port=…)`, `server.started` 대기) + `httpx.Client.stream()`으로 도착 시각을 측정한다. 이때 `r.iter_raw()`가 반환한 iterator를 **변수에 보관**해야 한다. 임시 iterator를 `next()`만 하고 버리면 GC가 응답을 닫아 가짜 disconnect가 생긴다(실험 중 실제로 겪음) [E].

## UNCONFIRMED

- 운영 리버스 프록시·LB 종류와 버퍼링·idle timeout 설정(저장소에 배포 설정 없음).
- 상위 HTTP 연결을 닫았을 때 각 제공자(OpenAI/Claude/Gemini)가 서버 측 생성과 과금을 즉시 멈추는지.
- 취소가 generator 바깥(`send` 대기 중)에 떨어져 generator가 `yield`에서 멈춘 채 GC로만 정리되는 경로: 소스상 이론적으로 가능하지만 backpressure 실험에서도 재현되지 않았다.
- 새 FastAPI 버전의 SSE 전용 지원 여부(0.121에는 없음만 확인).

## 출처

- [S] `starlette/responses.py` (StreamingResponse), `starlette/requests.py:304` (is_disconnected), `starlette/middleware/gzip.py`, `starlette/testclient.py`; `uvicorn/protocols/http/{h11,httptools}_impl.py`, `uvicorn/protocols/utils.py` (ClientDisconnected); `starlette_csrf/middleware.py`; `fastapi/routing.py:101-115`; `httpx/_transports/asgi.py`; `openai/_streaming.py`; 저장소 `src/main.py`, `src/api/dependencies.py:43`, `static/js/chat.js`
- [W] WHATWG HTML — Server-sent events: https://html.spec.whatwg.org/multipage/server-sent-events.html
- [W] MDN — Using readable streams: https://developer.mozilla.org/en-US/docs/Web/API/Streams_API/Using_readable_streams · TextDecoderStream: https://developer.mozilla.org/en-US/docs/Web/API/TextDecoderStream
- [W] nginx proxy module (proxy_buffering, X-Accel-Buffering, proxy_read_timeout): https://nginx.org/en/docs/http/ngx_http_proxy_module.html
- [W] anyio — Cancellation and timeouts: https://anyio.readthedocs.io/en/stable/cancellation.html
- [W] httpx — Transports (ASGITransport): https://www.python-httpx.org/advanced/transports/
- [W] RFC 9111 §5.2.2.6 no-transform: https://www.rfc-editor.org/rfc/rfc9111#section-5.2.2.6
- PRD `Refactoring_docs/misconcept_platform_refactoring_prd_ko_v1.0.md` §9.2–9.4
