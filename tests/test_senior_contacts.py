from app.db import GUARDIAN, SENIOR_USER
from test_contract import ADMIN, GUARD, S1, S2, S4, client

SELF={'Authorization':'Bearer senior-demo-token'}


def test_senior_phone_is_own_contact_and_preserves_scope(client):
    db=client.app.state.db
    # Deliberately different synthetic contacts detect an accidental guardian join.
    db.execute('UPDATE users SET phone_number=? WHERE user_id=?',('010-0000-0000',SENIOR_USER))
    db.execute('UPDATE users SET phone_number=? WHERE user_id=?',('010-0000-0001',GUARDIAN))
    db.commit()
    response=client.get('/api/v1/seniors',headers=GUARD)
    assert response.status_code==200
    rows=response.json()['items']
    assert len(rows)==3 and S4 not in {r['senior_id'] for r in rows}
    first=next(r for r in rows if r['senior_id']==S1)
    assert first['phone_number']=='010-0000-0000'
    assert set(first)=={'senior_id','name','timezone','phone_number'}
    assert next(r for r in rows if r['senior_id']==S2)['phone_number'] is None
    assert client.get('/api/v1/seniors',headers=SELF).json()['items']==[first]
    assert len(client.get('/api/v1/seniors',headers=ADMIN).json()['items'])==4
    assert client.get('/api/v1/seniors').status_code==401


def test_unregistered_senior_phone_is_null_and_contract_is_nullable(client):
    rows=client.get('/api/v1/seniors',headers=GUARD).json()['items']
    assert all('phone_number' in row and row['phone_number'] is None for row in rows)
    prop=client.get('/openapi.json').json()['components']['schemas']['Senior']['properties']['phone_number']
    assert {choice['type'] for choice in prop['anyOf']}=={'string','null'}
