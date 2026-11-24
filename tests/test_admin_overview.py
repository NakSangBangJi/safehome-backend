from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.db import ADMIN as ADMIN_ID, connect, iso, now
from app.main import create_app
from test_contract import ADMIN, GUARD, SENIORS, client, scenario

PATH='/api/v1/admin/seniors'


def test_admin_overview_matches_individual_status_and_handles_device_loss(client):
    db=client.app.state.db
    db.execute("UPDATE devices SET connection_status='OFFLINE' WHERE senior_id=? AND device_type='CAMERA'",(SENIORS[0],))
    risk_time=db.execute('SELECT last_status_updated_at FROM seniors WHERE senior_id=?',(SENIORS[2],)).fetchone()[0]
    db.execute('UPDATE devices SET last_heartbeat_at=? WHERE senior_id=?',(iso(now()-timedelta(minutes=2)),SENIORS[2]))
    db.execute('DELETE FROM devices WHERE senior_id=?',(SENIORS[3],))
    r=client.get(PATH,headers=ADMIN)
    assert r.status_code==200,r.text
    assert r.headers['cache-control']=='no-store'
    result=r.json()
    assert (result['total'],result['page'],result['size'],result['total_pages'])==(4,1,20,1)
    assert {item['senior_id'] for item in result['items']}==set(SENIORS)
    by_id={item['senior_id']:item for item in result['items']}
    for item in result['items']:
        assert set(item)=={'senior_id','name','risk_level','last_updated_at','status_event_id','data_freshness','device_summary'}
        single=client.get(f"/api/v1/seniors/{item['senior_id']}/status",headers=ADMIN).json()
        for field in ('risk_level','last_updated_at','status_event_id','data_freshness','device_summary'):
            assert item[field]==single[field]
    assert by_id[SENIORS[0]]['device_summary']=={'online':4,'offline':1}
    assert by_id[SENIORS[1]]['data_freshness']=='FRESH'
    assert by_id[SENIORS[2]]['risk_level']=='DANGER'
    assert by_id[SENIORS[2]]['last_updated_at']==risk_time
    assert by_id[SENIORS[2]]['device_summary']=={'online':0,'offline':5}
    assert by_id[SENIORS[3]]['device_summary']=={'online':0,'offline':0}
    assert by_id[SENIORS[3]]['data_freshness']=='STALE'


def test_admin_overview_distinguishes_initial_normal_from_recorded_normal(client):
    def target(senior_id=SENIORS[0]):
        return next(item for item in client.get(PATH,headers=ADMIN).json()['items'] if item['senior_id']==senior_id)
    initial=target()
    assert initial['risk_level']=='NORMAL' and initial['status_event_id'] is None
    # No device connection or elapsed time can manufacture a judgment event.
    scenario(client,'OFFLINE')
    assert target()['status_event_id'] is None
    assert target()['data_freshness']=='STALE'
    result=scenario(client,'NORMAL')
    recorded=target()
    assert recorded['risk_level']=='NORMAL'
    assert recorded['status_event_id']==result['event_id']
    assert recorded['last_updated_at']==client.get(f'/api/v1/seniors/{SENIORS[0]}/status',headers=ADMIN).json()['last_updated_at']
    scenario(client,'ONLINE')
    assert target()['status_event_id']==result['event_id']
    assert target(SENIORS[3])['status_event_id'] is None


def test_admin_overview_pagination_is_stable_for_same_names(client):
    client.app.state.db.execute("UPDATE seniors SET name='같은 이름'")
    first=client.get(PATH+'?page=1&size=2',headers=ADMIN).json()
    second=client.get(PATH+'?page=2&size=2',headers=ADMIN).json()
    assert first['total']==second['total']==4
    assert first['total_pages']==second['total_pages']==2
    assert [item['senior_id'] for item in first['items']]==SENIORS[:2]
    assert [item['senior_id'] for item in second['items']]==SENIORS[2:]
    assert client.get(PATH+'?page=1&size=2',headers=ADMIN).json()==first
    for page in (3,10**30):
        empty=client.get(PATH,params={'page':page,'size':2},headers=ADMIN)
        assert empty.status_code==200
        assert empty.json()=={'items':[],'page':page,'size':2,'total':4,'total_pages':2}


@pytest.mark.parametrize('query',['page=0','page=-1','page=abc','size=0','size=101'])
def test_admin_overview_validates_pagination(client,query):
    r=client.get(PATH+'?'+query,headers=ADMIN)
    assert r.status_code==422 and r.json()['error_code']=='VALIDATION_ERROR'


def test_admin_overview_denies_non_admin(client):
    assert client.get(PATH).status_code==401
    for token in ('guardian-demo-token','senior-demo-token','gateway-demo-token'):
        r=client.get(PATH,headers={'Authorization':'Bearer '+token})
        assert r.status_code==(401 if token=='gateway-demo-token' else 403)
        assert 'items' not in r.json()
    # Existing guardian list still exposes only the three linked seniors.
    assert len(client.get('/api/v1/seniors',headers=GUARD).json()['items'])==3


def test_admin_overview_real_admin_session_with_mock_auth_off(tmp_path):
    with TestClient(create_app(str(tmp_path/'admin.db'),run_maintenance=False,enable_mock_auth=False)) as c:
        c.app.state.db.execute('UPDATE users SET password_hash=? WHERE user_id=?',(hash_password('Admin-test-only-123!'),ADMIN_ID))
        r=c.post('/api/v1/auth/login',json={'account_id':'admin-demo','password':'Admin-test-only-123!'})
        h={'Authorization':'Bearer '+r.json()['access_token']}
        assert len(c.get(PATH,headers=h).json()['items'])==4
        assert c.get(PATH,headers=ADMIN).status_code==401
        assert c.post('/api/v1/auth/logout',headers=h).status_code==204
        assert c.get(PATH,headers=h).status_code==401


def test_admin_overview_empty_database(tmp_path):
    path=str(tmp_path/'empty.db')
    db=connect(path)
    db.execute("INSERT INTO users(user_id,account_id,user_name,role,created_at) VALUES(?,?,?,'ADMIN',?)",(ADMIN_ID,'admin-demo','빈 목록 관리자',iso()))
    db.close()
    with TestClient(create_app(path,run_maintenance=False)) as c:
        result=c.get(PATH,headers=ADMIN)
        assert result.status_code==200
        assert result.json()=={'items':[],'page':1,'size':20,'total':0,'total_pages':0}


def test_admin_overview_page_and_script_served(client):
    assert '관리자 전체 현황' in client.get('/admin-test').text
    assert client.get('/assets/admin-test.js').status_code==200
