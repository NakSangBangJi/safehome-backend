# 어르신 복약 일정·알림 규격

기준일: **2026-11-22** · 백엔드 **v1.5.0**

어르신은 본인의 복약 일정을 조회·등록·수정·삭제할 수 있다. 앱 복약 안내와 활동 기반 스피커 명령은 별개다. 현재 일정과 알림 기록은 SQLite에 저장되며 실제 휴대폰 푸시·스피커 재생은 아직 연결하지 않았다. 전체 API는 [API_SPEC.md](API_SPEC.md), 필드 정의는 [OpenAPI](openapi.json)에 있다.

## 1. 로그인과 관리 권한

개발용 Mock 로그인은 `POST /api/v1/auth/mock-login`에 `{"account_id":"senior-demo"}`를 보낸다. 반환한 토큰을 `Authorization: Bearer <토큰>`으로 사용한다. 연습용 고정 토큰 `senior-demo-token`도 기본 개발 설정에서 사용할 수 있다.

비밀번호 로그인·만료·로그아웃도 현재 구현되어 있다. `POST /api/v1/auth/login`의 DB 세션 방식이며 JWT가 아니다. 계정 준비와 개인 PC의 비밀번호 확인은 [LOGIN_GUIDE.md](LOGIN_GUIDE.md)를 따른다. 프론트의 실제 로그인 화면 완성 여부와 백엔드 API 구현은 구분한다.

| 사용자 | 일정 조회·등록·수정·삭제 범위 |
|---|---|
| 어르신 `SENIOR` | `seniors.user_id`로 연결된 본인 |
| 보호자 `GUARDIAN` | `senior_guardians`로 연결된 어르신 |
| 관리자 `ADMIN` | 현재 API에서 전체 대상 허용. 관리자 프론트 화면 범위는 조회 중심으로 협의 |

어르신은 `GET /api/v1/seniors`에서 본인 한 명을 조회한다. 다른 senior_id·schedule_id를 알아도 권한이 생기지 않으며 서버가 요청마다 대상 연결을 검사한다. 화면 역할 선택으로 서버 권한을 바꿀 수 없다.

## 2. 일정 조회·등록·수정·삭제

| 작업 | 방식과 전체 경로 | 응답 |
|---|---|---|
| 조회 | `GET /api/v1/seniors/{senior_id}/medications` | 200, 일정 배열. 없으면 `[]` |
| 등록 | `POST /api/v1/seniors/{senior_id}/medications` | 201, 새 일정 |
| 수정·켜기·끄기 | `PATCH /api/v1/medications/{schedule_id}` | 200, 변경한 일정 |
| 삭제 | `DELETE /api/v1/medications/{schedule_id}` | 204, 본문 없음 |
| 안내 이력 | `GET /api/v1/seniors/{senior_id}/medication-logs` | 200, items 배열 |
| 사용 가능한 음성 | `GET /api/v1/voices` | 200, items 배열 |

등록 필수값은 약 이름·시각·요일이다. 음성·활동 대기시간·활성 여부는 생략하면 서버 기본값을 적용한다.

| 필드 | 입력 규칙 |
|---|---|
| `medication_name` | 필수, 1~80자 |
| `med_time` | 필수, KST `HH:mm` 00:00~23:59 |
| `days_of_week` | 필수, `MON`~`SUN` 1~7개·중복 금지, 응답은 월~일 순서 |
| `voice_id` | 기본 `TYPE_A`, `TYPE_B` 가능 |
| `activity_window_minutes` | 기본 30분, 1~180분 |
| `is_active` | 기본 true. false면 안내 비활성 |

```json
{
  "medication_name": "아침약",
  "med_time": "08:30",
  "days_of_week": ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
}
```

POST 헤더에 길이 8~128자의 `Idempotency-Key`를 보낸다. 같은 등록의 네트워크 재시도는 같은 키와 본문으로 최초 응답을 받으며, 같은 키에 다른 본문을 보내면 409다. 새 등록 작업에는 새 키를 쓴다. 삭제한 뒤 다시 등록하는 작업에도 새 키를 사용한다.

PATCH는 `{"med_time":"09:00"}` 또는 `{"is_active":false}`처럼 바꾸는 필드만 보낸다. 한 개 이상 필요하고 명시적 null은 거절한다. 화면에 없는 음성·대기시간 설정을 수정 요청에서 기본값으로 덮어쓰지 않는다.

DELETE는 소프트 삭제이므로 목록에서는 제외되지만 안내 이력은 남는다. 같은 삭제를 반복하면 204, 존재한 적 없는 ID는 404다. 204 응답에는 JSON이 없으므로 본문을 읽으려 하지 않는다. 수정·끄기·삭제 시 해당 일정의 대기 음성 명령은 EXPIRED, 관련 안내 이력은 FAILED로 정리해 옛 설정이 재생되지 않도록 한다.

