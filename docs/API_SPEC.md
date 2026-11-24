# REST·WebSocket API 규격

기준일: **2026-11-22** · 백엔드 **v1.5.0** · **REST 36개 작업·34개 경로, WebSocket 2개**

현재 코드에서 동작하는 계약을 정리한다. 전체 필드·필수값·자료형은 [openapi.json](openapi.json), 실행 예시는 [EXAMPLES.md](EXAMPLES.md)와 [examples.json](examples.json)에 있다. 실행 중인 서버의 `/docs`와 `/openapi.json`도 같은 버전을 확인한다. 실제 AI·장치와의 합의가 필요한 항목은 [DESIGN.md](DESIGN.md)와 [CAMERA_REVIEW.md](CAMERA_REVIEW.md)를 함께 적용한다.

## 1. 접속·인증·대상 권한

내 PC 기본 주소는 `http://127.0.0.1:8000`이다. JSON 요청은 `Content-Type: application/json`, 로그인 사용자·장치 요청은 `Authorization: Bearer <토큰>`을 사용한다. 다른 PC·휴대폰에서는 서버 PC의 IPv4 주소를 사용하고, 다른 웹 출처는 `CORS_ORIGINS`에 맞춘다. [실행 방법](../README.md)을 참고한다.

| 인증 | 현재 방식·범위 |
|---|---|
| 공개 | `/health`, Mock 로그인, 비밀번호 로그인 |
| U 사용자 | ADMIN 전체, GUARDIAN 연결된 대상, SENIOR 본인 대상. 각 요청마다 DB 연결·역할 검사 |
| A 관리자 | 전체 현황·사고·통합 sync·모의 입력. 관리자 이외 403 |
| D 장치 | `gateway-demo-token`. 첫 가상 대상의 게이트웨이와 직속 장치 범위만 허용 |

Mock 로그인은 `POST /api/v1/auth/mock-login`에 `{"account_id":"guardian-demo"}`를 보낸다. `guardian-demo`는 `guardian-demo-token`, `admin-demo`는 `admin-demo-token`, `senior-demo`는 `senior-demo-token`을 반환한다. 비밀번호·만료 없는 개발용 고정 토큰이다. 가상 대상 ID는 목록 또는 [fixtures.json](fixtures.json)에서 얻는다.

비밀번호 로그인은 `POST /api/v1/auth/login`에 `account_id`, `password`를 보낸다. 서버 DB의 역할로 `access_token`, `token_type`, `role`, `user_id`, `mock=false`, `expires_in`, `expires_at`을 반환한다. 임의 문자열의 **DB 세션 토큰이며 JWT가 아니다.** 기본 만료는 1시간이다. 비밀번호는 PBKDF2-HMAC-SHA256 60만 회와 임의 salt, DB의 토큰은 SHA-256 해시로 저장한다. `GET /auth/me`는 안전한 프로필만 반환한다. `POST /auth/logout`은 비밀번호 세션을 철회하고 204를 반환하며 같은 알려진 세션의 반복 로그아웃도 204다. Mock 고정 토큰을 로그아웃으로 철회하지는 않는다.

로그인 요청에 역할을 보내 권한을 선택하지 않는다. 계정당 15분 5회, 접속 단위 15분 30회 실패 후 429로 제한한다. `ENABLE_MOCK_AUTH=0`은 Mock 로그인과 사용자 고정 토큰 허용을 끄며 WebSocket에도 적용한다. 장치 테스트 토큰을 실제 장치 인증으로 바꾸는 기능은 별도다. 회원가입·비밀번호 재설정 API는 없다. 연습 계정 준비는 [LOGIN_GUIDE.md](LOGIN_GUIDE.md)를 따른다.

## 2. 공통 데이터·오류 규칙

