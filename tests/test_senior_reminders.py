import sqlite3
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db import KST,SENIOR_USER,SENIORS,connect,iso,one,seed,now
from app.main import create_app,maintenance
from test_contract import ADMIN,GUARD,client,med_body,scenario

SELF={'Authorization':'Bearer senior-demo-token'}
S1,S2=SENIORS[:2]


def due_schedule(client,**changes):
    local=now().astimezone(KST)
    body=med_body(med_time=(local-timedelta(minutes=1)).strftime('%H:%M'),days_of_week=['MON','TUE','WED','THU','FRI','SAT','SUN'],**changes)
    r=client.post(f'/api/v1/seniors/{S1}/medications',headers={**GUARD,'Idempotency-Key':str(uuid4())},json=body)
    assert r.status_code==201,r.text
    return r.json()


def test_senior_login_scope_and_own_schedule_crud(client):
    login=client.post('/api/v1/auth/mock-login',json={'account_id':'senior-demo'})
    assert login.status_code==200 and login.json()['role']=='SENIOR'
    assert login.json()['access_token']=='senior-demo-token'
    assert [s['senior_id'] for s in client.get('/api/v1/seniors',headers=SELF).json()['items']]==[S1]
    assert client.get(f'/api/v1/seniors/{S2}/medications',headers=SELF).status_code==403
    url=f'/api/v1/seniors/{S1}/medications'
    headers={**SELF,'Idempotency-Key':str(uuid4())}
    created=client.post(url,headers=headers,json=med_body())
    assert created.status_code==201,created.text
    schedule=created.json()
    assert client.post(url,headers=headers,json=med_body()).json()==schedule
    path='/api/v1/medications/'+schedule['schedule_id']
    changed=client.patch(path,headers=SELF,json={'med_time':'09:15','is_active':False})
    assert changed.status_code==200 and changed.json()['med_time']=='09:15'
    assert changed.json()['is_active'] is False
    assert any(m['schedule_id']==schedule['schedule_id'] for m in client.get(url,headers=GUARD).json())
    assert client.delete(path,headers=SELF).status_code==204
    assert all(m['schedule_id']!=schedule['schedule_id'] for m in client.get(url,headers=SELF).json())


def test_senior_cannot_write_other_seniors_medications(client):
    url=f'/api/v1/seniors/{S2}/medications'
    other=client.post(url,headers={**GUARD,'Idempotency-Key':str(uuid4())},json=med_body())
    assert other.status_code==201
    path='/api/v1/medications/'+other.json()['schedule_id']
    assert client.post(url,headers={**SELF,'Idempotency-Key':str(uuid4())},json=med_body()).status_code==403
    assert client.patch(path,headers=SELF,json={'med_time':'01:00'}).status_code==403
    assert client.delete(path,headers=SELF).status_code==403
    assert client.get(url,headers=GUARD).json()==[other.json()]
    assert client.post(f'/api/v1/seniors/{S1}/medications',headers={'Idempotency-Key':str(uuid4())},json=med_body()).status_code==401
    assert client.patch(path,json={'med_time':'01:00'}).status_code==401
    assert client.delete(path).status_code==401


def test_time_reminder_senior_only_without_activity(client):
    token={'installation_id':str(uuid4()),'provider':'EXPO','push_token':'ExponentPushToken[senior-only]','device_os':'ANDROID'}
    assert client.post('/api/v1/notifications/token',headers=SELF,json=token).status_code==200
    schedule=due_schedule(client)
    db=client.app.state.db
    for _ in range(3):maintenance(db)
    reminders=client.get('/api/v1/notifications',headers=SELF).json()['items']
    matching=[r for r in reminders if r['schedule_id']==schedule['schedule_id']]
    assert len(matching)==1
    row=matching[0]
    assert row['notification_type']=='MEDICATION_REMINDER' and row['user_id']==SENIOR_USER
    assert row['delivery_status']=='MOCK_QUEUED' and row['source_event_id'] is None
    assert row['deep_link']=='safehome://medications/'+schedule['schedule_id']
    assert client.get('/api/v1/notifications',headers=GUARD).json()['items']==[]
    assert one(db,'SELECT 1 FROM speaker_commands') is None
    assert client.post('/api/v1/mock/medication-tick',headers=ADMIN).json()['generated_count']==0
    assert client.post('/api/v1/mock/medication-tick',headers=SELF).status_code==403


