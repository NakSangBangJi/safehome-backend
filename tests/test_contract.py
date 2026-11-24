from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import KST, SENIORS, iso, now, one, stable
from app.main import create_app, maintenance

S1,S2,S3,S4=SENIORS
GATEWAY=stable(S1+'/GATEWAY')
GUARD={'Authorization':'Bearer guardian-demo-token'}
ADMIN={'Authorization':'Bearer admin-demo-token'}
DEVICE={'Authorization':'Bearer gateway-demo-token'}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(str(tmp_path/'test.db'),run_maintenance=False)) as client:
        yield client


def med_body(**changes):
    return {'medication_name':'저녁약','med_time':'20:30','days_of_week':['MON','FRI'],'voice_id':'TYPE_A',**changes}


def event(**changes):
    return {'event_id':str(uuid4()),'senior_id':S1,'device_id':GATEWAY,'occurred_at':iso(),**changes}


def scenario(client,kind,senior=S1):
    r=client.post('/api/v1/mock/scenarios',headers=ADMIN,json={'senior_id':senior,'scenario':kind})
    assert r.status_code==200,r.text
    return r.json()


def test_openapi_auth_and_errors(client):
    spec=client.get('/openapi.json').json()
    assert spec['openapi'].startswith('3.1')
    assert len(spec['paths'])>=24
    r=client.post('/api/v1/auth/mock-login',json={'account_id':'guardian-demo'})
    assert r.status_code==200 and r.json()['access_token']=='guardian-demo-token'
    assert len(client.get('/api/v1/seniors',headers=GUARD).json()['items'])==3
    assert len(client.get('/api/v1/seniors',headers=ADMIN).json()['items'])==4
    for path,headers,status in [('/api/v1/seniors',{},401),(f'/api/v1/seniors/{S4}/status',GUARD,403),(f'/api/v1/incidents/{uuid4()}',ADMIN,404),('/api/v1/seniors/not-a-uuid/status',ADMIN,422)]:
        r=client.get(path,headers=headers)
        assert r.status_code==status,r.text
        assert {'error_code','message','details','request_id','timestamp'}==set(r.json())
    assert client.post('/api/v1/mock/scenarios',headers=GUARD,json={'senior_id':S1,'scenario':'DANGER'}).status_code==403


def test_medication_crud_and_idempotency(client):
    path=f'/api/v1/seniors/{S1}/medications'
    body=med_body()
    headers={**GUARD,'Idempotency-Key':'create-schedule-1'}
    a=client.post(path,headers=headers,json=body)
    assert a.status_code==201,a.text
    schedule=a.json()
    assert client.post(path,headers=headers,json=body).json()==schedule
    assert client.post(path,headers=headers,json=med_body(med_time='21:00')).status_code==409
    sid=schedule['schedule_id']
    r=client.patch('/api/v1/medications/'+sid,headers=GUARD,json={'med_time':'21:15','is_active':False})
    assert r.status_code==200 and r.json()['is_active'] is False and r.json()['med_time']=='21:15'
    assert client.patch('/api/v1/medications/'+sid,headers=GUARD,json={}).status_code==422
    assert client.patch('/api/v1/medications/'+sid,headers=GUARD,json={'med_time':None}).status_code==422
    assert client.delete('/api/v1/medications/'+sid,headers=GUARD).status_code==204
    assert client.delete('/api/v1/medications/'+sid,headers=GUARD).status_code==204
    assert all(m['schedule_id']!=sid for m in client.get(path,headers=GUARD).json())
    assert client.patch('/api/v1/medications/'+sid,headers=GUARD,json={'is_active':True}).status_code==404


@pytest.mark.parametrize('body',[med_body(med_time='25:00'),med_body(days_of_week=[]),med_body(days_of_week=['MON','MON']),med_body(activity_window_minutes=0),med_body(unknown=True)])
def test_medication_validation(client,body):
    r=client.post(f'/api/v1/seniors/{S1}/medications',headers={**GUARD,'Idempotency-Key':'validation-test'},json=body)
    assert r.status_code==422,r.text