| 항목 | 현재 규칙 |
|---|---|
| 식별자 | UUID 문자열. 음성 ID·로그인 ID 등 별도 정의 필드는 예외 |
| 일반 시각 | 시간대가 있는 ISO 8601 입력, UTC `Z` 응답. URL의 `+09:00`은 인코딩하거나 `Z` 사용 |
| 수신 시각 | 서버보다 5분 넘는 미래 이벤트는 422 `FUTURE_EVENT`. 오래된 판단은 최신 상태를 덮어쓰지 않음 |
| 복약 시간 | `Asia/Seoul`의 `HH:mm`, 요일 `MON`~`SUN` 1~7개·중복 불가·월~일 정렬 |
| 기간 | `start_at` 포함·`end_at` 제외, 둘 다 있으면 시작 < 종료. 시간대 필수 |
| 패턴 기간 | 관찰 구간과 조회 구간이 겹치는 기록 조회 |
| 페이지 | page≥1, size 기본 20·최대 100. 사고는 발생 시각 DESC·사고 ID DESC |
| 빈 결과 | 복약 목록은 직접 `[]`. 그 외 목록은 `items:[]` 등 정의된 응답 모델. 전체 건수가 0이면 `total_pages=0`. 범위 밖 페이지는 `items=[]`이지만 실제 `total`과 `total_pages` 유지 |
| 입력 검증 | 정의되지 않은 JSON 필드 금지, 숫자 NaN·무한대 금지, 필드별 범위·필수값 검증 |
| 204 | 응답 본문 없음. 프론트에서 JSON으로 읽지 않음 |
| 추적 | 응답 헤더 `X-Request-ID`, 오류 본문 `request_id` |

공통 오류 본문은 다음 구조다. `details`는 비어 있을 수 있다.

```json
{
  "error_code": "VALIDATION_ERROR",
  "message": "입력 내용을 확인하세요.",
  "details": [{"field": "body.med_time", "message": "유효한 시간을 입력하세요."}],
  "request_id": "요청별 식별자",
  "timestamp": "2026-11-22T00:00:00.000000Z"
}
```

| HTTP | 주요 의미·처리 |
|---|---|
| 400 | 잘못된 요청 형식 등. 메시지 확인 |
| 401 | 토큰 없음·세션 만료·로그인 실패·장치 인증 오류. 사용자 로그인 안내. `WWW-Authenticate: Bearer` |
| 403 | 역할·대상·장치 범위 불일치. 같은 요청 자동 재시도로 해결되지 않음 |
| 404 | 권한 확인 뒤 조회할 기록 없음 등 |
| 409 | 이벤트 ID·등록 키 충돌, 카메라 순서·최종값 충돌, 이미 종료된 명령, 커서 불일치 |
| 422 | 필수값·자료형·UUID·시간·범위·상태 조합 검증 실패 |
| 429 | 로그인 실패 제한. 최대 15분 후 재시도 |
| 500 | 서버 내부 오류. 요청 ID와 함께 확인 |

## 3. REST 전체 작업 목록

아래 목록은 v1.5.0 `openapi.json`의 모든 REST 작업을 옮긴 것이다. 경로는 생략 없이 표시한다. 요청 칸의 `/` 뒤는 쿼리 또는 별도 헤더다. U·A·D의 의미는 1번 표와 같으며 U는 대상별 접근 검증을 포함한다. 204 외 응답 이름의 세부 필드는 OpenAPI와 예시를 확인한다. `/`, `/admin-test`, `/login-test`, `/design/`는 개발 확인 화면으로 아래 업무 API 개수에서 제외한다.