def test_senior_never_receives_risk_or_pattern_alerts(client):
    due_schedule(client)
    maintenance(client.app.state.db)
    scenario(client,'DANGER');scenario(client,'PATTERN')
    rows=client.get('/api/v1/notifications',headers=SELF).json()['items']
    assert rows and all(r['notification_type']=='MEDICATION_REMINDER' for r in rows)
    guard=client.get('/api/v1/notifications',headers=GUARD).json()['items']
    assert {r['notification_type'] for r in guard}=={'FALL_ALERT','PATTERN_ALERT'}
    sync=client.get(f'/api/v1/seniors/{S1}/sync',headers=SELF).json()
    assert all(e['event_type'] in ['MEDICATION_REMINDER','MEDICATION_UPDATED','MEDICATION_DELETED'] for e in sync['events'])
    assert 'MEDICATION_REMINDER' not in [e['event_type'] for e in client.get(f'/api/v1/seniors/{S1}/sync',headers=GUARD).json()['events']]
    with client.websocket_connect('/ws/live') as ws:
        ws.send_json({'action':'SUBSCRIBE','access_token':'senior-demo-token','senior_id':S1,'after_cursor':0})
        assert ws.receive_json()['type']=='SUBSCRIBED'
        observed=[]
        while True:
            message=ws.receive_json()
            if message['type']=='CURSOR':break
            if message['type']=='EVENT':observed.append(message['event_type'])
        assert 'MEDICATION_REMINDER' in observed and 'INCIDENT_ALERT' not in observed and 'PATTERN_UPDATED' not in observed


def test_inactive_deleted_and_outside_window_not_reminded(client):
    inactive=due_schedule(client,is_active=False)
    deleted=due_schedule(client)
    client.delete('/api/v1/medications/'+deleted['schedule_id'],headers=GUARD)
    db=client.app.state.db
    clock=now().astimezone(KST).replace(hour=14,minute=0,second=0,microsecond=0)
    with patch('app.main.now',return_value=clock):
        maintenance(db)
    records=client.get('/api/v1/notifications',headers=SELF).json()['items']
    assert not any(r['schedule_id'] in [inactive['schedule_id'],deleted['schedule_id']] for r in records)


def test_no_token_and_midnight_recovery_window(client):
    local=now().astimezone(KST)
    # The current time is controlled; the previous day's 23:50 window crosses midnight.
    clock=(local+timedelta(days=1)).replace(hour=0,minute=5,second=0,microsecond=0)
    body=med_body(med_time='23:50',days_of_week=['MON','TUE','WED','THU','FRI','SAT','SUN'])
    r=client.post(f'/api/v1/seniors/{S1}/medications',headers={**GUARD,'Idempotency-Key':str(uuid4())},json=body)
    sid=r.json()['schedule_id']
    with patch('app.main.now',return_value=clock):
        maintenance(client.app.state.db)
    row=one(client.app.state.db,'SELECT * FROM notifications WHERE schedule_id=?',(sid,))
    assert row['scheduled_date']==(clock.date()-timedelta(days=1)).isoformat()
    assert row['delivery_status']=='NO_TOKEN'


def test_v1_upgrade_preserves_data(tmp_path):
    source=Path(__file__).resolve().parents[1]/'app/schema.sql'
    schema=source.read_text(encoding='utf-8')
    old=schema.replace("'ADMIN','GUARDIAN','SENIOR'","'ADMIN','GUARDIAN'").replace(' user_id TEXT UNIQUE REFERENCES users(user_id),\n','')
    old=old.replace('source_event_id TEXT REFERENCES ingested_events(event_id),\n schedule_id TEXT REFERENCES medication_schedules(schedule_id), scheduled_date TEXT,','source_event_id TEXT NOT NULL REFERENCES ingested_events(event_id),')
    old=old.replace("'FALL_ALERT','PATTERN_ALERT','MEDICATION_REMINDER'","'FALL_ALERT','PATTERN_ALERT'")
    start=old.index(' UNIQUE(user_id, source_event_id, notification_type),')
    end=old.index('\n);',start)
    old=old[:start]+' UNIQUE(user_id, source_event_id, notification_type)'+old[end:]
    path=str(tmp_path/'v1.sqlite3')
    legacy=sqlite3.connect(path);legacy.executescript(old)
    from app.db import ADMIN as ADMIN_ID,GUARDIAN,insert,stable
    t=iso()
    for user_id,role,account in [(ADMIN_ID,'ADMIN','admin-demo'),(GUARDIAN,'GUARDIAN','guardian-demo')]:
        insert(legacy,'users',user_id=user_id,account_id=account,user_name=account,role=role,created_at=t)
    insert(legacy,'seniors',senior_id=S1,name='기존 사용자',timezone='Asia/Seoul',current_risk_level='NORMAL',last_status_updated_at=t,status_event_id=None)
    legacy.commit();legacy.close()
    db=connect(path);seed(db)
    assert one(db,'SELECT name,user_id FROM seniors WHERE senior_id=?',(S1,))=={'name':'기존 사용자','user_id':SENIOR_USER}
    assert db.execute('PRAGMA foreign_keys').fetchone()[0]==1
    assert not db.execute('PRAGMA foreign_key_check').fetchall()
    assert one(db,'SELECT role FROM users WHERE user_id=?',(SENIOR_USER,))['role']=='SENIOR'
    assert db.execute('PRAGMA user_version').fetchone()[0]==4
    db.close()
    again=connect(path);seed(again);assert again.execute('SELECT COUNT(*) FROM users').fetchone()[0]==3;again.close()