def test_ingest_idempotency_scope_and_order(client):
    payload=event(risk_level='DANGER',ai_fall_confidence=0.97)
    path='/api/v1/ingest/risk-events'
    first=client.post(path,headers=DEVICE,json=payload)
    assert first.status_code==200,first.text
    assert client.post(path,headers=DEVICE,json=payload).json()['duplicate'] is True
    assert client.post(path,headers=DEVICE,json={**payload,'risk_level':'NORMAL'}).status_code==409
    assert client.post(path,headers=DEVICE,json=event(senior_id=S2,risk_level='DANGER')).status_code==403
    assert client.post(path,headers=GUARD,json=event(risk_level='DANGER')).status_code==401
    detail=client.get('/api/v1/incidents/'+first.json()['incident_id'],headers=GUARD).json()
    assert detail['evidence']['ai_fall_confidence']==0.97
    # Delayed NORMAL is persisted but does not overwrite a newer danger state.
    r=client.post(path,headers=DEVICE,json=event(occurred_at=iso(now()-timedelta(days=1)),risk_level='NORMAL'))
    assert r.status_code==200,r.text
    assert client.get(f'/api/v1/seniors/{S1}/status',headers=GUARD).json()['risk_level']=='DANGER'
    assert client.post(path,headers=DEVICE,json=event(risk_level='NORMAL',occurred_at='2026-11-04T12:00:00')).status_code==422
    assert client.post(path,headers=DEVICE,json=event(risk_level='NORMAL',occurred_at=iso(now()+timedelta(hours=1)))).status_code==422


def test_empty_paging_and_filters(client):
    assert client.get(f'/api/v1/seniors/{S4}/medications',headers=ADMIN).json()==[]
    r=client.get(f'/api/v1/seniors/{S4}/incidents',headers=ADMIN).json()
    assert r['items']==[] and r['total']==0 and r['total_pages']==0
    for _ in range(3):scenario(client,'DANGER')
    pages=[client.get(f'/api/v1/seniors/{S1}/incidents?page={n}&size=2',headers=GUARD).json() for n in [1,2]]
    assert [len(p['items']) for p in pages]==[2,1]
    assert pages[0]['total']==3
    assert client.get(f'/api/v1/seniors/{S1}/incidents?size=101',headers=GUARD).status_code==422
    assert client.get(f'/api/v1/seniors/{S1}/patterns?start_at=2027-01-01T00:00:00Z&end_at=2026-01-01T00:00:00Z',headers=GUARD).status_code==422


def test_push_tokens_cancel_and_pattern(client):
    token={'installation_id':str(uuid4()),'provider':'EXPO','push_token':'ExponentPushToken[unit-test]','device_os':'ANDROID'}
    a=client.post('/api/v1/notifications/token',headers=GUARD,json=token)
    assert a.status_code==200,a.text
    assert client.post('/api/v1/notifications/token',headers=GUARD,json={**token,'push_token':'ExponentPushToken[changed]'}).json()['token_id']==a.json()['token_id']
    scenario(client,'CANCELLED')
    assert client.get('/api/v1/notifications',headers=GUARD).json()['items']==[]
    assert client.get(f'/api/v1/seniors/{S1}/status',headers=GUARD).json()['risk_level']=='NORMAL'
    scenario(client,'DANGER');scenario(client,'PATTERN')
    rows=client.get('/api/v1/notifications',headers=GUARD).json()['items']
    assert {x['notification_type'] for x in rows}=={'FALL_ALERT','PATTERN_ALERT'}
    assert all(x['delivery_status']=='MOCK_QUEUED' for x in rows)
    assert client.get(f'/api/v1/seniors/{S1}/patterns',headers=GUARD).json()['items'][0]['metric_unit']=='count'
    assert client.delete('/api/v1/notifications/token/'+token['installation_id'],headers=GUARD).status_code==204