| 메서드 | 전체 경로 | 권한 | JSON 모델 / 쿼리·헤더 | 성공 응답 |
|---|---|---|---|---|
| GET | `/health` | 공개 | 없음 | 200 본문 없음 |
| POST | `/api/v1/auth/mock-login` | 공개 | Login | 200 LoginResult |
| POST | `/api/v1/auth/login` | 공개 | PasswordLogin | 200 PasswordLoginResult |
| GET | `/api/v1/auth/me` | U | 없음 | 200 Profile |
| POST | `/api/v1/auth/logout` | U | 없음 | 204 본문 없음 |
| GET | `/api/v1/seniors` | U | 없음 | 200 SeniorList |
| GET | `/api/v1/seniors/{senior_id}/status` | U | 없음 | 200 Status |
| GET | `/api/v1/admin/seniors` | A | 없음 / page, size | 200 AdminSeniorPage |
| GET | `/api/v1/seniors/{senior_id}/devices` | U | 없음 | 200 DeviceList |
| GET | `/api/v1/seniors/{senior_id}/incidents` | U | 없음 / page, size, start_at, end_at | 200 IncidentPage |
| GET | `/api/v1/incidents/{incident_id}` | U | 없음 | 200 IncidentDetail |
| GET | `/api/v1/admin/incidents` | A | 없음 / page, size, senior_id, start_at, end_at | 200 AdminIncidentPage |
| GET | `/api/v1/admin/sync` | A | 없음 / after_cursor, limit | 200 AdminSyncResult |
| GET | `/api/v1/seniors/{senior_id}/patterns` | U | 없음 / start_at, end_at | 200 PatternList |
| GET | `/api/v1/voices` | U | 없음 | 200 VoiceList |
| GET | `/api/v1/seniors/{senior_id}/medications` | U | 없음 | 200 Medication[] |
| POST | `/api/v1/seniors/{senior_id}/medications` | U | MedicationCreate / idempotency-key | 201 Medication |
| PATCH | `/api/v1/medications/{schedule_id}` | U | MedicationPatch | 200 Medication |
| DELETE | `/api/v1/medications/{schedule_id}` | U | 없음 | 204 본문 없음 |
| GET | `/api/v1/seniors/{senior_id}/medication-logs` | U | 없음 | 200 MedicationLogList |
| POST | `/api/v1/notifications/token` | U | PushTokenCreate | 200 PushTokenResult |
| DELETE | `/api/v1/notifications/token/{installation_id}` | U | 없음 | 204 본문 없음 |
| GET | `/api/v1/notifications` | U | 없음 | 200 NotificationList |
| GET | `/api/v1/seniors/{senior_id}/sync` | U | 없음 / after_cursor, limit | 200 SyncResult |
| GET | `/api/v1/patterns/{pattern_id}` | U | 없음 | 200 Pattern |
| POST | `/api/v1/ingest/risk-events` | D | RiskEvent | 200 IngestResult |
| POST | `/api/v1/ingest/activities` | D | ActivityEvent | 200 IngestResult |
| POST | `/api/v1/ingest/heartbeats` | D | Heartbeat | 200 IngestResult |
| POST | `/api/v1/ingest/patterns` | D | PatternInput | 200 IngestResult |
| POST | `/api/v1/ingest/fall-assessments` | D | FallAssessment | 200 FallAssessmentResult |
| POST | `/api/v1/mock/fall-assessments` | A | FallAssessment | 200 FallAssessmentResult |
| GET | `/api/v1/devices/{device_id}/commands` | D | 없음 | 200 CommandList |
| POST | `/api/v1/commands/{command_id}/ack` | D | CommandAck | 200 Command |
| POST | `/api/v1/mock/scenarios` | A | Scenario | 200 ScenarioResult |
| POST | `/api/v1/mock/reset` | A | 없음 | 200 ResetResult |
| POST | `/api/v1/mock/medication-tick` | A | 없음 | 200 ReminderTickResult |

## 4. 프론트 화면에 연결할 주요 응답

| 화면 | 주요 값·표시 규칙 |
|---|---|
| 대상 선택·전화 | `GET /seniors`의 items: `senior_id`, `name`, `timezone`, `phone_number`. 번호는 본인 계정 번호이며 없으면 null. 번호 null이면 전화 버튼 비활성화·미등록 표시. 119는 프론트 고정 번호 |
| 현재 상태 | `risk_level`, `last_updated_at`, `status_event_id`, `data_freshness`, `device_summary.online/offline`. `status_event_id=null`은 판단 기록 없음이며 기본 NORMAL·초기 시각을 AI 판단으로 표시하지 않음 |
| 사고 목록·상세 | `incident_id`, `event_id`, `event_time`, `risk_level`, `summary`, `status=OPEN/CANCELLED`. 상세는 `evidence`와 `assessment`, `assessment_history`, `assessment_history_has_more` 추가 |
| 생활 패턴 | `pattern_id`, `pattern_type`, `observation_start_time/end_time`, `metric_value/unit`, `summary_note`, `is_anomaly`. 단일 발생 시각 대신 관찰 기간 표시 |
| 복약 | `schedule_id`, `medication_name`, `med_time`, `days_of_week`, `voice_id`, `activity_window_minutes`, `is_active`, `timezone` |
| 장치 | `device_id`, `device_type`, `parent_device_id`, `connection_status`, `last_heartbeat_at` |
| 관리자 전체 | `/admin/seniors`는 이름·상태·판단 근거·연결 수를 페이지 조회. `/admin/incidents`는 사고 값과 `senior_name`, 선택 대상·기간 필터 |

