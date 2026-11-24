# 5조 안심 서비스 백엔드

기준일: **2026-11-22** · 실행 버전: **1.5.0** · 중간발표 범위: **B01~B05 설계·Mock 단계**

센서·AI가 보낼 판단 결과를 가상 데이터로 입력하고, 실제 SQLite DB에 저장한 뒤 앱·웹용 API로 확인하는 개발용 서버입니다. 현재 REST **36개 작업(34개 경로)**과 WebSocket **2개 채널**, SQLite **21개 테이블·141개 컬럼**을 제공합니다. Mock은 가상 입력과 가상 알림을 뜻하며, 저장·권한 검사·조회·복약 일정 변경은 실제 코드로 동작합니다.

## 1. 서버 켜고 확인하기

이미 설치한 이 PC에서는 `safehome-backend` 폴더의 **start-admin-demo.cmd**를 더블클릭합니다. 검은 실행 창은 열어 둡니다. PowerShell에서 켜려면 같은 폴더에서 다음 한 줄을 입력합니다.

```powershell
.\.venv\Scripts\python.exe run.py
```

브라우저에서 아래 주소를 엽니다. 이 주소는 서버를 켠 내 PC에서 사용합니다.

| 볼 내용 | 주소 |
|---|---|
| 상태·위험 시연·복약 일정 확인 | http://127.0.0.1:8000/ |
| 관리자 전체 현황·사고·실시간 경고·카메라 모의 시험 | http://127.0.0.1:8000/admin-test |
| API를 펼쳐 직접 호출하는 Swagger | http://127.0.0.1:8000/docs |
| 서버 구성도·ERD·설계 문서 | http://127.0.0.1:8000/design/ |
| 서버 실행 상태 | http://127.0.0.1:8000/health |
| 실행 중인 서버의 전체 OpenAPI | http://127.0.0.1:8000/openapi.json |

코드를 바꿨다면 실행 창에서 Ctrl+C로 끄고 다시 켠 뒤 브라우저를 새로고침합니다. 종료할 때도 Ctrl+C를 누릅니다. `사이트에 연결할 수 없음`이면 먼저 실행 창에 `Uvicorn running on http://127.0.0.1:8000`이 있는지 확인합니다.

비밀번호 로그인까지 연습하려면 **start-login-test.cmd**를 실행하고 http://127.0.0.1:8000/login-test 를 엽니다. 처음 실행할 때 준비하는 연습용 비밀번호 파일은 개인 PC에서만 확인하며 발표·팀 공유 자료에 포함하지 않습니다. [로그인 사용 설명](docs/LOGIN_GUIDE.md)에 자세한 순서가 있습니다.

## 2. 현재 완료 범위 읽는 법

| 구분 | 현재 상태 | 남은 확인 |
|---|---|---|
| B01 서버 구조·핵심 기능 정의 | 역할, 서버 모듈, 저장·조회·알림 경계 문서화 | 실제 부품·배포 환경이 정해지면 변경 반영 |
| B02 게이트웨이·AI·프론트 규격 협의 | 요청·응답·권한·오류의 실행 가능한 초안, 회신 반영 | AI 실제 출력 의미와 카메라 최종 결과 처리 등 팀 최종 합의 |
| B03 SQLite DB ERD 설계 | 현재 코드 기준 21개 테이블·141개 컬럼, PK·FK·제약·ERD | 실데이터를 사용한 통합 시험과 보관 정책 |
| B04 FastAPI 서버·DB 실행 환경 | 개인 Windows PC에서 단일 서버·SQLite 실행 구성 | 팀 기기 접속·호스팅·배포 환경 검증 |
| B05 Swagger/Mock API·모의 응답 | Swagger·예시·가상 계정·시연 화면 제공 | 실제 프론트 화면과 공동 연동 시험 |

로그인, HTTP 모의 수신, 복약 엔진, 관리자 통합 조회·실시간 경고, 카메라 재검증 처리는 후속 작업을 미리 구현한 내용입니다. **실제 하드웨어·AI·프론트 전체 통합 완료와는 구분합니다.** MQTT 수신, FCM/Expo 실제 휴대폰 발송, 실제 스피커 재생은 아직 연결하지 않았습니다.

