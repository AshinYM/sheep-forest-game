# 로컬 게임 HTTP 계약

주소는 `http://127.0.0.1:8063`입니다. 단일 로컬 서버·브라우저용이며 인터넷 공개 배포용 인증·사용자 관리 시스템이 아닙니다. 같은 DB를 여러 서버 프로세스에서 동시에 쓰는 구성은 지원하지 않습니다.

| 요청 | 역할 | 정상 응답 |
|---|---|---|
| `POST /api/new` + `{}` | 새 조사와 HttpOnly/SameSite 쿠키 생성 | 201, `state` |
| `GET /api/state` | 쿠키가 가리키는 조사 상태 복원 | 200, `state` |
| `GET /api/choices` | 현재 사건에 한정된 짧은 명령 | 200, `choices` |
| `POST /api/turn` | 입력·해석 확인·취소 | 200, 게임 결과 |
| `GET /health` | 서버와 설정 모드 확인 | 200 |

`state`의 `game_id`는 조사 식별자, `revision`은 상태 변경 버전입니다. 세션 쿠키가 해당 조사를 선택하며 요청 본문의 상태는 신뢰하지 않습니다. 프런트엔드는 서버가 반환한 상태만 표시합니다.

## 입력

```json
{
  "operation": "input",
  "request_id": "unique-request-id",
  "game_id": "state에서 받은 조사 ID",
  "expected_revision": 0,
  "text": "주변을 조사한다"
}
```

확인·취소는 `operation=confirm` 또는 `cancel`, `text` 대신 서버가 발급한 `proposal_id`를 보냅니다. 클라이언트가 action·위반 횟수·아이템·생사를 직접 지정하는 필드는 거부합니다.

- `request_id`: 8~80자의 영문·숫자·`_`·`-`, 요청 하나당 새 값
- `expected_revision`: 현재 상태의 정수 버전
- `text`: 공백을 제외한 내용이 있는 1~1,000자 문자열
- JSON 본문 최대 16 KiB, chunked 전송 미지원
- Host는 실제 서버의 `127.0.0.1:포트`, Origin이 있으면 동일 출처여야 함

## 결과

- `advanced`: 유효한 행동과 시간 진행
- `violation`: 첫 번째 또는 두 번째 위반, `hint` 제공
- `game_over`: 누적 세 번째 규칙 위반, 사망. 추가 입력으로 위반 수를 늘리지 않음
- `escaped` / `ended`: 탈출 또는 새벽 수색 종료
- `ambiguous` / `dialogue` / `clarify`: 세계 상태·위반·시간 변경 없음
- `confirmation_required`: LLM 행동 제안. 확인 전에는 세계 상태 변경 없음
- `cancelled` / `rejected`: 제안 취소 또는 만료. 세계 상태 변경 없음
- `interpretation_error`: 외부 해석 오류·호출 상한. 위반·시간 변경 없음

모든 정상 게임 응답은 현재 `state`와 `replayed`를 포함합니다. 모델 요청 횟수는 세계 상태와 별도로 증가할 수 있습니다.

형식 오류는 400, 세션 없음은 401, 다른 Host/Origin은 403, 크기 초과는 413, 잘못된 Content-Type은 415입니다. 다른 payload로 request_id를 재사용하거나 오래된 revision/조사 ID를 보내면 409로 거부합니다. 현재 상태를 받을 수 있는 오류에서는 최신 state도 제공합니다.

## 중복·중단

같은 조사와 같은 request_id·payload는 SQLite에 저장된 결과를 다시 사용합니다. 같은 ID에 다른 payload는 거부합니다. 재전송 응답에는 **최신 상태**를 함께 제공해 오래된 응답으로 화면이 되돌아가지 않게 합니다. 취소되거나 만료된 확인 제안은 다시 열지 않습니다.

상태 변경과 완료 응답 기록은 한 DB 트랜잭션으로 저장합니다. 외부 API 호출 전 요청과 호출 횟수를 예약합니다. 그 사이 프로세스가 중단되면 같은 요청을 자동 재실행하지 않고 `interrupted`로 거부합니다. 사용자가 최신 상태에서 새 요청을 만들 수 있지만, 원격 API에서 이미 진행된 작업이나 비용을 취소한다는 뜻은 아닙니다.

같은 세션은 프로세스 내 잠금으로 직렬 처리합니다. 서로 다른 요청이 동시에 같은 revision을 사용하면 먼저 확정된 상태 이후의 요청은 409가 됩니다.
