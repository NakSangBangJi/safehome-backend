PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
 user_id TEXT PRIMARY KEY, account_id TEXT NOT NULL UNIQUE, user_name TEXT NOT NULL,
 password_hash TEXT, phone_number TEXT,
 role TEXT NOT NULL CHECK(role IN ('ADMIN','GUARDIAN','SENIOR')), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seniors (
 senior_id TEXT PRIMARY KEY, name TEXT NOT NULL, timezone TEXT NOT NULL DEFAULT 'Asia/Seoul',
 user_id TEXT UNIQUE REFERENCES users(user_id),
 birth_date TEXT, address TEXT,
 current_risk_level TEXT NOT NULL DEFAULT 'NORMAL' CHECK(current_risk_level IN ('NORMAL','CAUTION','DANGER')),
 last_status_updated_at TEXT NOT NULL, status_event_id TEXT REFERENCES ingested_events(event_id)
);
CREATE TABLE IF NOT EXISTS senior_guardians (
 senior_id TEXT NOT NULL REFERENCES seniors(senior_id), guardian_id TEXT NOT NULL REFERENCES users(user_id),
 relationship TEXT NOT NULL, is_primary INTEGER NOT NULL CHECK(is_primary IN (0,1)),
 PRIMARY KEY(senior_id, guardian_id)
);
CREATE TABLE IF NOT EXISTS devices (
 device_id TEXT PRIMARY KEY, senior_id TEXT NOT NULL REFERENCES seniors(senior_id),
 parent_device_id TEXT REFERENCES devices(device_id),
 device_type TEXT NOT NULL CHECK(device_type IN ('GATEWAY','PIEZO_MAT','TOF_SENSOR','CAMERA','SPEAKER')),
 connection_status TEXT NOT NULL CHECK(connection_status IN ('ONLINE','OFFLINE')),
 last_heartbeat_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS device_logs (
 log_id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(device_id),
 status TEXT NOT NULL CHECK(status IN ('ONLINE','OFFLINE')), reason TEXT NOT NULL, occurred_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingested_events (
 event_id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(device_id),
 senior_id TEXT NOT NULL REFERENCES seniors(senior_id), event_type TEXT NOT NULL,
 occurred_at TEXT NOT NULL, received_at TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sensor_logs (
 log_id TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE REFERENCES ingested_events(event_id),
 piezo_impact_val REAL CHECK(piezo_impact_val >= 0), tof_estimated_height_mm INTEGER CHECK(tof_estimated_height_mm >= 0),
 ai_fall_confidence REAL CHECK(ai_fall_confidence BETWEEN 0 AND 1)
);
CREATE TABLE IF NOT EXISTS incidents (
 incident_id TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE REFERENCES ingested_events(event_id),
 senior_id TEXT NOT NULL REFERENCES seniors(senior_id), event_time TEXT NOT NULL,
 risk_level TEXT NOT NULL CHECK(risk_level IN ('CAUTION','DANGER')), summary TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('OPEN','CANCELLED')), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incident_details (
 incident_id TEXT PRIMARY KEY REFERENCES incidents(incident_id),
 sensor_log_id TEXT NOT NULL REFERENCES sensor_logs(log_id), gesture_cancelled INTEGER NOT NULL CHECK(gesture_cancelled IN (0,1)),
 snapshot_image_url TEXT
);
CREATE TABLE IF NOT EXISTS voice_assets (
 voice_id TEXT PRIMARY KEY, label TEXT NOT NULL, text TEXT NOT NULL, is_active INTEGER NOT NULL CHECK(is_active IN (0,1))
);
CREATE TABLE IF NOT EXISTS medication_schedules (
 schedule_id TEXT PRIMARY KEY, senior_id TEXT NOT NULL REFERENCES seniors(senior_id),
 medication_name TEXT NOT NULL, med_time TEXT NOT NULL, days_of_week_json TEXT NOT NULL CHECK(json_valid(days_of_week_json)),
 voice_id TEXT NOT NULL REFERENCES voice_assets(voice_id), activity_window_minutes INTEGER NOT NULL CHECK(activity_window_minutes BETWEEN 1 AND 180),
 is_active INTEGER NOT NULL CHECK(is_active IN (0,1)), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS medication_logs (
 log_id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL REFERENCES medication_schedules(schedule_id),
 source_event_id TEXT REFERENCES ingested_events(event_id),
 scheduled_date TEXT NOT NULL, activity_detected_at TEXT,
 voice_played_at TEXT, play_status TEXT NOT NULL CHECK(play_status IN ('PENDING','SUCCESS','NO_ACTIVITY','FAILED')),
 UNIQUE(schedule_id, scheduled_date)
);
CREATE TABLE IF NOT EXISTS speaker_commands (
 command_id TEXT PRIMARY KEY, device_id TEXT NOT NULL REFERENCES devices(device_id),
 log_id TEXT NOT NULL UNIQUE REFERENCES medication_logs(log_id), voice_id TEXT NOT NULL REFERENCES voice_assets(voice_id),
 status TEXT NOT NULL CHECK(status IN ('PENDING','SUCCESS','FAILED','EXPIRED')),
 created_at TEXT NOT NULL, expires_at TEXT NOT NULL, acknowledged_at TEXT
);
CREATE TABLE IF NOT EXISTS lifestyle_patterns (
 pattern_id TEXT PRIMARY KEY, event_id TEXT NOT NULL UNIQUE REFERENCES ingested_events(event_id),
 senior_id TEXT NOT NULL REFERENCES seniors(senior_id),
 pattern_type TEXT NOT NULL CHECK(pattern_type IN ('NIGHT_ACTIVITY_SURGE','PROLONGED_LYING','INACTIVE')),
 observation_start_time TEXT NOT NULL, observation_end_time TEXT NOT NULL, metric_value REAL NOT NULL CHECK(metric_value >= 0),
 metric_unit TEXT NOT NULL CHECK(metric_unit IN ('count','minutes')), summary_note TEXT NOT NULL,
 is_anomaly INTEGER NOT NULL CHECK(is_anomaly IN (0,1)), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS push_tokens (
 token_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id), installation_id TEXT NOT NULL,
 provider TEXT NOT NULL CHECK(provider IN ('EXPO','FCM')), push_token TEXT NOT NULL,
 device_os TEXT NOT NULL CHECK(device_os IN ('ANDROID','IOS')), updated_at TEXT NOT NULL,
 UNIQUE(user_id, installation_id), UNIQUE(provider, push_token)
);
CREATE TABLE IF NOT EXISTS notifications (
 notification_id TEXT PRIMARY KEY, senior_id TEXT NOT NULL REFERENCES seniors(senior_id),
 user_id TEXT NOT NULL REFERENCES users(user_id), source_event_id TEXT REFERENCES ingested_events(event_id),
 schedule_id TEXT REFERENCES medication_schedules(schedule_id), scheduled_date TEXT,
 notification_type TEXT NOT NULL CHECK(notification_type IN ('FALL_ALERT','PATTERN_ALERT','MEDICATION_REMINDER')),
 title TEXT NOT NULL, body TEXT NOT NULL, deep_link TEXT NOT NULL,
 delivery_status TEXT NOT NULL CHECK(delivery_status IN ('MOCK_QUEUED','NO_TOKEN')), created_at TEXT NOT NULL,
 UNIQUE(user_id, source_event_id, notification_type),
 UNIQUE(user_id, schedule_id, scheduled_date, notification_type),
 CHECK((notification_type='MEDICATION_REMINDER' AND schedule_id IS NOT NULL AND scheduled_date IS NOT NULL AND source_event_id IS NULL)
    OR (notification_type IN ('FALL_ALERT','PATTERN_ALERT') AND source_event_id IS NOT NULL AND schedule_id IS NULL AND scheduled_date IS NULL))
);
CREATE TABLE IF NOT EXISTS event_feed (
 cursor INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
 senior_id TEXT NOT NULL REFERENCES seniors(senior_id), event_type TEXT NOT NULL,
 occurred_at TEXT NOT NULL, data_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency_keys (
 principal_id TEXT NOT NULL REFERENCES users(user_id), route_key TEXT NOT NULL, idempotency_key TEXT NOT NULL,
 request_hash TEXT NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(principal_id, route_key, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_incidents_senior_time ON incidents(senior_id, event_time DESC, incident_id DESC);
CREATE INDEX IF NOT EXISTS idx_devices_senior ON devices(senior_id);
CREATE INDEX IF NOT EXISTS idx_events_senior_time ON ingested_events(senior_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_patterns_senior_time ON lifestyle_patterns(senior_id, observation_start_time);
CREATE INDEX IF NOT EXISTS idx_feed_senior_cursor ON event_feed(senior_id, cursor);
CREATE INDEX IF NOT EXISTS idx_schedules_senior ON medication_schedules(senior_id, deleted_at, med_time);
CREATE INDEX IF NOT EXISTS idx_commands_device ON speaker_commands(device_id, status, expires_at);
CREATE TABLE IF NOT EXISTS auth_sessions (
  session_id TEXT PRIMARY KEY NOT NULL,
  user_id TEXT NOT NULL REFERENCES users(user_id),
  token_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry ON auth_sessions(expires_at);
CREATE TABLE IF NOT EXISTS auth_login_attempts (
  scope_key TEXT PRIMARY KEY NOT NULL,
  fail_count INTEGER NOT NULL,
  window_started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incident_assessment_events (
 assessment_seq INTEGER PRIMARY KEY AUTOINCREMENT,
 event_id TEXT NOT NULL UNIQUE REFERENCES ingested_events(event_id),
 incident_id TEXT NOT NULL REFERENCES incidents(incident_id),
 applied INTEGER NOT NULL CHECK(applied IN (0,1))
);
CREATE INDEX IF NOT EXISTS idx_assessments_incident_seq ON incident_assessment_events(incident_id,assessment_seq);
