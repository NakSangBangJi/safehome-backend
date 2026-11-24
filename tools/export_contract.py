"""프로젝트 루트에서 python tools/export_contract.py. 명세·타입·사전은 실제 앱/SQL에서 생성."""
import json
import sqlite3
import sys
from pathlib import Path
from datetime import timedelta
from uuid import uuid4

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from app.main import create_app
from app.db import KST,SENIORS,iso,now,stable


def write(path,value):
    (ROOT/path).write_text(value,encoding='utf-8')


def ts(schema):
    if '$ref' in schema:return 'Schemas['+json.dumps(schema['$ref'].split('/')[-1])+']'
    if 'const' in schema:return json.dumps(schema['const'],ensure_ascii=False)
    if 'enum' in schema:return ' | '.join(json.dumps(x,ensure_ascii=False) for x in schema['enum'])
    if 'anyOf' in schema:return ' | '.join(ts(x) for x in schema['anyOf'])
    if 'allOf' in schema:return ' & '.join(ts(x) for x in schema['allOf'])
    kind=schema.get('type')
    if kind=='array':return 'Array<'+ts(schema.get('items',{}))+'>'
    if kind=='object' or 'properties' in schema:
        required=schema.get('required',[])
        properties=schema.get('properties',{})
        if not properties:return 'Record<string, unknown>'
        return '{ '+ '; '.join(json.dumps(k)+('' if k in required else '?')+': '+ts(v) for k,v in properties.items())+' }'
    return {'string':'string','integer':'number','number':'number','boolean':'boolean','null':'null'}.get(kind,'unknown')


