# D5-03 공개 smoke 체크리스트 (#208)

2단계에서 승인된 공개 origin에 대해 실행한다. 실행 결과는 이 파일이 아니라
`public-smoke-<sha7>.md`(`README.md`의 template)에 옮겨 적는다.

## 실행 규칙

- origin은 셸 환경변수로만 준다. 문서·증거에 실제 값을 적지 않고 `$DEMO_ORIGIN` 등 이름으로 남긴다.
  ```sh
  export DEMO_ORIGIN=...      # d5-03-approval.md §1
  export NORMAL_ORIGIN=...    # d5-03-approval.md §1
  export DEMO_PUBLIC_URL=...  # d5-03-approval.md §1
  ```
- cookie 값, CSRF token, `browser_scope_id`, 대화 원문은 기록하지 않는다. 헤더는 아래 mask 명령을 거친 출력만 옮긴다.
- 결과 열은 `passed` / `failed` / `not run` 중 하나다. `not run`과 `failed`는 이유를 적는다.
- **필수 행이 하나라도 failed이면** 공개·유입을 즉시 멈추고 `../d5-03-approval.md` §4 순서
  (신규 start 차단 → demo 중단 → 취소/drain/dispose → 승인된 rollback 대상)로 복구한다.
  절차 본문은 #206 `docs/demo/d5-01-operations.md` §8, 결정 기록은 `rollback-decision-<sha7>.md`다.

## 체크 표

| ID | 검사 | 계약 | 명령 | 기대 | 실제 | 결과 | 이유 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| P01 | demo `/health` 무방문 readiness | §4:64-65 | 블록 P01 | 200, `cache-control: no-store`, `set-cookie` 없음 | | | |
| P02 | demo `/health` 본문 | §4:64 | 블록 P02 | `{"status":"healthy","database":"healthy","uptime_seconds":…}`; 경로·방문객 수 없음 | | | |
| P03 | normal `/health` 영향 없음 | §28:771 | 블록 P03 | 200 | | | |
| L01 | 링크 변경 **전** normal 화면 링크 수 | §26:633 | 블록 L01 | 0 (첫 공개) | | | |
| L02 | 링크 변경 **후** normal 화면 링크 수 | §26:633 | 블록 L01 | 1 | | | |
| L03 | host 분리 | §2, §26:633 | 블록 L03 | 공개 URL host = demo host, demo host ≠ normal host | | | |
| C01 | demo 쿠키 속성 | §10:254-255 | 블록 C01 | 둘 다 `Domain` 없음·`Path=/`·`Secure`·`SameSite=lax`; `demo_session_id`는 `HttpOnly`·`Max-Age=28800`; `demo_csrftoken`은 `HttpOnly`·`Max-Age`·`Expires` 없음 | | | |
| X01 | token/origin 없는 start | §10, §28:761 | 블록 X01 | 403 | | | |
| X02 | token/origin 없는 role | §10, §28:761 | 블록 X01 | 403 | | | |
| X03 | token/origin 없는 reset | §10, §28:761 | 블록 X01 | 403 | | | |
| X04 | 유효 token + 다른 Origin의 start | §10, §28:761 | 블록 X04 | 403 | | | |
| N01 | normal origin의 demo route | §28:759 | 블록 N01 | `/demo`, `/demo/state` 모두 404 | | | |
| D01 | demo의 비밀번호 로그인 POST | §4:62 | 블록 D01 | 403 | | | |
| D02 | demo 무방문 `/metrics` | §4:66 | 블록 D02 | 401 | | | |
| A01 | demo 관리자의 위험 method 1건 | §20:512 | 블록 A01 | 403 `DEMO_ACTION_FORBIDDEN` | | | |
| E01 | 외부 AI 시도 0 | §14, §28:760 | 인용 | 같은 SHA의 #203 증거 링크; 공개 환경 재실행 없음(인프라 egress 증명은 D5-02 범위) | | | |

## 명령 블록

