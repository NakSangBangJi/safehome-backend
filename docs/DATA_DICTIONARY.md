# SQLite 데이터 사전
자료형은 실제 schema.sql 기준입니다. 사용자·어르신·장치·이벤트·일정 등의 고유번호는 UUID 문자열이고 event_feed.cursor는 서버 순번입니다. account_id는 로그인 문자열, voice_id는 TYPE_A/TYPE_B 코드이므로 모든 *_id가 UUID인 것은 아닙니다.
SQL NOT NULL은 실제 DDL 선언이고, PK 식별자 규칙은 앱에서 적용하는 규칙입니다. 일부 TEXT PRIMARY KEY는 NOT NULL이 별도로 선언되어 있지 않으므로 두 항목을 구분합니다. INTEGER PRIMARY KEY는 자동 생성될 수 있습니다.

## users

| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |
|---|---|---|---|---|---|
| user_id | TEXT | 1 |  | 앱필수 |  |
| account_id | TEXT |  | Y |  |  |
| user_name | TEXT |  | Y |  |  |
| password_hash | TEXT |  |  |  |  |
| phone_number | TEXT |  |  |  |  |
| role | TEXT |  | Y |  |  |
| created_at | TEXT |  | Y |  |  |

## seniors

| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |
|---|---|---|---|---|---|
| senior_id | TEXT | 1 |  | 앱필수 |  |
| name | TEXT |  | Y |  |  |
| timezone | TEXT |  | Y |  | 'Asia/Seoul' |
| user_id | TEXT |  |  |  | users.user_id |
| birth_date | TEXT |  |  |  |  |
| address | TEXT |  |  |  |  |
| current_risk_level | TEXT |  | Y |  | 'NORMAL' |
| last_status_updated_at | TEXT |  | Y |  |  |
| status_event_id | TEXT |  |  |  | ingested_events.event_id |

## senior_guardians

| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |
|---|---|---|---|---|---|
| senior_id | TEXT | 1 | Y | 앱필수 | seniors.senior_id |
| guardian_id | TEXT | 2 | Y | 앱필수 | users.user_id |
| relationship | TEXT |  | Y |  |  |
| is_primary | INTEGER |  | Y |  |  |

## devices

| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |
|---|---|---|---|---|---|
| device_id | TEXT | 1 |  | 앱필수 |  |
| senior_id | TEXT |  | Y |  | seniors.senior_id |
| parent_device_id | TEXT |  |  |  | devices.device_id |
| device_type | TEXT |  | Y |  |  |
| connection_status | TEXT |  | Y |  |  |
| last_heartbeat_at | TEXT |  |  |  |  |
| created_at | TEXT |  | Y |  |  |

## device_logs

| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |
|---|---|---|---|---|---|
| log_id | TEXT | 1 |  | 앱필수 |  |
| device_id | TEXT |  | Y |  | devices.device_id |
| status | TEXT |  | Y |  |  |
| reason | TEXT |  | Y |  |  |
| occurred_at | TEXT |  | Y |  |  |

## ingested_events

| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |
|---|---|---|---|---|---|
| event_id | TEXT | 1 |  | 앱필수 |  |
| device_id | TEXT |  | Y |  | devices.device_id |
| senior_id | TEXT |  | Y |  | seniors.senior_id |
| event_type | TEXT |  | Y |  |  |
| occurred_at | TEXT |  | Y |  |  |
| received_at | TEXT |  | Y |  |  |
| payload_json | TEXT |  | Y |  |  |

## sensor_logs

| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |
|---|---|---|---|---|---|
| log_id | TEXT | 1 |  | 앱필수 |  |
| event_id | TEXT |  | Y |  | ingested_events.event_id |
| piezo_impact_val | REAL |  |  |  |  |
| tof_estimated_height_mm | INTEGER |  |  |  |  |
| ai_fall_confidence | REAL |  |  |  |  |

## incidents

| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |
|---|---|---|---|---|---|
| incident_id | TEXT | 1 |  | 앱필수 |  |
| event_id | TEXT |  | Y |  | ingested_events.event_id |
| senior_id | TEXT |  | Y |  | seniors.senior_id |
| event_time | TEXT |  | Y |  |  |
| risk_level | TEXT |  | Y |  |  |
| summary | TEXT |  | Y |  |  |
| status | TEXT |  | Y |  |  |
| created_at | TEXT |  | Y |  |  |

## incident_details

| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |
|---|---|---|---|---|---|
| incident_id | TEXT | 1 |  | 앱필수 | incidents.incident_id |
| sensor_log_id | TEXT |  | Y |  | sensor_logs.log_id |
| gesture_cancelled | INTEGER |  | Y |  |  |
| snapshot_image_url | TEXT |  |  |  |  |

## voice_assets

| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |
|---|---|---|---|---|---|
| voice_id | TEXT | 1 |  | 앱필수 |  |
| label | TEXT |  | Y |  |  |
| text | TEXT |  | Y |  |  |
| is_active | INTEGER |  | Y |  |  |

## medication_schedules

| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |
|---|---|---|---|---|---|
| schedule_id | TEXT | 1 |  | 앱필수 |  |
| senior_id | TEXT |  | Y |  | seniors.senior_id |
| medication_name | TEXT |  | Y |  |  |
| med_time | TEXT |  | Y |  |  |
| days_of_week_json | TEXT |  | Y |  |  |
| voice_id | TEXT |  | Y |  | voice_assets.voice_id |
| activity_window_minutes | INTEGER |  | Y |  |  |
| is_active | INTEGER |  | Y |  |  |
| created_at | TEXT |  | Y |  |  |
| updated_at | TEXT |  | Y |  |  |
| deleted_at | TEXT |  |  |  |  |

## medication_logs

| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |
|---|---|---|---|---|---|
| log_id | TEXT | 1 |  | 앱필수 |  |
| schedule_id | TEXT |  | Y |  | medication_schedules.schedule_id |
| source_event_id | TEXT |  |  |  | ingested_events.event_id |
| scheduled_date | TEXT |  | Y |  |  |
| activity_detected_at | TEXT |  |  |  |  |
| voice_played_at | TEXT |  |  |  |  |
| play_status | TEXT |  | Y |  |  |

## speaker_commands

| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |
|---|---|---|---|---|---|
| command_id | TEXT | 1 |  | 앱필수 |  |
| device_id | TEXT |  | Y |  | devices.device_id |
| log_id | TEXT |  | Y |  | medication_logs.log_id |
| voice_id | TEXT |  | Y |  | voice_assets.voice_id |
| status | TEXT |  | Y |  |  |
| created_at | TEXT |  | Y |  |  |
| expires_at | TEXT |  | Y |  |  |
| acknowledged_at | TEXT |  |  |  |  |

## lifestyle_patterns

| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |
|---|---|---|---|---|---|
| pattern_id | TEXT | 1 |  | 앱필수 |  |
| event_id | TEXT |  | Y |  | ingested_events.event_id |
| senior_id | TEXT |  | Y |  | seniors.senior_id |
| pattern_type | TEXT |  | Y |  |  |
| observation_start_time | TEXT |  | Y |  |  |
| observation_end_time | TEXT |  | Y |  |  |
| metric_value | REAL |  | Y |  |  |
| metric_unit | TEXT |  | Y |  |  |
| summary_note | TEXT |  | Y |  |  |
| is_anomaly | INTEGER |  | Y |  |  |
| created_at | TEXT |  | Y |  |  |

## push_tokens

| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |
|---|---|---|---|---|---|
| token_id | TEXT | 1 |  | 앱필수 |  |
| user_id | TEXT |  | Y |  | users.user_id |
| installation_id | TEXT |  | Y |  |  |
| provider | TEXT |  | Y |  |  |
| push_token | TEXT |  | Y |  |  |
| device_os | TEXT |  | Y |  |  |
| updated_at | TEXT |  | Y |  |  |

## notifications

| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |
|---|---|---|---|---|---|
| notification_id | TEXT | 1 |  | 앱필수 |  |
| senior_id | TEXT |  | Y |  | seniors.senior_id |
| user_id | TEXT |  | Y |  | users.user_id |
| source_event_id | TEXT |  |  |  | ingested_events.event_id |
| schedule_id | TEXT |  |  |  | medication_schedules.schedule_id |
| scheduled_date | TEXT |  |  |  |  |
| notification_type | TEXT |  | Y |  |  |
| title | TEXT |  | Y |  |  |
| body | TEXT |  | Y |  |  |
| deep_link | TEXT |  | Y |  |  |
| delivery_status | TEXT |  | Y |  |  |
| created_at | TEXT |  | Y |  |  |

## event_feed

| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |
|---|---|---|---|---|---|
| cursor | INTEGER | 1 |  | 자동 |  |
| event_id | TEXT |  | Y |  |  |
| senior_id | TEXT |  | Y |  | seniors.senior_id |
| event_type | TEXT |  | Y |  |  |
| occurred_at | TEXT |  | Y |  |  |
| data_json | TEXT |  | Y |  |  |

## idempotency_keys

| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |
|---|---|---|---|---|---|
| principal_id | TEXT | 1 | Y | 앱필수 | users.user_id |
| route_key | TEXT | 2 | Y | 앱필수 |  |
| idempotency_key | TEXT | 3 | Y | 앱필수 |  |
| request_hash | TEXT |  | Y |  |  |
| response_json | TEXT |  | Y |  |  |
| created_at | TEXT |  | Y |  |  |

## auth_sessions

| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |
|---|---|---|---|---|---|
| session_id | TEXT | 1 | Y | 앱필수 |  |
| user_id | TEXT |  | Y |  | users.user_id |
| token_hash | TEXT |  | Y |  |  |
| created_at | TEXT |  | Y |  |  |
| expires_at | TEXT |  | Y |  |  |
| revoked_at | TEXT |  |  |  |  |

## auth_login_attempts

| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |
|---|---|---|---|---|---|
| scope_key | TEXT | 1 | Y | 앱필수 |  |
| fail_count | INTEGER |  | Y |  |  |
| window_started_at | TEXT |  | Y |  |  |

## incident_assessment_events

| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |
|---|---|---|---|---|---|
| assessment_seq | INTEGER | 1 |  | 자동 |  |
| event_id | TEXT |  | Y |  | ingested_events.event_id |
| incident_id | TEXT |  | Y |  | incidents.incident_id |
| applied | INTEGER |  | Y |  |  |
