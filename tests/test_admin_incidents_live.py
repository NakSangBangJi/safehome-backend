from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.db import ADMIN as ADMIN_ID, feed, iso, now
from app.main import create_app
from test_contract import ADMIN, GUARD, SENIORS, client, scenario

INCIDENTS='/api/v1/admin/incidents'
SYNC='/api/v1/admin/sync'


def subscribe(ws,cursor=0,token='admin-demo-token'):
    ws.send_json({'action':'SUBSCRIBE','access_token':token,'after_cursor':cursor})
    return ws.receive_json()


def drain(ws):
    events=[]
    for _ in range(2000):
        message=ws.receive_json()
        if message['type']=='EVENT':events.append(message)
        if message['type']=='ERROR':raise AssertionError(message)
        if message['type']=='CURSOR' and not message['has_more']:return events,message['cursor']
    raise AssertionError('Stream did not finish replay')


def test_admin_incident_filters_order_and_details(client):
    db=client.app.state.db
    initial=client.get(INCIDENTS,headers=ADMIN).json()['items']
    assert len(initial)==2 and {r['senior_id'] for r in initial}==set(SENIORS[1:3])
    t=datetime(2026,11,10,0,0,tzinfo=timezone.utc)
    ids=[]
    for index,row in enumerate(initial):
        db.execute('UPDATE incidents SET event_time=? WHERE incident_id=?',(iso(t+timedelta(hours=index)),row['incident_id']))
        ids.append(row['incident_id'])
    r=client.get(INCIDENTS,headers=ADMIN,params={'start_at':iso(t),'end_at':iso(t+timedelta(hours=1))})
    assert r.status_code==200 and [row['incident_id'] for row in r.json()['items']]==ids[:1]
    kst=client.get(INCIDENTS,headers=ADMIN,params={'start_at':'2026-11-10T09:00:00+09:00','end_at':'2026-11-10T10:00:00+09:00'})
    assert kst.json()==r.json()
    selected=client.get(INCIDENTS,headers=ADMIN,params={'senior_id':initial[0]['senior_id']}).json()
    assert selected['total']==1 and selected['items'][0]['senior_name']
    for item in client.get(INCIDENTS,headers=ADMIN).json()['items']:
        detail=client.get('/api/v1/incidents/'+item['incident_id'],headers=ADMIN)
        assert detail.status_code==200
        for key in ('senior_id','risk_level','event_time','status','summary'):
            assert detail.json()[key]==item[key]
        assert 'password_hash' not in item and 'evidence' not in item
    cancelled=scenario(client,'CANCELLED')['result']['incident_id']
    assert client.get(INCIDENTS,headers=ADMIN).json()['items'][0]['incident_id']==cancelled
    assert client.get('/api/v1/incidents/'+cancelled,headers=ADMIN).json()['status']=='CANCELLED'
    total=client.get(INCIDENTS,headers=ADMIN).json()['total']
    scenario(client,'NORMAL')
    assert client.get(INCIDENTS,headers=ADMIN).json()['total']==total


def test_admin_incident_pagination_and_empty(client):
    db=client.app.state.db
    db.execute('UPDATE incidents SET event_time=?',(iso(),))
    ordered=sorted([r[0] for r in db.execute('SELECT incident_id FROM incidents')],reverse=True)
    found=[client.get(INCIDENTS,headers=ADMIN,params={'page':p,'size':1}).json()['items'][0]['incident_id'] for p in (1,2)]
    assert found==ordered
    r=client.get(INCIDENTS,headers=ADMIN,params={'page':10**30,'size':1})
    assert r.status_code==200 and r.json()['items']==[] and r.json()['total']==2
    assert client.get(INCIDENTS,headers=ADMIN,params={'senior_id':SENIORS[3]}).json()['items']==[]
    assert client.get(INCIDENTS,headers=ADMIN,params={'senior_id':str(uuid4())}).status_code==404


@pytest.mark.parametrize('params',[{'page':0},{'size':101},{'senior_id':'bad'},{'start_at':'2026-11-10T00:00:00'},{'start_at':'2026-11-11T00:00:00Z','end_at':'2026-11-10T00:00:00Z'}])
def test_admin_incidents_bad_filters(client,params):
    assert client.get(INCIDENTS,headers=ADMIN,params=params).status_code==422


def test_admin_routes_scope(client):
    for path in (INCIDENTS,SYNC):
        assert client.get(path).status_code==401
        assert client.get(path,headers=GUARD).status_code==403
        assert client.get(path,headers={'Authorization':'Bearer senior-demo-token'}).status_code==403
    for token,status in [('', 'UNAUTHENTICATED'),('guardian-demo-token','FORBIDDEN'),('senior-demo-token','FORBIDDEN')]:
        with client.websocket_connect('/ws/admin/live') as ws:
            assert subscribe(ws,token=token)['error_code']==status


def test_admin_sync_pages_excludes_medication_and_advances_cursor(client):
    db=client.app.state.db
    baseline=client.get(SYNC,headers=ADMIN).json()['latest_cursor']
    for i in range(205):
        feed(db,SENIORS[i%4],'STATUS_CHANGED',{'risk_level':'NORMAL'})
        feed(db,SENIORS[i%4],'MEDICATION_REMINDER',{'private':'not in admin warning channel'})
    cursor=baseline;events=[]
    while True:
        r=client.get(SYNC,headers=ADMIN,params={'after_cursor':cursor,'limit':100})
        assert r.status_code==200
        data=r.json();events+=data['events'];cursor=data['next_cursor']
        if not data['has_more']:break
    assert len(events)==205
    assert len({e['cursor'] for e in events})==205
    assert {e['senior_id'] for e in events}==set(SENIORS)
    assert all(e['event_type']=='STATUS_CHANGED' and e['senior_name'] for e in events)
    assert cursor==data['latest_cursor']>events[-1]['cursor']
    assert client.get(SYNC,headers=ADMIN,params={'after_cursor':cursor}).json()['events']==[]
    with client.websocket_connect('/ws/admin/live') as ws:
        assert subscribe(ws,baseline)['type']=='SUBSCRIBED'
        streamed,end=drain(ws)
        assert [e['cursor'] for e in streamed]==[e['cursor'] for e in events]
        assert all(e['replayed'] for e in streamed) and end==cursor