## 3. 자료는 여기에서 확인하기

| 필요한 내용 | 현재 자료 |
|---|---|
| 담당 경계·구조·현재 기능·DB 설계 이유 | [DESIGN.md](docs/DESIGN.md) |
| REST 전체 목록·WebSocket·권한·오류 | [API_SPEC.md](docs/API_SPEC.md) |
| 요청·응답 예시 | [EXAMPLES.md](docs/EXAMPLES.md), [examples.json](docs/examples.json) |
| 전체 자료형·필수값 | [openapi.json](docs/openapi.json) |
| DB 요약 / 전체 관계 / 전체 필드 | [erd.svg](docs/erd.svg) / [erd-relationships.svg](docs/erd-relationships.svg) / [erd-detailed.svg](docs/erd-detailed.svg) |
| 서버 구성도 | [architecture.svg](docs/architecture.svg) |
| DB 필드·PK·FK·NULL 규칙 | [DATA_DICTIONARY.md](docs/DATA_DICTIONARY.md) |
| 실제 DB를 만드는 SQL | [app/schema.sql](app/schema.sql) |
| 어르신 복약 권한·앱 알림·음성 명령 | [SENIOR_MEDICATION.md](docs/SENIOR_MEDICATION.md) |
| 관리자 목록·전체 경고·재연결 | [ADMIN_OVERVIEW.md](docs/ADMIN_OVERVIEW.md), [ADMIN_LIVE.md](docs/ADMIN_LIVE.md) |
| 판단 기록 없는 초기 상태 | [INITIAL_STATUS.md](docs/INITIAL_STATUS.md) |
| 카메라 단계별 결과 제안 규격 | [CAMERA_REVIEW.md](docs/CAMERA_REVIEW.md) |
| 검증 결과와 실제 확인 범위 | [TEST_REPORT.md](docs/TEST_REPORT.md) |
| 프론트 호출 코드·자료형 | [api.ts](frontend/api.ts), [schemas.ts](frontend/schemas.ts) |
| 가상 사용자·대상·장치 식별자 | [fixtures.json](docs/fixtures.json) |

실제 DB 기본 위치는 **data/mock.sqlite3**입니다. 이 파일은 실행 후 만들어지며 일정·사고·원본 이벤트·알림 기록이 저장됩니다. [ERD](docs/erd.svg)는 표와 관계를 설명하는 그림이고, SQL은 DB 구조를 만드는 정의입니다. 초기 상태의 `status_event_id=null`은 아직 판단 기록이 없다는 뜻입니다. 기본 `NORMAL`을 실제 AI의 안전 판정으로 설명하지 않습니다.

## 4. 화면에서 볼 수 있는 기능

1. 첫 화면에서 보호자·어르신 본인·관리자 가상 계정을 바꾸고 접근 가능한 대상을 확인합니다. 보호자는 연결된 3명, 어르신은 본인 1명, 관리자는 전체 4명입니다.
2. 정상·주의·위험 시연 후 상태와 사고 상세를 확인합니다. 장치 끊김은 마지막 위험 단계를 유지하고 연결 상태만 바꿉니다.
3. 보호자 또는 어르신 본인으로 복약 일정을 등록·켜기·끄기·삭제합니다. 약 이름·시각·요일 변경은 `/docs`의 PATCH API에서 확인합니다. 어르신은 다른 사람의 일정에 접근할 수 없습니다.
4. 알림 기록의 `MOCK_QUEUED`·`NO_TOKEN`을 확인합니다. 이는 DB 기록이며 실제 휴대폰 발송 성공이 아닙니다.
5. 관리자 화면에서 전체 대상·사고 목록·실시간 경고를 확인하고, 카메라 ‘의심 시작 → 낙상 확정/오탐 취소/실패’를 모의 입력합니다.