## 3. 앱 알림과 스피커 안내의 차이

| 구분 | 앱 복약 안내 | 스피커 안내 명령 |
|---|---|---|
| 대상 | 일정 대상에 연결된 SENIOR 본인 계정 | 해당 가구의 게이트웨이 |
| 시작 조건 | 활성 일정의 요일·KST 시간 구간 | 같은 구간에서 120초 이내 최근 활동 수신 |
| 활동 필요 | 필요 없음 | 필요함 |
| 중복 방지 | 본인·일정·KST 날짜별 알림 하나 | 일정·KST 날짜별 안내 이력·명령 하나 |
| 현재 결과 | DB의 MEDICATION_REMINDER·MOCK_QUEUED/NO_TOKEN | PENDING 명령 생성 후 게이트웨이 ACK 대기 |
| 실물 연결 | 실제 휴대폰 발송 미연동 | 실제 스피커 재생 미연동 |

복약 시간 구간은 정한 시각 이상부터 `activity_window_minutes`분이 지나기 전까지다. 예를 들어 08:30·30분이면 08:30 이상 09:00 미만이다. 기본 일일 반복은 모든 요일을 보내야 한다. 앱 알림은 서버가 실행되는 동안의 주기 점검으로 생성하며 서버 중단 중 정시 발송을 보장하지 않는다. 본인 SENIOR 계정 연결이 없으면 본인 앱 알림을 만들 수 없다.

활동 이벤트를 늦게 재전송해도 120초를 넘은 기록은 즉시 음성 명령을 만들지 않는다. 시간 구간이 끝날 때 활동이 없으면 NO_ACTIVITY, 명령이 만료되면 안내 이력은 FAILED다. 게이트웨이는 명령을 조회해 재생 후 SUCCESS/FAILED ACK를 보내야 한다. ACK의 같은 최종 상태 재시도는 허용하고 서로 다른 최종 상태는 409로 거절한다. **음성 안내 SUCCESS는 실제 약 복용 완료를 확인한 값이 아니다.**

## 4. 알림 대상·조회·상세 이동

어르신은 본인 `MEDICATION_REMINDER`만 알림 기록으로 수신한다. 보호자는 연결 대상의 낙상 `FALL_ALERT`, 생활 패턴 `PATTERN_ALERT`를 수신한다. `GET /api/v1/notifications`는 로그인한 사용자 본인의 기록만 반환한다.

복약 deep_link는 `safehome://medications/{schedule_id}`다. 실제 앱에서 이 주소를 어떤 화면에 연결할지는 프론트와 맞춘다. 복약 전용 단건 GET은 없으므로 일정 목록에서 schedule_id를 찾거나 실시간 복약 이벤트의 `medication_name`, `med_time`을 표시한다. 이미 변경·삭제한 일정이면 알림 내용과 현재 일정의 차이를 안내한다.

앱 실시간 `/ws/live`를 어르신 토큰으로 구독하면 `MEDICATION_REMINDER`, `MEDICATION_UPDATED`, `MEDICATION_DELETED`만 전달한다. 알림·이벤트에 포함된 schedule_id로 목록을 갱신한다. 위험·사고·패턴 이벤트는 어르신 실시간 채널에 보내지 않는다. 이는 알림 정책이며 본인 상태 REST 조회 권한과 별개다.

푸시 토큰이 있으면 `MOCK_QUEUED`, 없으면 `NO_TOKEN`을 기록한다. EXPO/FCM은 현재 등록 가능한 제공자 이름이며 실제 전송 어댑터가 아니다. 가상 토큰 등록만으로 휴대폰에 푸시가 도착하지 않는다.

## 5. 내 PC에서 확인하기

1. `safehome-backend` 폴더의 **start-admin-demo.cmd**를 실행하고 http://127.0.0.1:8000/ 를 연다.
2. 조회 권한을 ‘어르신 본인’으로 바꾼다. 본인만 선택되는지 확인한다.
3. 연습용 약 이름·시각을 등록하고 목록에 추가되는지 본다. 끄기·켜기·삭제도 실행한다.
4. 등록한 일정이 서버 재시작 후에도 남는지 확인한다. 가상 데이터 초기화는 누르지 않는다.
5. 현재 KST 시간 구간의 활성 일정에서 복약 알림 기록을 확인한다. 활동 시연은 별도의 스피커 명령 생성 시험이다.
6. 자세한 JSON·요일·음성 변경·다른 사람 접근 거절은 `/docs`의 복약 API와 자동 시험 기록에서 확인한다.

확인 화면은 개발용으로 매일 일정을 등록하는 간단한 입력을 제공한다. 전체 요일 선택·음성 선택을 포함한 실제 프론트 화면의 완성·연동 시험을 뜻하지 않는다. 최신 검증 날짜와 결과는 [TEST_REPORT.md](TEST_REPORT.md)를 참고한다.