장치가 하나라도 OFFLINE이거나 등록 장치가 없으면 `STALE`이다. ONLINE 장치의 하트비트가 90초 넘게 없으면 서버가 끊김을 기록한다. 위험 단계는 끊김으로 NORMAL로 바꾸지 않는다. `FRESH/STALE`은 장치 연결 요약이며 AI 판단의 유효 기간을 뜻하지 않는다. 초기 상태 상세는 [INITIAL_STATUS.md](INITIAL_STATUS.md)를 따른다.

첫 연동은 보호자 Mock 로그인 → `GET /seniors` → 선택 ID의 `/status` → 같은 ID의 `/medications` 순서다. 이 목록 선택은 시험용 절차이며 별도 앱 목록 화면을 필수로 요구하지 않는다. 비어 있는 목록·번호 null·401·403을 함께 표시해 본다. 프론트의 실제 화면 연동 완료 여부는 공동 시험으로 확인한다.

## 5. 복약 CRUD·앱 알림·음성 명령

보호자는 연결 대상, 어르신은 본인, 관리자는 전체 대상의 일정 CRUD를 할 수 있다. 알 수 있는 schedule_id만 보낸다고 접근 권한이 생기지 않는다.

| 등록 필드 | 필수·기본값 |
|---|---|
| `medication_name` | 필수, 1~80자 |
| `med_time` | 필수, `HH:mm` 00:00~23:59 |
| `days_of_week` | 필수, 요일 1~7개·중복 금지 |
| `voice_id` | 생략 시 `TYPE_A`, `TYPE_B`도 지원. 사용 가능 목록 `/voices` |
| `activity_window_minutes` | 생략 시 30, 1~180분 |
| `is_active` | 생략 시 true |

등록은 `Idempotency-Key` 헤더 8~128자를 요구하고 201을 반환한다. 같은 사용자·경로·키·본문 재시도는 최초 응답을 돌려주며, 본문이 다르면 409 `IDEMPOTENCY_KEY_CONFLICT`다. 새 등록에는 새 키를 사용하고 네트워크 재시도에는 기존 키를 유지한다. PATCH는 실제 바꾸는 필드만 보내고 한 개 이상 필요하다. 명시적 null은 거절하며 생략 필드는 유지한다. DELETE는 소프트 삭제·204이며 이미 삭제된 같은 일정도 204, 존재한 적 없는 ID는 404다.

일정 수정·끄기·삭제 시 기존 대기 음성 명령은 만료시키고 관련 안내 이력은 FAILED로 남긴다. 삭제한 일정은 목록에서 빠지며 이력은 보존한다. 숨겨진 음성·대기시간 설정을 수정 화면에서 서버 기본값으로 덮어쓰지 않는다.

앱 복약 안내는 일정의 활성 KST 시간 구간에 활동 감지 없이 본인에게 생성한다. 스피커는 같은 구간에서 120초 이내 활동을 받았을 때 별도 명령을 만든다. 같은 일정·현지 날짜 중복을 막고, 오래된 활동 재전송은 즉시 재생 명령을 만들지 않는다. 앱 알림 생성은 서버가 실행되는 동안 주기 점검으로 처리하며 중단 중 발송을 보장하지 않는다.