### P01
```sh
curl -sS -D - -o /dev/null "$DEMO_ORIGIN/health" | grep -iE '^HTTP/|^cache-control:|^set-cookie:' | sed -E 's/=[^;]*/=<masked>/'
```
### P02
```sh
curl -sS "$DEMO_ORIGIN/health"; echo
```
### P03
```sh
curl -sS -o /dev/null -w '%{http_code}\n' "$NORMAL_ORIGIN/health"
```
### L01
```sh
curl -sS "$NORMAL_ORIGIN/login" | grep -cF "href=\"$DEMO_PUBLIC_URL\""
```
### L03
```sh
for u in "$DEMO_PUBLIC_URL" "$DEMO_ORIGIN" "$NORMAL_ORIGIN"; do printf '%s\n' "$u" | awk -F/ '{print $3}'; done
# 1행 = 2행, 2행 ≠ 3행이면 passed. 출력 값은 기록하지 않고 "같음/다름"만 적는다.
```
### C01
```sh
curl -sS -D - -o /dev/null "$DEMO_ORIGIN/demo" | grep -i '^set-cookie:' | sed -E 's/=[^;]*/=<masked>/'
```
### X01
```sh
for p in start role reset; do
  curl -sS -o /dev/null -w "$p %{http_code}\n" -X POST -H 'Content-Type: application/json' -d '{}' "$DEMO_ORIGIN/demo/$p"
done
```
### X04
```sh
jar=$(mktemp)
curl -sS -c "$jar" -o /dev/null "$DEMO_ORIGIN/demo"
tok=$(awk '$6=="demo_csrftoken"{print $7}' "$jar")
rid=$(python3 -c 'import uuid; print(uuid.uuid4())')
curl -sS -b "$jar" -o /dev/null -w '%{http_code}\n' -X POST \
  -H 'Content-Type: application/json' -H "x-csrf-token: $tok" \
  -H 'Origin: https://cross-origin.invalid' \
  -d "{\"request_id\":\"$rid\",\"role\":\"teacher\"}" "$DEMO_ORIGIN/demo/start"
rm -f "$jar"
```
### N01
```sh
for p in /demo /demo/state; do curl -sS -o /dev/null -w "$p %{http_code}\n" "$NORMAL_ORIGIN$p"; done
```
### D01
```sh
curl -sS -o /dev/null -w '%{http_code}\n' -X POST -d 'username=x&password=x' "$DEMO_ORIGIN/login"
```
### D02
```sh
curl -sS -o /dev/null -w '%{http_code}\n' "$DEMO_ORIGIN/metrics"
```
### A01
관리자 방문 공간을 하나 만들고 위험 method 1건만 보낸다. 응답 본문의 `code`만 기록한다.
```sh
jar=$(mktemp)
curl -sS -c "$jar" -o /dev/null "$DEMO_ORIGIN/demo"
tok=$(awk '$6=="demo_csrftoken"{print $7}' "$jar")
rid=$(python3 -c 'import uuid; print(uuid.uuid4())')
curl -sS -b "$jar" -c "$jar" -o /dev/null -X POST -H 'Content-Type: application/json' \
  -H "x-csrf-token: $tok" -H "Origin: $DEMO_ORIGIN" \
  -d "{\"request_id\":\"$rid\",\"role\":\"admin\"}" "$DEMO_ORIGIN/demo/start"
for i in $(seq 30); do  # 준비 완료(state=ready)까지 최대 30초
  st=$(curl -sS -b "$jar" -c "$jar" "$DEMO_ORIGIN/demo/state")
  [ "$(printf '%s' "$st" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("state"))')" = ready ] && break
  sleep 1
done
hdr=$(printf '%s' "$st" | python3 -c 'import json,sys; s=json.load(sys.stdin); print(s["browser_scope_id"], s["generation"], s["auth_epoch"])')
set -- $hdr
tok=$(awk '$6=="demo_csrftoken"{print $7}' "$jar")
curl -sS -b "$jar" -o "$jar.body" -w '%{http_code}\n' -X POST \
  -H "x-csrf-token: $tok" -H "Origin: $DEMO_ORIGIN" \
  -H "X-Demo-Scope: $1" -H "X-Demo-Generation: $2" -H "X-Demo-Auth-Epoch: $3" \
  "$DEMO_ORIGIN/admin/ai/settings/update"
python3 -c 'import json,sys; print(json.load(sys.stdin)["detail"]["code"])' < "$jar.body"
rm -f "$jar" "$jar.body"
```
A01의 state 응답 형식은 2단계 실행 전 같은 SHA의 #204 browser 증거와 대조한다. 형식이 다르면 `not run`과 이유를 적고
브라우저에서 관리자 역할로 같은 요청을 재현한다.