def test_admin_live_replay_new_event_and_reconnect(client):
    baseline=client.get(SYNC,headers=ADMIN).json()['latest_cursor']
    # Event between initial HTTP checkpoint and socket connection must be replayed.
    replay=scenario(client,'DANGER',SENIORS[3])['result']['incident_id']
    with client.websocket_connect('/ws/admin/live') as ws:
        assert subscribe(ws,baseline)['type']=='SUBSCRIBED'
        first,cursor=drain(ws)
        assert any(e['data'].get('incident_id')==replay and e['replayed'] for e in first)
        new_id=scenario(client,'CAUTION',SENIORS[0])['result']['incident_id']
        while True:
            new,end=drain(ws)
            if any(e['data'].get('incident_id')==new_id for e in new):break
        assert any(not e['replayed'] for e in new)
        ws.send_json({'action':'PING'})
        for _ in range(20):
            if ws.receive_json()['type']=='PONG':break
        else:raise AssertionError('No PONG')
    while_offline=scenario(client,'DANGER',SENIORS[1])['result']['incident_id']
    with client.websocket_connect('/ws/admin/live') as ws:
        subscribe(ws,end)
        events,new_cursor=drain(ws)
        assert any(e['data'].get('incident_id')==while_offline for e in events)
        assert all(e['cursor']>end and e['replayed'] for e in events)
        assert new_cursor>end


@pytest.mark.parametrize('value',[-1,True,'0',1.5,9007199254740992])
def test_admin_live_rejects_invalid_cursor(client,value):
    with client.websocket_connect('/ws/admin/live') as ws:
        ws.send_json({'action':'SUBSCRIBE','access_token':'admin-demo-token','after_cursor':value})
        assert ws.receive_json()['error_code']=='INVALID_SUBSCRIPTION'


def test_admin_live_bad_message_and_cursor_recovery(client):
    for value in ([],None,{'action':'NOPE'}):
        with client.websocket_connect('/ws/admin/live') as ws:
            ws.send_json(value)
            assert ws.receive_json()['type']=='ERROR'
    with client.websocket_connect('/ws/admin/live') as ws:
        ws.send_text('{invalid json')
        assert ws.receive_json()['error_code']=='INVALID_MESSAGE'
    latest=client.get(SYNC,headers=ADMIN).json()['latest_cursor']
    assert client.get(SYNC,headers=ADMIN,params={'after_cursor':latest+100}).status_code==409
    with client.websocket_connect('/ws/admin/live') as ws:
        assert subscribe(ws,latest+100)['error_code']=='CURSOR_AHEAD'
    assert client.post('/api/v1/mock/reset',headers=ADMIN).status_code==200
    r=client.get(SYNC,headers=ADMIN,params={'after_cursor':1})
    assert r.status_code==409 and r.json()['error_code']=='CURSOR_EXPIRED'
    assert client.get(SYNC,headers=ADMIN).status_code==200
    for params in ({'after_cursor':-1},{'limit':0},{'limit':101}):
        assert client.get(SYNC,headers=ADMIN,params=params).status_code==422


def test_admin_live_session_logout_and_expiry(tmp_path):
    with TestClient(create_app(str(tmp_path/'admin.db'),run_maintenance=False,enable_mock_auth=False)) as c:
        c.app.state.db.execute('UPDATE users SET password_hash=? WHERE user_id=?',(hash_password('Admin-test-123!'),ADMIN_ID))
        for action in ('logout','expire'):
            r=c.post('/api/v1/auth/login',json={'account_id':'admin-demo','password':'Admin-test-123!'})
            token=r.json()['access_token']
            with c.websocket_connect('/ws/admin/live') as ws:
                assert subscribe(ws,token=token)['type']=='SUBSCRIBED'
                drain(ws)
                if action=='logout':c.post('/api/v1/auth/logout',headers={'Authorization':'Bearer '+token})
                else:c.app.state.db.execute('UPDATE auth_sessions SET expires_at=?',(iso(now()-timedelta(seconds=1)),))
                for _ in range(30):
                    message=ws.receive_json()
                    if message['type']=='ERROR':
                        assert message['error_code']=='SESSION_EXPIRED';break
                else:raise AssertionError('Session not revoked')


def test_admin_records_survive_server_restart(tmp_path):
    path=str(tmp_path/'persistent.db')
    with TestClient(create_app(path,run_maintenance=False)) as c:
        baseline=c.get(SYNC,headers=ADMIN).json()['latest_cursor']
        record=scenario(c,'DANGER')['result']['incident_id']
    with TestClient(create_app(path,run_maintenance=False)) as c:
        assert any(i['incident_id']==record for i in c.get(INCIDENTS,headers=ADMIN).json()['items'])
        assert any(e['data'].get('incident_id')==record for e in c.get(SYNC,headers=ADMIN,params={'after_cursor':baseline}).json()['events'])