게이트웨이는 `/devices/{device_id}/commands`에서 PENDING 명령을 받고 `/commands/{command_id}/ack`에 `{"status":"SUCCESS"}` 또는 `FAILED`를 보낸다. 같은 최종 ACK는 중복 성공, 다른 최종값·만료 후 변경은 409 `COMMAND_ALREADY_FINAL`이다. 명령 상태는 PENDING/SUCCESS/FAILED/EXPIRED, 안내 이력은 PENDING/SUCCESS/NO_ACTIVITY/FAILED다. **안내 성공은 복약 완료 확인이 아니다.** [SENIOR_MEDICATION.md](SENIOR_MEDICATION.md)에 확인 방법이 있다.

## 6. 게이트웨이·AI HTTP 입력과 중복 규칙

공통 필드는 `event_id`, `senior_id`, `device_id`, 시간대 포함 `occurred_at`이다. 장치·대상 범위 검사 후 원본을 저장한다. `event_id`는 수신 종류 전체에서 고유하다. 같은 ID·같은 본문은 `duplicate=true`, 같은 ID·다른 본문은 409 `EVENT_ID_CONFLICT`다. 숫자 없는 필드는 null/생략으로 두며 근거를 임의로 만들지 않는다.

| 입력 경로 끝부분 | 추가 필드와 처리 |
|---|---|
| `risk-events` | `risk_level=NORMAL/CAUTION/DANGER`, 선택 `piezo_impact_val≥0`, `tof_estimated_height_mm≥0`, `ai_fall_confidence=0~1`, 기본 `gesture_cancelled=false`, `summary`. 압전 수치의 실제 단위·보정은 협의 대상 |
| `activities` | `activity_type=PRESENT/WAKE_UP/MOVEMENT`. 활성 복약 시간 구간·최근 활동이면 명령 생성 |
| `heartbeats` | `connection_status=ONLINE/OFFLINE`, 생략 시 ONLINE. 오래된 입력이 최신 연결 상태를 덮어쓰지 않음 |
| `patterns` | `pattern_type`, 관찰 시작·종료, `metric_value≥0`, `metric_unit`, `summary_note`, `is_anomaly`. NIGHT_ACTIVITY_SURGE는 count, PROLONGED_LYING/INACTIVE는 minutes |
| `fall-assessments` | 아래 단계별 제안 규격. 기존 일회성 위험 입력과 별도이며 실제 AI 출력 변환은 공동 합의 필요 |

일회성 NORMAL은 상태·근거를 저장하고 사고를 만들지 않는다. CAUTION/DANGER는 사고를 만든다. 미취소 DANGER는 연결 보호자에게 가상 낙상 알림을 기록한다. `gesture_cancelled=true`는 사고 CANCELLED, 현재 유효 상태 NORMAL이며 원본 위험 단계는 유지한다. 이상 패턴은 PATTERN_ALERT를 기록한다.

### 카메라 단계별 제안 규격

공통 필드 외 `incident_id`(송신 측 생성 UUID), `stage`, `analysis_status`, 선택 `confirmed_fall`, `camera_confidence=0~1`, `summary` 1~300자를 사용한다. 같은 사고 ID를 유지하고 전송마다 새 event_id를 사용하되 같은 요청 재시도는 동일 ID·본문을 유지한다.

| 단계·상태 | 값과 처리 |
|---|---|
| PRELIMINARY·PENDING_VERIFICATION | 확정값·카메라 신뢰도는 null/생략. CAUTION·OPEN 사고 생성 |
| FINAL·COMPLETED | strict boolean `confirmed_fall` 필수. true는 낙상 확정, false는 최종 오탐 취소. 신뢰도 선택 |
| FINAL·CAMERA_UNAVAILABLE / CAMERA_TIMEOUT / INSUFFICIENT_DATA | 확정값·신뢰도 null/생략. 실패 기록·의심 상태 유지, 후속 재시도 가능 |

초기 의심 없으면 409 `PRELIMINARY_REQUIRED`, 새 전송 ID로 초기 의심 중복 생성은 `INCIDENT_ID_CONFLICT`, 이전보다 오래된 결과는 `OUT_OF_ORDER_ASSESSMENT`다. 완료 결과를 다른 내용으로 바꾸면 `ASSESSMENT_ALREADY_FINAL`이다. 다른 전송 ID여도 확정값·신뢰도·설명이 모두 같은 완료 결과는 중복으로 수신 이력만 남기며 사고·알림을 반복하지 않는다.

