import sqlite3
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import SENIORS, connect, iso, now, seed, stable
from app.main import create_app
from test_contract import ADMIN, DEVICE, GUARD, client, scenario

PATH='/api/v1/ingest/fall-assessments'
S1,S2=SENIORS[:2]


def initial(**changes):
    return dict(event_id=str(uuid4()),incident_id=str(uuid4()),senior_id=S1,device_id=stable(S1+'/GATEWAY'),
                occurred_at=iso(),stage='PRELIMINARY',analysis_status='PENDING_VERIFICATION',summary='1차 낙상 의심',**changes)


def final(start,**changes):
    return {**start,'event_id':str(uuid4()),'occurred_at':iso(),'stage':'FINAL','analysis_status':'COMPLETED',
            'confirmed_fall':True,'camera_confidence':0.9,'summary':'카메라 확인',**changes}


def send(client,body,headers=DEVICE,path=PATH):
    return client.post(path,headers=headers,json=body)


def status(client):
    return client.get(f'/api/v1/seniors/{S1}/status',headers=GUARD).json()


def detail(client,start):
    return client.get('/api/v1/incidents/'+start['incident_id'],headers=GUARD).json()


def counts(client):
    return {table:client.app.state.db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in
            ('incidents','ingested_events','sensor_logs','notifications','event_feed','incident_assessment_events')}


def test_suspect_confirm_same_case_and_duplicate_transports(client):
    before=counts(client);body=initial()
    r=send(client,body)
    assert r.status_code==200,r.text
    assert not r.json()['duplicate'] and r.json()['state_updated']
    assert status(client)['risk_level']=='CAUTION'
    assert counts(client)['incidents']==before['incidents']+1
    assert counts(client)['notifications']==before['notifications']
    after_initial=counts(client)
    assert send(client,body).json()['duplicate']
    assert counts(client)==after_initial
    confirmed=final(body)
    r=send(client,confirmed)
    assert r.status_code==200 and r.json()['incident_id']==body['incident_id']
    assert r.json()['assessment']['confirmed_fall'] is True
    assert status(client)['risk_level']=='DANGER'
    assert status(client)['status_event_id']==confirmed['event_id']
    incident=detail(client,body)
    assert incident['event_id']==body['event_id'] and incident['event_time']==body['occurred_at']
    assert incident['assessment']['last_event_id']==confirmed['event_id']
    assert [h['stage'] for h in incident['assessment_history']]==['PRELIMINARY','FINAL']
    assert incident['assessment']['camera_confidence']==0.9
    assert incident['evidence']['ai_fall_confidence'] is None  # Camera confidence is not a fabricated fusion score.
    assert counts(client)['incidents']==before['incidents']+1
    assert counts(client)['notifications']==before['notifications']+1
    terminal=counts(client)
    assert send(client,confirmed).json()['duplicate'] and counts(client)==terminal
    # A new transport ID for the same final decision is audited but cannot send another alert.
    transport={**confirmed,'event_id':str(uuid4()),'occurred_at':iso()}
    r=send(client,transport)
    assert r.json()['duplicate'] and not r.json()['state_updated']
    after=counts(client)
    for table in ('incidents','sensor_logs','notifications','event_feed'):assert after[table]==terminal[table]
    assert detail(client,body)['assessment_history'][-1]['applied'] is False
    assert status(client)['status_event_id']==confirmed['event_id']
    assert send(client,body).json()['assessment']['analysis_status']=='COMPLETED'


def test_final_false_cancels_without_fall_notification(client):
    body=initial();send(client,body);before=counts(client)
    result=send(client,final(body,confirmed_fall=False))
    assert result.status_code==200 and result.json()['incident_status']=='CANCELLED'
    assert status(client)['risk_level']=='NORMAL'
    incident=detail(client,body)
    assert incident['status']=='CANCELLED' and incident['assessment']['confirmed_fall'] is False
    assert incident['evidence']['gesture_cancelled'] is False  # Camera cancellation is separate from a gesture.
    assert counts(client)['notifications']==before['notifications']