def test_heartbeat_timeout_and_restoration(client):
    scenario(client,'DANGER')
    db=client.app.state.db
    db.execute('UPDATE devices SET last_heartbeat_at=? WHERE senior_id=?',(iso(now()-timedelta(seconds=91)),S1))
    maintenance(db)
    data=client.get(f'/api/v1/seniors/{S1}/status',headers=GUARD).json()
    assert data['risk_level']=='DANGER' and data['data_freshness']=='STALE' and data['device_summary']['offline']==5
    scenario(client,'ONLINE')
    assert client.get(f'/api/v1/seniors/{S1}/status',headers=GUARD).json()['data_freshness']=='FRESH'
    # A stale heartbeat cannot override the recovery timestamp.
    r=client.post('/api/v1/ingest/heartbeats',headers=DEVICE,json=event(occurred_at=iso(now()-timedelta(days=1)),connection_status='OFFLINE'))
    assert r.status_code==200
    assert client.get(f'/api/v1/seniors/{S1}/status',headers=GUARD).json()['device_summary']['online']==5


def test_sync_pagination_and_websocket_replay(client):
    for kind in ['DANGER','NORMAL','CAUTION']:scenario(client,kind)
    cursor,seen=0,[]
    while True:
        r=client.get(f'/api/v1/seniors/{S1}/sync?after_cursor={cursor}&limit=1',headers=GUARD)
        assert r.status_code==200,r.text
        data=r.json();seen += data['events'];cursor=data['next_cursor']
        if not data['has_more']:break
    assert len({x['cursor'] for x in seen})==len(seen)
    assert client.get(f'/api/v1/seniors/{S1}/sync?after_cursor={cursor}',headers=GUARD).json()['events']==[]
    with client.websocket_connect('/ws/live') as ws:
        ws.send_json({'action':'SUBSCRIBE','access_token':'guardian-demo-token','senior_id':S1,'after_cursor':cursor})
        assert ws.receive_json()['type']=='SUBSCRIBED'
        assert ws.receive_json()['type']=='CURSOR'
        scenario(client,'DANGER')
        received=[]
        while len(received)<2:
            msg=ws.receive_json()
            if msg['type']=='EVENT':received.append(msg)
        assert {x['event_type'] for x in received}=={'STATUS_CHANGED','INCIDENT_ALERT'}
        last=received[-1]['cursor']
    scenario(client,'NORMAL')
    with client.websocket_connect('/ws/live') as ws:
        ws.send_json({'action':'SUBSCRIBE','access_token':'guardian-demo-token','senior_id':S1,'after_cursor':last})
        assert ws.receive_json()['type']=='SUBSCRIBED'
        assert ws.receive_json()['event_type']=='STATUS_CHANGED'
    with client.websocket_connect('/ws/live') as ws:
        ws.send_json({'action':'SUBSCRIBE','access_token':'guardian-demo-token','senior_id':S4})
        assert ws.receive_json()['error_code']=='FORBIDDEN'


def test_activity_command_ack_and_no_duplicate(client):
    local=now().astimezone(KST)
    body=med_body(med_time=(local-timedelta(minutes=1)).strftime('%H:%M'),days_of_week=['MON','TUE','WED','THU','FRI','SAT','SUN'])
    r=client.post(f'/api/v1/seniors/{S1}/medications',headers={**GUARD,'Idempotency-Key':str(uuid4())},json=body)
    assert r.status_code==201,r.text
    schedule_id=r.json()['schedule_id']
    payload=event(activity_type='WAKE_UP')
    r=client.post('/api/v1/ingest/activities',headers=DEVICE,json=payload)
    assert r.status_code==200,r.text
    ids=r.json()['command_ids'];assert ids
    assert client.post('/api/v1/ingest/activities',headers=DEVICE,json=payload).json()['command_ids']==ids
    assert client.post('/api/v1/ingest/activities',headers=DEVICE,json=event(activity_type='MOVEMENT')).json()['command_ids']==[]
    commands=client.get('/api/v1/devices/'+GATEWAY+'/commands',headers=DEVICE).json()['items']
    assert ids[0] in [x['command_id'] for x in commands]
    ack=client.post('/api/v1/commands/'+ids[0]+'/ack',headers=DEVICE,json={'status':'SUCCESS'})
    assert ack.status_code==200 and ack.json()['status']=='SUCCESS'
    assert client.post('/api/v1/commands/'+ids[0]+'/ack',headers=DEVICE,json={'status':'FAILED'}).status_code==409
    assert client.post('/api/v1/commands/'+ids[0]+'/ack',headers=DEVICE,json={'status':'SUCCESS'}).status_code==200
    log=one(client.app.state.db,'SELECT * FROM medication_logs WHERE schedule_id=?',(schedule_id,))
    assert log['play_status']=='SUCCESS' and log['voice_played_at']