최종 결과는 기존 사고를 갱신하고, 이 사고가 현재 상태의 근거인 경우에만 현재 상태를 바꾼다. 과거 사고의 늦은 최종 결과가 새 사건의 상태를 덮어쓰지 않는다. 같은 대상·게이트웨이의 사건만 이어 붙인다. 서버가 카메라를 호출하거나 자동으로 카메라 시간 초과를 판정하지는 않는다.

사고 상세의 `assessment`는 현재 단계 상태, `assessment_history`는 최근 50개 수신·반영 이력이다. 더 있으면 `assessment_history_has_more=true`다. 이전 일회성 사고는 assessment=null, 이력=[]다. 원시 카메라 false·AI UNKNOWN·융합 MEDIUM을 임의로 최종 오탐 취소로 보내면 안 된다. [CAMERA_REVIEW.md](CAMERA_REVIEW.md)에서 예시와 합의 사항을 확인한다.

## 7. 알림 기록·딥링크

`POST /notifications/token`은 설치 UUID, `provider=EXPO/FCM`, 10~4096자 push_token, `device_os=ANDROID/IOS`를 받는다. 사용자 ID는 로그인 토큰에서 결정한다. 같은 사용자·설치의 토큰은 갱신하고 다른 사용자·설치에 중복 등록된 제공자 토큰은 409다. 등록 결과에 push_token 원문은 반환하지 않는다. DELETE는 본인 설치를 해제한다.

| 대상 | 알림 종류 | deep_link |
|---|---|---|
| 연결 보호자 | `FALL_ALERT` | `safehome://incidents/{incident_id}` |
| 연결 보호자 | `PATTERN_ALERT` | `safehome://patterns/{pattern_id}` |
| 어르신 본인 | `MEDICATION_REMINDER` | `safehome://medications/{schedule_id}` |

`GET /notifications`는 본인 알림 기록만 반환한다. 토큰 등록 여부에 따라 `MOCK_QUEUED` 또는 `NO_TOKEN`이며 실제 제공자 전송 결과가 아니다. 복약 전용 단건 GET API는 없으므로 알림의 schedule_id로 목록을 찾아 표시하거나 복약 실시간 이벤트의 약 이름·시각을 사용한다. 일정이 변경·삭제되었을 때의 표시도 프론트와 맞춘다. 딥링크의 실제 앱 화면 등록과 실제 FCM/Expo 발송은 아직 미연동이다.

## 8. WebSocket 두 채널과 재연결

두 채널 모두 URL에 토큰을 넣지 않고 연결 후 **10초 이내 첫 메시지**로 전송한다. `ws://127.0.0.1:8000` 뒤에 각 경로를 붙인다. 실제 HTTPS 환경에서는 wss를 사용한다.

대상별 첫 구독:

```json
{"action":"SUBSCRIBE","access_token":"guardian-demo-token","senior_id":"00000000-0000-4000-8000-000000000101","after_cursor":0}
```

관리자 전체 첫 구독:

```json
{"action":"SUBSCRIBE","access_token":"admin-demo-token","after_cursor":0}
```

| 항목 | 대상별 `/ws/live`·`/seniors/{senior_id}/sync` | 관리자 전체 `/ws/admin/live`·`/admin/sync` |
|---|---|---|
| 대상 | 권한 있는 어르신 한 명 | ADMIN만 전체 어르신 |
| 허용 이벤트 | ADMIN 전 종류. GUARDIAN 복약 알림 제외. SENIOR는 MEDICATION_REMINDER·MEDICATION_UPDATED·MEDICATION_DELETED만 | STATUS_CHANGED·INCIDENT_ALERT·DEVICE_STATUS_CHANGED·PATTERN_UPDATED |
| 추가 데이터 | 공통 FeedEvent | `senior_name`, WS의 `replayed` |
| HTTP after_cursor | 0 이상 | 0~9,007,199,254,740,991 |
| HTTP limit | 기본 100, 최대 500 | 기본 100, 최대 100 |
| WS after_cursor | 0 이상 정수로 전송. 현 코드는 int 변환으로 검사 | strict 정수, 위 최대값. 정의되지 않은 구독 필드 거절 |
| 한 번에 전달 | WS 100건씩, has_more면 이어 전달 | WS 100건씩, has_more면 이어 전달 |
| 커서 오류 | 미래 커서 409 CURSOR_AHEAD. 보관 하한 만료 검사 없음 | 409 CURSOR_AHEAD, 보관 하한보다 오래된 커서 CURSOR_EXPIRED |
| 오류 종료 | 4401 인증, 4403 권한, 나머지 4400 | 4401 인증, 4403 권한, 4409 커서, 나머지 4400 |