def main():
    docs=ROOT/'docs';docs.mkdir(exist_ok=True)
    examples={}
    fixtures={}
    with TestClient(create_app(':memory:',run_maintenance=False)) as c:
        guard={'Authorization':'Bearer guardian-demo-token'}
        self_user={'Authorization':'Bearer senior-demo-token'}
        admin={'Authorization':'Bearer admin-demo-token'}
        device={'Authorization':'Bearer gateway-demo-token'}
        senior=SENIORS[0];gateway=stable(senior+'/GATEWAY')
        def capture(method,path,route=None,**kwargs):
            r=c.request(method,path,**kwargs)
            assert r.status_code<400,(path,r.text)
            examples[f'{method} {route or path.split("?")[0]}']={'request_headers':dict(kwargs.get('headers',{})),'request':kwargs.get('json'),'status':r.status_code,'response':r.json() if r.content else None}
            return r.json() if r.content else None
        capture('GET','/health')
        capture('POST','/api/v1/auth/mock-login',json={'account_id':'guardian-demo'})
        # Disposable in-memory account only; never exports local practice passwords.
        from app.auth import hash_password
        c.app.state.db.execute("UPDATE users SET password_hash=? WHERE account_id='guardian-demo'",(hash_password('Example-only-password-123!'),))
        password_session=capture('POST','/api/v1/auth/login',json={'account_id':'guardian-demo','password':'Example-only-password-123!'})
        password_header={'Authorization':'Bearer '+password_session['access_token']}
        capture('GET','/api/v1/auth/me',headers=password_header)
        capture('POST','/api/v1/auth/logout',headers=password_header)
        fixtures['accounts']={'guardian':{'account_id':'guardian-demo','access_token':'guardian-demo-token'},'admin':{'account_id':'admin-demo','access_token':'admin-demo-token'},'senior':{'account_id':'senior-demo','access_token':'senior-demo-token','senior_id':senior},'gateway':{'access_token':'gateway-demo-token','gateway_id':gateway,'senior_id':senior}}
        fixtures['seniors']=c.get('/api/v1/seniors',headers=admin).json()['items']
        capture('GET','/api/v1/seniors',headers=guard)
        capture('GET','/api/v1/admin/seniors?page=1&size=20',headers=admin)
        capture('GET','/api/v1/admin/incidents?page=1&size=20',headers=admin)
        capture('GET','/api/v1/admin/sync?after_cursor=0&limit=100',headers=admin)
        assessment={'event_id':str(uuid4()),'incident_id':str(uuid4()),'senior_id':senior,'device_id':gateway,'occurred_at':iso(),
                    'stage':'PRELIMINARY','analysis_status':'PENDING_VERIFICATION','summary':'예시: 센서 낙상 의심'}
        assessment_examples=[]
        first=capture('POST','/api/v1/ingest/fall-assessments',headers=device,json=assessment)
        assessment_examples.append({'request':assessment,'response':first})
        camera={**assessment,'event_id':str(uuid4()),'occurred_at':iso(),'stage':'FINAL','analysis_status':'CAMERA_UNAVAILABLE','confirmed_fall':None,'camera_confidence':None,'summary':'예시: 카메라 검증 불가'}
        response=c.post('/api/v1/ingest/fall-assessments',headers=device,json=camera)
        assert response.status_code==200,response.text
        assessment_examples.append({'request':camera,'response':response.json()})
        confirmed={**camera,'event_id':str(uuid4()),'occurred_at':iso(),'analysis_status':'COMPLETED','confirmed_fall':True,'summary':'예시: 카메라 낙상 확인'}
        result=c.post('/api/v1/ingest/fall-assessments',headers=device,json=confirmed)
        assert result.status_code==200,result.text
        assessment_examples.append({'request':confirmed,'response':result.json()})
        duplicate=c.post('/api/v1/ingest/fall-assessments',headers=device,json=confirmed)
        assert duplicate.status_code==200 and duplicate.json()['duplicate']
        assessment_examples.append({'request':confirmed,'response':duplicate.json()})
        assessment_examples[-1]['detail']=c.get('/api/v1/incidents/'+assessment['incident_id'],headers=admin).json()
        # The administrator preview uses the same processing rules, with its own new case.
        capture('POST','/api/v1/mock/fall-assessments',headers=admin,json={**assessment,'event_id':str(uuid4()),'incident_id':str(uuid4()),'occurred_at':iso()})
        write(Path('docs/camera-assessment-examples.json'),json.dumps(assessment_examples,ensure_ascii=False,indent=2))
        for sid in SENIORS:
            fixtures.setdefault('devices',[]).extend(c.get(f'/api/v1/seniors/{sid}/devices',headers=admin).json()['items'])
        for suffix in ['status','devices','medications','patterns','incidents','medication-logs','sync']:
            capture('GET',f'/api/v1/seniors/{senior}/{suffix}',f'/api/v1/seniors/{{senior_id}}/{suffix}',headers=guard)
        capture('GET','/api/v1/voices',headers=guard)
        med=capture('POST',f'/api/v1/seniors/{senior}/medications','/api/v1/seniors/{senior_id}/medications',headers={**self_user,'Idempotency-Key':'example-create-001'},json={'medication_name':'저녁약','med_time':'20:30','days_of_week':['MON','TUE','WED','THU','FRI','SAT','SUN'],'voice_id':'TYPE_A','activity_window_minutes':30,'is_active':True})
        capture('PATCH','/api/v1/medications/'+med['schedule_id'],'/api/v1/medications/{schedule_id}',headers=self_user,json={'med_time':'21:00','is_active':False})
        capture('DELETE','/api/v1/medications/'+med['schedule_id'],'/api/v1/medications/{schedule_id}',headers=self_user)
        token={'installation_id':str(uuid4()),'provider':'EXPO','push_token':'ExponentPushToken[m1-example]','device_os':'ANDROID'}
        capture('POST','/api/v1/notifications/token',headers=guard,json=token)
        base={'event_id':str(uuid4()),'senior_id':senior,'device_id':gateway,'occurred_at':iso()}
        result=capture('POST','/api/v1/ingest/risk-events',headers=device,json={**base,'risk_level':'DANGER','piezo_impact_val':850.0,'tof_estimated_height_mm':200,'ai_fall_confidence':0.96,'gesture_cancelled':False,'summary':'압전·ToF·영상 AI 판정'})
        capture('GET','/api/v1/incidents/'+result['incident_id'],'/api/v1/incidents/{incident_id}',headers=guard)
        capture('GET',f'/api/v1/seniors/{senior}/incidents','/api/v1/seniors/{senior_id}/incidents',headers=guard)
        capture('GET',f'/api/v1/seniors/{senior}/status','/api/v1/seniors/{senior_id}/status',headers=guard)
        pattern=capture('POST','/api/v1/ingest/patterns',headers=device,json={**base,'event_id':str(uuid4()),'pattern_type':'PROLONGED_LYING','observation_start_time':iso(now()-timedelta(hours=3)),'observation_end_time':iso(),'metric_value':180,'metric_unit':'minutes','summary_note':'장시간 누움 증가','is_anomaly':True})
        capture('GET','/api/v1/patterns/'+pattern['pattern_id'],'/api/v1/patterns/{pattern_id}',headers=guard)
        capture('GET',f'/api/v1/seniors/{senior}/patterns','/api/v1/seniors/{senior_id}/patterns',headers=guard)
        capture('POST','/api/v1/ingest/heartbeats',headers=device,json={**base,'event_id':str(uuid4()),'occurred_at':iso(),'connection_status':'ONLINE'})
        # Ensure that a real activity -> pending command -> ACK example is captured.
        local=now().astimezone(__import__('app.db',fromlist=['KST']).KST)
        c.post(f'/api/v1/seniors/{senior}/medications',headers={**guard,'Idempotency-Key':'activity-example'},json={'medication_name':'시연용 현재 시각 약','med_time':(local-timedelta(minutes=1)).strftime('%H:%M'),'days_of_week':['MON','TUE','WED','THU','FRI','SAT','SUN']})
        activity=capture('POST','/api/v1/ingest/activities',headers=device,json={**base,'event_id':str(uuid4()),'occurred_at':iso(),'activity_type':'WAKE_UP'})
        capture('POST','/api/v1/mock/medication-tick',headers=admin)
        capture('GET',f'/api/v1/devices/{gateway}/commands','/api/v1/devices/{device_id}/commands',headers=device)
        capture('POST','/api/v1/commands/'+activity['command_ids'][0]+'/ack','/api/v1/commands/{command_id}/ack',headers=device,json={'status':'SUCCESS'})
        capture('GET',f'/api/v1/seniors/{senior}/medication-logs','/api/v1/seniors/{senior_id}/medication-logs',headers=guard)
        capture('GET','/api/v1/notifications',headers=guard)
        capture('GET',f'/api/v1/seniors/{senior}/sync','/api/v1/seniors/{senior_id}/sync',headers=guard)
        capture('DELETE','/api/v1/notifications/token/'+token['installation_id'],'/api/v1/notifications/token/{installation_id}',headers=guard)
        capture('POST','/api/v1/mock/scenarios',headers=admin,json={'senior_id':senior,'scenario':'DANGER'})
        ws_examples={'path':'/ws/admin/live','subscribe':{'action':'SUBSCRIBE','access_token':'admin-demo-token','after_cursor':0},'messages':[]}
        with c.websocket_connect('/ws/admin/live') as ws:
            ws.send_json(ws_examples['subscribe'])
            ws_examples['messages'].append(ws.receive_json())
            while True:
                message=ws.receive_json();ws_examples['messages'].append(message)
                if message['type']=='CURSOR' and not message['has_more']:break
            ws.send_json({'action':'PING'})
            while True:
                message=ws.receive_json()
                if message['type']=='PONG':ws_examples['messages'].append(message);break
        with c.websocket_connect('/ws/admin/live') as ws:
            ws.send_json({'action':'SUBSCRIBE','access_token':'guardian-demo-token','after_cursor':0})
            ws_examples['forbidden']=ws.receive_json()
        write(Path('docs/admin-websocket-examples.json'),json.dumps(ws_examples,ensure_ascii=False,indent=2))
        capture('POST','/api/v1/mock/reset',headers=admin)
        spec=c.get('/openapi.json').json()
    for key,example in examples.items():
        method,path=key.split(' ',1)
        op=spec['paths'][path][method.lower()]
        if example['response'] is not None:
            op['responses'][str(example['status'])]['content']['application/json']['example']=example['response']
        if example['request'] is not None:
            op['requestBody']['content']['application/json']['example']=example['request']
    write(Path('docs/openapi.json'),json.dumps(spec,ensure_ascii=False,indent=2))
    write(Path('docs/examples.json'),json.dumps(examples,ensure_ascii=False,indent=2))
    write(Path('docs/fixtures.json'),json.dumps(fixtures,ensure_ascii=False,indent=2))
    write(Path('frontend/schemas.ts'),'// Generated from docs/openapi.json. python tools/export_contract.py\nexport interface Schemas {\n'+ '\n'.join('  '+json.dumps(k)+': '+ts(v)+';' for k,v in spec['components']['schemas'].items())+'\n}\n')
    markdown=['# API 요청·응답 예시',f'생성 기준: {now().astimezone(KST).strftime("%Y-%m-%d")} / 현재 Mock 서버 응답.',
              'UUID·시각·커서는 예시이므로 실제 호출에는 현재 서버에서 조회한 값을 사용하세요.',
              '각 예시에 요청 헤더를 표시했습니다. 어르신 목록은 보호자 기준 3명, 복약 등록·수정·삭제는 어르신 본인 계정 예시입니다. fixtures.json은 관리자 기준 전체 가상 데이터 4명입니다.',
              '센서 수치와 알림 상태는 개발용 모의 값이며 실제 AI 판단·휴대폰 전송 성공을 뜻하지 않습니다.',
              '비밀번호 로그인 예시는 버려지는 메모리 DB에서 생성한 것입니다. Example-only-password-123!은 현재 PC의 실제 연습 계정 비밀번호가 아닙니다. 세션 토큰도 이미 폐기된 예시입니다.','']
    for key,example in examples.items():
        markdown+=['## '+key,'']
        if example['request_headers']:
            markdown+=['요청 헤더(폐기된 예시 세션 또는 개발용 Mock 토큰)','```json',json.dumps(example['request_headers'],ensure_ascii=False,indent=2),'```','']
        if example['request'] is not None:markdown+=['요청 본문','```json',json.dumps(example['request'],ensure_ascii=False,indent=2),'```','']
        markdown += [f"응답: HTTP {example['status']}",'```json',json.dumps(example['response'],ensure_ascii=False,indent=2) if example['response'] is not None else '(본문 없음)','```','']
    write(Path('docs/EXAMPLES.md'),'\n'.join(markdown))
    db=sqlite3.connect(':memory:')
    db.executescript((ROOT/'app/schema.sql').read_text(encoding='utf-8'))
    tables=[x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    dictionary=['# SQLite 데이터 사전','자료형은 실제 schema.sql 기준입니다. 사용자·어르신·장치·이벤트·일정 등의 고유번호는 UUID 문자열이고 event_feed.cursor는 서버 순번입니다. account_id는 로그인 문자열, voice_id는 TYPE_A/TYPE_B 코드이므로 모든 *_id가 UUID인 것은 아닙니다.', 'SQL NOT NULL은 실제 DDL 선언이고, PK 식별자 규칙은 앱에서 적용하는 규칙입니다. 일부 TEXT PRIMARY KEY는 NOT NULL이 별도로 선언되어 있지 않으므로 두 항목을 구분합니다. INTEGER PRIMARY KEY는 자동 생성될 수 있습니다.','']
    mermaid=['erDiagram'];diagram={'tables':[],'edges':[]}
    for table in tables:
        columns=db.execute('PRAGMA table_info('+table+')').fetchall()
        fks=db.execute('PRAGMA foreign_key_list('+table+')').fetchall()
        # Derive one-to-one and optional links from the actual schema, including composite keys.
        unique_sets={tuple(c[1] for c in sorted(columns,key=lambda c:c[5]) if c[5])}
        for index in db.execute('PRAGMA index_list('+table+')').fetchall():
            if index[2] and not index[4]:
                unique_sets.add(tuple(c[2] for c in db.execute('PRAGMA index_info('+index[1]+')').fetchall()))
        fkcols={f[3]:f for f in fks}
        dictionary+=['## '+table,'','| 필드 | SQLite 자료형 | PK | SQL NOT NULL | PK 식별자 규칙 | FK / 기본값 |','|---|---|---|---|---|---|']
        mermaid+=['    '+table+' {']
        node={'name':table,'columns':[]}
        for _,name,kind,notnull,default,pk in columns:
            fk=fkcols.get(name)
            target=(fk[2]+'.'+fk[4]) if fk else ''
            pk_rule='자동' if pk and kind.upper()=='INTEGER' else '앱필수' if pk else ''
            dictionary += [f'| {name} | {kind} | {pk or ""} | {"Y" if notnull else ""} | {pk_rule} | {target or default or ""} |']
            marker='PK,FK' if pk and fk else 'PK' if pk else 'FK' if fk else ''
            mermaid+=['        '+kind+' '+name+(' '+marker if marker else '')]
            node['columns'].append({'name':name,'type':kind,'pk':bool(pk),'fk':target,'required':bool(notnull or pk)})
        mermaid+=['    }'];dictionary+=['']
        for f in fks:
            child_column=next(column for column in columns if column[1]==f[3])
            unique=(f[3],) in unique_sets
            optional=not bool(child_column[3] or child_column[5])
            identifying=bool(child_column[5])
            relation=('o|' if optional else '||')+('--' if identifying else '..')+('o|' if unique else 'o{')
            mermaid+=['    '+f[2]+' '+relation+' '+table+' : '+f[3]]
            diagram['edges'].append({'from':f[2],'to':table,'field':f[3],'unique':unique,'optional':optional,'identifying':identifying})
        diagram['tables'].append(node)
    write(Path('docs/DATA_DICTIONARY.md'),'\n'.join(dictionary))
    write(Path('docs/erd.mmd'),'\n'.join(mermaid)+'\n')
    write(Path('docs/erd-model.json'),json.dumps(diagram,indent=2))
    write(Path('docs/schema.sql'),(ROOT/'app/schema.sql').read_text(encoding='utf-8'))
    print(f"Exported {len(spec['paths'])} paths, {len(examples)} request/response examples, {len(tables)} tables.")


if __name__=='__main__':main()
