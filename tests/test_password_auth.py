from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password, verify_password, token_hash
from app.db import GUARDIAN, SENIOR_USER, SENIORS, connect, iso, now, seed
from app.main import create_app
from tools.setup_login_demo import provision

PASSWORD='Local-test-password-123!'


def prepare(client):
    client.app.state.db.execute('UPDATE users SET password_hash=? WHERE user_id IN (?,?)',(hash_password(PASSWORD),GUARDIAN,SENIOR_USER))


def login(client,account='guardian-demo',password=PASSWORD):
    return client.post('/api/v1/auth/login',json={'account_id':account,'password':password})


def headers(result):
    return {'Authorization':'Bearer '+result.json()['access_token']}


def test_hash_salts_and_verification():
    a,b=hash_password(PASSWORD),hash_password(PASSWORD)
    assert a!=b and PASSWORD not in a
    assert verify_password(PASSWORD,a)
    assert not verify_password('wrong',a)
    assert not verify_password(PASSWORD,'invalid')


def test_login_scope_logout_and_mock_disabled(tmp_path):
    with TestClient(create_app(str(tmp_path/'auth.db'),run_maintenance=False,enable_mock_auth=False)) as c:
        prepare(c)
        assert c.post('/api/v1/auth/mock-login',json={'account_id':'guardian-demo'}).status_code==403
        assert c.get('/api/v1/seniors',headers={'Authorization':'Bearer admin-demo-token'}).status_code==401
        r=login(c)
        assert r.status_code==200 and r.json()['mock'] is False
        assert r.json()['expires_in']==3600 and r.headers['cache-control']=='no-store'
        h=headers(r)
        profile=c.get('/api/v1/auth/me',headers=h)
        assert profile.json()['role']=='GUARDIAN' and 'password_hash' not in profile.text
        assert len(c.get('/api/v1/seniors',headers=h).json()['items'])==3
        assert c.get(f'/api/v1/seniors/{SENIORS[3]}/status',headers=h).status_code==403
        db=c.app.state.db
        stored=dict(db.execute('SELECT * FROM auth_sessions').fetchone())
        assert stored['token_hash']==token_hash(r.json()['access_token'])
        assert r.json()['access_token'] not in str(stored)
        assert c.post('/api/v1/auth/logout',headers=h).status_code==204
        assert c.post('/api/v1/auth/logout',headers=h).status_code==204
        assert c.get('/api/v1/auth/me',headers=h).status_code==401
        assert c.get('/api/v1/seniors',headers=h).status_code==401
        assert c.post('/api/v1/auth/logout').status_code==401
        senior=login(c,'senior-demo');sh=headers(senior)
        assert len(c.get('/api/v1/seniors',headers=sh).json()['items'])==1
        r=c.post(f'/api/v1/seniors/{SENIORS[0]}/medications',headers={**sh,'Idempotency-Key':'password-own-crud'},json={'medication_name':'로그인 시험약','med_time':'09:00','days_of_week':['MON'],'voice_id':'TYPE_A'})
        assert r.status_code==201,r.text
        assert c.delete('/api/v1/medications/'+r.json()['schedule_id'],headers=sh).status_code==204
        assert c.post('/api/v1/auth/login',json={'account_id':'senior-demo','password':PASSWORD,'role':'ADMIN'}).status_code==422
        assert PASSWORD not in c.post('/api/v1/auth/login',json={'account_id':'senior-demo','password':PASSWORD,'extra':True}).text


def test_failures_rate_limit_and_window(tmp_path):
    path=str(tmp_path/'auth.db')
    with TestClient(create_app(path,run_maintenance=False)) as c:
        prepare(c)
        for account in ('guardian-demo','unknown','admin-demo'):
            r=login(c,account,'wrong')
            assert r.status_code==401 and r.json()['error_code']=='INVALID_CREDENTIALS'
        for _ in range(4):
            assert login(c,password='wrong').status_code==401
        assert login(c).status_code==429
    with TestClient(create_app(path,run_maintenance=False)) as c:
        assert login(c).status_code==429
        c.app.state.db.execute('UPDATE auth_login_attempts SET window_started_at=?',(iso(now()-timedelta(minutes=16)),))
        assert login(c).status_code==200


def test_restart_and_expiry(tmp_path):
    path=str(tmp_path/'auth.db')
    with TestClient(create_app(path,run_maintenance=False)) as c:
        prepare(c);h=headers(login(c))
    with TestClient(create_app(path,run_maintenance=False)) as c:
        assert c.get('/api/v1/auth/me',headers=h).status_code==200
        c.app.state.db.execute('UPDATE auth_sessions SET expires_at=?',(iso(now()-timedelta(seconds=1)),))
        assert c.get('/api/v1/auth/me',headers=h).status_code==401


def test_websocket_logout_and_disabled_mock(tmp_path):
    with TestClient(create_app(str(tmp_path/'auth.db'),run_maintenance=False,enable_mock_auth=False)) as c:
        prepare(c);r=login(c);h=headers(r)
        with c.websocket_connect('/ws/live') as ws:
            ws.send_json({'action':'SUBSCRIBE','access_token':r.json()['access_token'],'senior_id':SENIORS[0]})
            assert ws.receive_json()['type']=='SUBSCRIBED'
            assert c.post('/api/v1/auth/logout',headers=h).status_code==204
            while True:
                message=ws.receive_json()
                if message['type']=='ERROR':
                    assert message['error_code']=='SESSION_EXPIRED'
                    break
        with c.websocket_connect('/ws/live') as ws:
            ws.send_json({'action':'SUBSCRIBE','access_token':'admin-demo-token','senior_id':SENIORS[0]})
            assert ws.receive_json()['type']=='ERROR'


def test_provision_preserves_password_and_existing_data(tmp_path):
    db=connect(str(tmp_path/'auth.db'));seed(db)
    destination=tmp_path/'accounts.json'
    med_before=list(map(tuple,db.execute('SELECT * FROM medication_schedules')))
    provision(db,destination)
    hashes=list(map(tuple,db.execute('SELECT user_id,password_hash FROM users ORDER BY user_id')))
    original=destination.read_text()
    provision(db,destination)
    assert original==destination.read_text()
    assert hashes==list(map(tuple,db.execute('SELECT user_id,password_hash FROM users ORDER BY user_id')))
    assert med_before==list(map(tuple,db.execute('SELECT * FROM medication_schedules')))
    assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    assert len(db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall())==21
    db.close()


def test_mock_reset_keeps_password_and_revokes_session(tmp_path):
    with TestClient(create_app(str(tmp_path/'auth.db'),run_maintenance=False)) as c:
        prepare(c);r=login(c)
        assert c.post('/api/v1/mock/reset',headers={'Authorization':'Bearer admin-demo-token'}).status_code==200
        assert c.get('/api/v1/auth/me',headers=headers(r)).status_code==401
        assert login(c).status_code==200


def test_test_page_served(tmp_path):
    with TestClient(create_app(str(tmp_path/'auth.db'),run_maintenance=False)) as c:
        assert '비밀번호 로그인 확인' in c.get('/login-test').text
        assert c.get('/assets/login-test.js').status_code==200