시연 버튼은 관리자 모의 입력 API를 사용하며 화면의 조회 계정 권한과 별도로 동작합니다. 하트비트가 90초 넘게 없으면 등록 장치는 OFFLINE이 될 수 있습니다. ‘가상 데이터 초기화’는 일정·시연 이력·등록 토큰 등 개발용 데이터를 초기화하므로 발표 증빙 저장 후 사용합니다.

## 5. 처음 설치하는 PC

Python 3.12를 준비한 뒤 `safehome-backend` 폴더의 PowerShell에서 순서대로 입력합니다. 첫 설치에는 인터넷이 필요합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe run.py
```

실행 시에는 Python 환경이 설치되어 있다면 내 PC의 `127.0.0.1` 확인에 Wi-Fi가 필요하지 않습니다. 다른 PC·휴대폰에서 접속하려면 같은 네트워크에서 서버를 `--host 0.0.0.0`으로 실행하고 서버 PC의 실제 IPv4 주소를 사용합니다.

```powershell
.\.venv\Scripts\python.exe run.py --host 0.0.0.0 --port 8000
```

휴대폰의 `127.0.0.1`은 휴대폰 자신입니다. 프론트가 다른 웹 주소에서 실행되면 `CORS_ORIGINS`도 맞춰야 합니다. 기본 허용 주소는 `localhost:3000`, `localhost:5173`, `localhost:8081`, `127.0.0.1:5173`(모두 http)입니다. 새 주소는 실행 전에 쉼표로 구분해 설정합니다.

```powershell
$env:CORS_ORIGINS='http://localhost:5173,http://192.168.0.20:5173'
.\.venv\Scripts\python.exe run.py --host 0.0.0.0
```

DB 경로는 `DATABASE_PATH`로 바꿀 수 있습니다. Windows/Python 3.12의 개인 PC 실행을 기준으로 합니다. Docker 파일은 제공하지만 실제 배포·성능·다른 OS 동작을 검증했다고 주장하지 않습니다. 현재는 워커 1개로 실행합니다.

## 6. 검사와 명세 갱신

자동 검사는 **check-backend.cmd**로 실행하거나 다음 명령을 사용합니다. 2026-11-22 재검사에서 87개가 통과했고 경고 1개가 있었습니다. 기능 시험 결과이며 실제 장치·전체 통합·부하 시험을 뜻하지 않습니다. 상세 범위는 [TEST_REPORT.md](docs/TEST_REPORT.md)에서 확인합니다.

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

코드의 요청·응답을 바꾼 뒤 계약 자료를 다시 만들 때는 다음 명령을 사용합니다. 메모리 시험 DB를 사용하며 실행 중인 개인 DB를 초기화하지 않습니다.

```powershell
.\.venv\Scripts\python.exe tools/export_contract.py
```

OpenAPI·예시·자료형·데이터 사전·Mermaid를 갱신한 뒤 SVG도 현재 스키마와 맞춰 확인합니다.

## 7. GitHub에서 팀원과 공유하기

저장소에는 서버 코드, 자동시험, 프론트 호출 예시와 최신 설계·ERD·검증 증빙을 담습니다. 개인 PC의 실행 DB, 연습용 비밀번호 파일, 가상환경, 임시 로그는 포함하지 않습니다. 팀원 PC에서는 5번의 설치 순서로 실행하면 새 가상 DB가 생성됩니다. 비밀번호 로그인 확인이 필요하면 `start-login-test.cmd`를 실행해 본인 PC에 연습 계정을 준비합니다.

DB 편집 사이트에서 전체 ERD를 열려면 [drawDB 가져오기 파일](docs/latest-drawdb.json)을 저장한 뒤 drawDB의 JSON 가져오기로 엽니다. 전체 21개 표·141개 항목·33개 외래키가 들어 있습니다. [요약 그림](docs/erd.svg)은 핵심 9개 표만 표시하므로 전체 설계 검토에는 [전체 필드 그림](docs/erd-detailed.svg)과 [SQL](app/schema.sql)을 함께 확인합니다.

화면에 보이는 이름·사고·장치 기록은 개발용 가상 데이터입니다. 실제 센서·AI·앱 전체 연동, 휴대폰 푸시 발송, 스피커 재생은 후속 공동시험 대상입니다.