서버 메시지는 `SUBSCRIBED`, `EVENT`, `CURSOR`, `PONG`, `ERROR`다. EVENT는 cursor·event_id·senior_id·event_type·occurred_at·data를 담는다. HTTP sync는 events·next_cursor·latest_cursor·has_more를 반환한다. CURSOR는 cursor·has_more다. 클라이언트는 `{"action":"PING"}`을 보낼 수 있다. 관리자 채널은 구독 후 PING 이외 요청을 거절한다. 연결 중에도 세션 만료·로그아웃·권한을 다시 확인한다.

커서는 전체 event_feed에서 증가하므로 대상·종류 필터 때문에 숫자가 건너뛸 수 있다. EVENT를 처리한 뒤 CURSOR까지 저장하고, has_more=true면 계속 읽는다. 커서는 서버 주소·계정별로 관리한다. 이미 처리한 cursor 이하 이벤트는 다시 적용하지 않는다.

대상별 첫 연결은 구독을 연 뒤 현재 상태·목록을 조회하고, 재연결은 저장 커서로 누락 이벤트를 받는다. 관리자 첫 연결은 HTTP `/admin/sync?after_cursor=0`에서 latest_cursor 확보 → 전체 상태·사고 목록 조회 → 그 커서로 WS 구독 순서다. 사이에 들어온 이벤트는 재전달되며 `replayed=true`는 연결 시작 시 이미 존재한 기록이라는 뜻이다. 오래된 재전달 데이터로 현재 화면을 되돌리지 않도록 최신 REST를 다시 읽는다.

관리자 확인 화면은 1·2·4·8초 이후 최대 15초 간격으로 재접속한다. 이는 확인 화면 정책이며 서버의 강제 주기는 아니다. 인증·권한 오류는 자동 반복을 멈춘다. 409/4409이면 이전 커서를 지우고 새 기준점·전체 목록을 다시 읽는다. 두 채널의 순번 범위·복구 오류를 하나의 규칙으로 가정하지 않는다. [ADMIN_LIVE.md](ADMIN_LIVE.md)에 관리자 상세 절차가 있다.

## 9. 모의 시험 API와 현재 한계

`/mock/scenarios`는 관리자만 NORMAL/CAUTION/DANGER/CANCELLED/OFFLINE/ONLINE/PATTERN/ACTIVITY를 입력한다. `/mock/fall-assessments`도 관리자만 같은 단계별 검증을 시험한다. `/mock/medication-tick`은 주기 점검을 즉시 수행해 이번에 생성한 복약 알림 수를 반환한다.

`/mock/reset`은 개발용 데이터를 초기화하고 비밀번호 해시는 보존하지만 실제 로그인 세션·시연 기록·일정·푸시 토큰은 지운다. 응답 안내대로 목록을 재조회하고 커서를 초기화한다. 발표용 증빙을 먼저 보관한다.

현재 HTTP 수신·SQLite 기록·권한·CRUD·실시간·복구는 모의 입력으로 시험할 수 있다. MQTT, 실제 AI 출력 변환, 실장치 통신·스피커, FCM/Expo 전송, 실제 프론트 전체 연결, 성능·배포 검증은 별도다. 중간발표에서는 B01~B05의 설계·실행 환경·Mock 근거와 후속 선행 구현을 나눠 제시한다.
