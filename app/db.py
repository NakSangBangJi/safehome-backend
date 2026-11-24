import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4, uuid5, NAMESPACE_URL

UTC = timezone.utc
KST = timezone(timedelta(hours=9))
ADMIN = '00000000-0000-4000-8000-000000000001'
GUARDIAN = '00000000-0000-4000-8000-000000000002'
SENIOR_USER = '00000000-0000-4000-8000-000000000003'
SENIORS = [f'00000000-0000-4000-8000-00000000010{i}' for i in range(1, 5)]


def now():
    return datetime.now(UTC)


def iso(value=None):
    return (value or now()).astimezone(UTC).isoformat(timespec='microseconds').replace('+00:00', 'Z')


def uid():
    return str(uuid4())


def stable(value):
    return str(uuid5(NAMESPACE_URL, 'safehome-m1/' + value))


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def connect(path):
    if path != ':memory:':
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA busy_timeout=5000')
    schema=Path(__file__).with_name('schema.sql').read_text(encoding='utf-8')
    migrate_v1(db,schema)
    db.executescript(schema)
    db.execute('PRAGMA user_version=4')
    return db


def migrate_v1(db,schema):
    """Preserve existing v1 rows while extending CHECK constraints and optional links."""
    old=db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
    if old is None or "'SENIOR'" in old['sql']:
        return
    db.execute('PRAGMA foreign_keys=OFF')
    try:
        with transaction(db):
            for table in ('users','notifications'):
                ddl=re.search(r'CREATE TABLE IF NOT EXISTS '+table+r' \(.*?\n\);',schema,re.S).group(0)
                db.execute(ddl.replace('IF NOT EXISTS '+table,'IF NOT EXISTS '+table+'_v2',1))
                columns=','.join(row['name'] for row in db.execute('PRAGMA table_info('+table+')'))
                db.execute(f'INSERT INTO {table}_v2 ({columns}) SELECT {columns} FROM {table}')
                db.execute('DROP TABLE '+table)
                db.execute('ALTER TABLE '+table+'_v2 RENAME TO '+table)
            db.execute('ALTER TABLE seniors ADD COLUMN user_id TEXT REFERENCES users(user_id)')
            db.execute('CREATE UNIQUE INDEX idx_seniors_user ON seniors(user_id)')
            if db.execute('PRAGMA foreign_key_check').fetchone():
                raise RuntimeError('Foreign key check failed during mock database upgrade')
    finally:
        db.execute('PRAGMA foreign_keys=ON')


@contextmanager
def transaction(db):
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK')
        raise


def one(db, query, args=()):
    row = db.execute(query, args).fetchone()
    return dict(row) if row else None


def many(db, query, args=()):
    return [dict(row) for row in db.execute(query, args).fetchall()]


def insert(db, table, **values):
    # Table and column names are internal constants, never request input.
    db.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))


def feed(db, senior_id, event_type, data, occurred_at=None):
    insert(db, 'event_feed', event_id=uid(), senior_id=senior_id, event_type=event_type,
           occurred_at=occurred_at or iso(), data_json=dumps(data))


def seed(db):
    if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
        with transaction(db):
            seed_senior_account(db)
        return
    t = iso()
    with transaction(db):
        for user_id, account, name, role in [(ADMIN,'admin-demo','시연 관리자','ADMIN'), (GUARDIAN,'guardian-demo','시연 보호자','GUARDIAN')]:
            insert(db, 'users', user_id=user_id, account_id=account, user_name=name, role=role, created_at=t)
        for voice_id, label, text in [('TYPE_A','기본 안내','복약 시간입니다. 약을 챙겨 드세요.'),('TYPE_B','친근한 안내','약 드실 시간이에요. 물과 함께 챙겨 드세요.')]:
            insert(db, 'voice_assets', voice_id=voice_id, label=label, text=text, is_active=1)
        for index, senior_id in enumerate(SENIORS):
            risk = ['NORMAL','CAUTION','DANGER','NORMAL'][index]
            insert(db, 'seniors', senior_id=senior_id, name=['김안심(가상)','이주의(가상)','박위험(가상)','최빈목록(가상)'][index],
                   timezone='Asia/Seoul', current_risk_level=risk, last_status_updated_at=t, status_event_id=None)
            if index < 3:
                insert(db, 'senior_guardians', senior_id=senior_id, guardian_id=GUARDIAN, relationship='자녀', is_primary=1)
            gateway = stable(senior_id + '/GATEWAY')
            for kind in ['GATEWAY','PIEZO_MAT','TOF_SENSOR','CAMERA','SPEAKER']:
                insert(db, 'devices', device_id=stable(senior_id + '/' + kind), senior_id=senior_id,
                       parent_device_id=None if kind == 'GATEWAY' else gateway, device_type=kind,
                       connection_status='ONLINE' if index != 3 else 'OFFLINE', last_heartbeat_at=t if index != 3 else None, created_at=t)
            if index in (1,2):
                event_id, incident_id, log_id = stable(senior_id+'/seed-event'), stable(senior_id+'/incident'), stable(senior_id+'/sensor')
                insert(db, 'ingested_events', event_id=event_id, device_id=gateway, senior_id=senior_id, event_type='RISK', occurred_at=t, received_at=t, payload_json='{}')
                insert(db, 'sensor_logs', log_id=log_id, event_id=event_id, piezo_impact_val=830.0, tof_estimated_height_mm=210, ai_fall_confidence=0.94)
                insert(db, 'incidents', incident_id=incident_id, event_id=event_id, senior_id=senior_id, event_time=t, risk_level=risk,
                       summary='모의 센서·AI 낙상 판정', status='OPEN', created_at=t)
                insert(db, 'incident_details', incident_id=incident_id, sensor_log_id=log_id, gesture_cancelled=0, snapshot_image_url=None)
                db.execute('UPDATE seniors SET status_event_id=? WHERE senior_id=?', (event_id,senior_id))
            feed(db, senior_id, 'STATUS_CHANGED', {'risk_level':risk,'last_updated_at':t})
        insert(db, 'medication_schedules', schedule_id=stable('seed-medication'), senior_id=SENIORS[0], medication_name='아침약',
               med_time='08:30', days_of_week_json=dumps(['MON','TUE','WED','THU','FRI','SAT','SUN']), voice_id='TYPE_A',
               activity_window_minutes=30, is_active=1, created_at=t, updated_at=t, deleted_at=None)
        seed_senior_account(db)


def seed_senior_account(db):
    db.execute("INSERT OR IGNORE INTO users(user_id,account_id,user_name,role,created_at) VALUES(?,?,?,'SENIOR',?)",(SENIOR_USER,'senior-demo','시연 어르신',iso()))
    db.execute('UPDATE seniors SET user_id=? WHERE senior_id=? AND user_id IS NULL',(SENIOR_USER,SENIORS[0]))