@pytest.mark.parametrize('failure',['CAMERA_UNAVAILABLE','CAMERA_TIMEOUT','INSUFFICIENT_DATA'])
def test_failed_camera_keeps_caution_and_allows_later_verification(client,failure):
    body=initial();send(client,body)
    r=send(client,final(body,analysis_status=failure,confirmed_fall=None,camera_confidence=None,summary=failure))
    assert r.status_code==200 and not r.json()['state_updated']
    assert status(client)['risk_level']=='CAUTION' and status(client)['status_event_id']==body['event_id']
    assert detail(client,body)['status']=='OPEN'
    later=send(client,final(body))
    assert later.status_code==200 and later.json()['assessment']['analysis_status']=='COMPLETED'
    assert len(detail(client,body)['assessment_history'])==3


def test_late_old_case_cannot_clear_or_overwrite_newer_case(client):
    old=initial();send(client,old)
    latest=scenario(client,'DANGER')['event_id']
    result=send(client,final(old,confirmed_fall=False))
    assert result.status_code==200 and not result.json()['state_updated']
    assert detail(client,old)['status']=='CANCELLED'
    assert status(client)['risk_level']=='DANGER' and status(client)['status_event_id']==latest
    old2=initial();send(client,old2)
    latest2=scenario(client,'NORMAL')['event_id']
    assert not send(client,final(old2)).json()['state_updated']
    assert status(client)['risk_level']=='NORMAL' and status(client)['status_event_id']==latest2


def test_ordering_conflicts_are_rolled_back(client):
    body=initial();baseline=counts(client)
    premature=final(body)
    assert send(client,premature).json()['error_code']=='PRELIMINARY_REQUIRED'
    assert counts(client)==baseline
    assert send(client,body).status_code==200
    # A camera result earlier than the initial signal is not accepted.
    earlier=final(body,occurred_at=iso(now()-timedelta(days=1)))
    before=counts(client)
    assert send(client,earlier).json()['error_code']=='OUT_OF_ORDER_ASSESSMENT'
    assert counts(client)==before
    assert send(client,{**body,'event_id':str(uuid4())}).json()['error_code']=='INCIDENT_ID_CONFLICT'
    assert send(client,{**body,'summary':'changed'}).json()['error_code']=='EVENT_ID_CONFLICT'
    assert counts(client)==before
    accepted=final(body);assert send(client,accepted).status_code==200
    before=counts(client)
    assert send(client,final(body,confirmed_fall=False)).json()['error_code']=='ASSESSMENT_ALREADY_FINAL'
    assert counts(client)==before


def test_cannot_attach_camera_review_to_legacy_incident(client):
    legacy=scenario(client,'DANGER')['result']['incident_id']
    body=initial();body['incident_id']=legacy
    assert send(client,body).status_code==409
    assert send(client,final(body)).json()['error_code']=='PRELIMINARY_REQUIRED'
    legacy_detail=client.get('/api/v1/incidents/'+legacy,headers=GUARD).json()
    assert legacy_detail['assessment'] is None and legacy_detail['assessment_history']==[]


@pytest.mark.parametrize('changes',[
    {'stage':'PRELIMINARY','analysis_status':'COMPLETED','confirmed_fall':True},
    {'stage':'PRELIMINARY','camera_confidence':0.8},
    {'stage':'FINAL','analysis_status':'COMPLETED'},
    {'stage':'FINAL','analysis_status':'CAMERA_TIMEOUT','confirmed_fall':False},
    {'stage':'FINAL','analysis_status':'PENDING_VERIFICATION'},
    {'stage':'FINAL','analysis_status':'COMPLETED','confirmed_fall':'false'},
    {'stage':'FINAL','analysis_status':'COMPLETED','confirmed_fall':True,'camera_confidence':1.1},
    {'stage':'FINAL','analysis_status':'UNKNOWN'},
])
def test_invalid_assessment_does_not_create_records(client,changes):
    body=initial();before=counts(client)
    assert send(client,{**body,**changes}).status_code==422
    assert counts(client)==before


def test_gateway_scope_and_admin_mock_permissions(client):
    body=initial()
    assert send(client,body,{}).status_code==401
    assert send(client,body,GUARD).status_code==401
    assert send(client,body,GUARD,'/api/v1/mock/fall-assessments').status_code==403
    other={**body,'senior_id':S2,'device_id':stable(S2+'/GATEWAY')}
    assert send(client,other).status_code==403
    assert send(client,other,ADMIN,'/api/v1/mock/fall-assessments').status_code==200
    review=final(other,senior_id=S1,device_id=stable(S1+'/GATEWAY'))
    assert send(client,review).json()['error_code']=='INCIDENT_SCOPE_MISMATCH'