def test_reset_and_persistence(tmp_path):
    app=create_app(str(tmp_path/'persistent.db'),run_maintenance=False)
    with TestClient(app) as c:
        scenario(c,'DANGER')
        watermark=c.get(f'/api/v1/seniors/{S1}/sync',headers=GUARD).json()['latest_cursor']
    with TestClient(app) as c:
        assert c.get(f'/api/v1/seniors/{S1}/status',headers=GUARD).json()['risk_level']=='DANGER'
        assert c.post('/api/v1/mock/reset',headers=ADMIN).status_code==200
        assert c.get(f'/api/v1/seniors/{S1}/status',headers=GUARD).json()['risk_level']=='NORMAL'
        assert c.get(f'/api/v1/seniors/{S1}/sync',headers=GUARD).json()['latest_cursor']>watermark


def test_cors_and_served_frontend(client):
    r=client.options('/api/v1/seniors',headers={'Origin':'http://localhost:5173','Access-Control-Request-Method':'GET','Access-Control-Request-Headers':'authorization,idempotency-key'})
    assert r.status_code==200
    assert r.headers['access-control-allow-origin']=='http://localhost:5173'
    assert client.get('/').status_code==200
    assert client.get('/assets/demo.js').status_code==200


def test_command_expiry_and_no_activity(client):
    from datetime import datetime
    db=client.app.state.db
    local=now().astimezone(KST)
    body=med_body(med_time=(local-timedelta(minutes=1)).strftime('%H:%M'),days_of_week=['MON','TUE','WED','THU','FRI','SAT','SUN'])
    r=client.post(f'/api/v1/seniors/{S1}/medications',headers={**GUARD,'Idempotency-Key':str(uuid4())},json=body)
    sid=r.json()['schedule_id']
    result=client.post('/api/v1/ingest/activities',headers=DEVICE,json=event(activity_type='PRESENT')).json()
    command=result['command_ids'][0]
    db.execute('UPDATE speaker_commands SET expires_at=? WHERE command_id=?',(iso(now()-timedelta(seconds=1)),command))
    maintenance(db)
    assert one(db,'SELECT status FROM speaker_commands WHERE command_id=?',(command,))['status']=='EXPIRED'
    assert one(db,'SELECT play_status FROM medication_logs WHERE schedule_id=?',(sid,))['play_status']=='FAILED'
    assert client.post('/api/v1/commands/'+command+'/ack',headers=DEVICE,json={'status':'SUCCESS'}).status_code==409
    # An earlier day's eligible schedule with no activity becomes NO_ACTIVITY.
    yesterday=local.date()-timedelta(days=1)
    other=client.post(f'/api/v1/seniors/{S1}/medications',headers={**GUARD,'Idempotency-Key':str(uuid4())},json=med_body(med_time='00:00',activity_window_minutes=1,days_of_week=['MON','TUE','WED','THU','FRI','SAT','SUN'])).json()['schedule_id']
    db.execute('UPDATE medication_schedules SET created_at=? WHERE schedule_id=?',(iso(now()-timedelta(days=2)),other))
    maintenance(db)
    log=one(db,'SELECT * FROM medication_logs WHERE schedule_id=? AND scheduled_date=?',(other,yesterday.isoformat()))
    assert log['play_status']=='NO_ACTIVITY' and log['source_event_id'] is None


def test_pattern_detail_and_patch_contract(client):
    result=scenario(client,'PATTERN')['result']
    r=client.get('/api/v1/patterns/'+result['pattern_id'],headers=GUARD)
    assert r.status_code==200 and r.json()['is_anomaly'] is True
    schema=client.get('/openapi.json').json()['components']['schemas']['MedicationPatch']
    assert schema['properties']['med_time']['type']=='string'
    assert 'anyOf' not in schema['properties']['med_time']