def test_newer_pending_case_is_not_cleared_by_previous_review(client):
    a=initial();send(client,a)
    b=initial();send(client,b)
    assert status(client)['status_event_id']==b['event_id']
    assert not send(client,final(a,confirmed_fall=False)).json()['state_updated']
    assert status(client)['risk_level']=='CAUTION' and status(client)['status_event_id']==b['event_id']


def test_first_historical_judgment_replaces_only_initial_sentinel(client):
    body=initial();body['occurred_at']=iso(now()-timedelta(minutes=1))
    assert send(client,body).json()['state_updated']
    assert status(client)['risk_level']=='CAUTION'


def test_history_is_bounded_and_older_retry_cannot_replace_newer_attempt(client):
    body=initial();body['occurred_at']=iso(now()-timedelta(minutes=10));send(client,body)
    failed=final(body,analysis_status='CAMERA_UNAVAILABLE',confirmed_fall=None,camera_confidence=None,occurred_at=iso(now()-timedelta(minutes=3)))
    assert send(client,failed).status_code==200
    before=counts(client)
    assert send(client,final(body,occurred_at=iso(now()-timedelta(minutes=5)))).json()['error_code']=='OUT_OF_ORDER_ASSESSMENT'
    assert counts(client)==before
    for _ in range(52):
        retry={**failed,'event_id':str(uuid4()),'occurred_at':iso()}
        assert send(client,retry).status_code==200
    record=detail(client,body)
    assert len(record['assessment_history'])==50 and record['assessment_history_has_more']
    assert record['assessment_history'][-1]['event_id']==retry['event_id']


def test_gateway_cannot_review_another_gateway_case_for_same_person(client):
    other_gateway=str(uuid4())
    client.app.state.db.execute("INSERT INTO devices(device_id,senior_id,device_type,connection_status,created_at) VALUES(?,?,'GATEWAY','OFFLINE',?)",(other_gateway,S1,iso()))
    body=initial();body['device_id']=other_gateway
    assert send(client,body,ADMIN,'/api/v1/mock/fall-assessments').status_code==200
    result=send(client,final(body,device_id=stable(S1+'/GATEWAY')))
    assert result.status_code==403 and result.json()['error_code']=='INCIDENT_SCOPE_MISMATCH'


def test_assessments_survive_restart_and_reset(tmp_path):
    path=str(tmp_path/'persist.db');body=initial()
    with TestClient(create_app(path,run_maintenance=False)) as c:
        assert send(c,body).status_code==200
    with TestClient(create_app(path,run_maintenance=False)) as c:
        assert detail(c,body)['assessment']['analysis_status']=='PENDING_VERIFICATION'
        assert send(c,final(body)).status_code==200
        assert not c.app.state.db.execute('PRAGMA foreign_key_check').fetchall()
        assert c.post('/api/v1/mock/reset',headers=ADMIN).status_code==200
        assert c.app.state.db.execute('SELECT COUNT(*) FROM incident_assessment_events').fetchone()[0]==0
        assert not c.app.state.db.execute('PRAGMA foreign_key_check').fetchall()


def test_upgrade_v3_preserves_existing_rows(tmp_path):
    path=str(tmp_path/'v3.db')
    # Recreate the previous schema with its existing users, medication and incident rows.
    current=Path(__file__).resolve().parents[1]/'app/schema.sql'
    old_schema=current.read_text(encoding='utf-8').split('CREATE TABLE IF NOT EXISTS incident_assessment_events')[0]
    old=sqlite3.connect(path);old.executescript(old_schema);old.row_factory=sqlite3.Row;old.isolation_level=None
    seed(old)
    names=[r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    before={table:list(map(tuple,old.execute('SELECT * FROM '+table))) for table in names}
    old.execute('PRAGMA user_version=3');old.close()
    upgraded=connect(path)
    assert upgraded.execute('PRAGMA user_version').fetchone()[0]==4
    assert {table:list(map(tuple,upgraded.execute('SELECT * FROM '+table))) for table in names}==before
    assert upgraded.execute('SELECT COUNT(*) FROM incident_assessment_events').fetchone()[0]==0
    assert not upgraded.execute('PRAGMA foreign_key_check').fetchall()
    upgraded.close()
