import asyncio
import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from pydantic import ValidationError

from . import models as m
from . import auth
from .errors import ApiError
from .fall_assessments import assessment_details, process_assessment
from .db import ADMIN, GUARDIAN, SENIOR_USER, KST, SENIORS, connect, dumps, feed, insert, iso, many, now, one, seed, stable, transaction, uid

ROOT = Path(__file__).resolve().parent.parent
TOKENS = {'admin-demo-token': ADMIN, 'guardian-demo-token': GUARDIAN, 'senior-demo-token': SENIOR_USER}
DEVICE_TOKEN = 'gateway-demo-token'
bearer = HTTPBearer(auto_error=False)
logger = logging.getLogger('safehome')


def require_record(row, message='대상을 찾을 수 없습니다.'):
    if row is None:
        raise ApiError(404, 'NOT_FOUND', message)
    return row


def user_from_token(db, token, allow_mock=True):
    user_id = TOKENS.get(token) if allow_mock and isinstance(token, str) else None
    if user_id:
        return require_record(one(db, 'SELECT * FROM users WHERE user_id=?', (user_id,)))
    return auth.session_user(db, token)


async def current_user(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    return user_from_token(request.app.state.db, credentials.credentials if credentials and credentials.scheme.lower() == 'bearer' else '', request.app.state.enable_mock_auth)


async def admin_user(user=Depends(current_user)):
    if user['role'] != 'ADMIN':
        raise ApiError(403, 'FORBIDDEN', '관리자 권한이 필요합니다.')
    return user


async def device_user(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    if not credentials or credentials.scheme.lower() != 'bearer' or credentials.credentials != DEVICE_TOKEN:
        raise ApiError(401, 'INVALID_DEVICE_TOKEN', '시연 게이트웨이 토큰이 필요합니다.')
    return {'device_id': stable(SENIORS[0]+'/GATEWAY')}


def authorize(db, user, senior_id):
    senior_id = str(senior_id)
    mapped=one(db,'SELECT 1 FROM seniors WHERE senior_id=? AND user_id=?',(senior_id,user['user_id'])) if user['role']=='SENIOR' else one(db,'SELECT 1 FROM senior_guardians WHERE senior_id=? AND guardian_id=?',(senior_id,user['user_id']))
    if user['role'] != 'ADMIN' and not mapped:
        raise ApiError(403, 'FORBIDDEN', '해당 어르신에 대한 접근 권한이 없습니다.')
    return require_record(one(db, 'SELECT * FROM seniors WHERE senior_id=?', (senior_id,)))


def scope_device(db, device, device_id, senior_id=None):
    target = require_record(one(db, 'SELECT * FROM devices WHERE device_id=?', (str(device_id),)))
    if target['device_id'] != device['device_id'] and target['parent_device_id'] != device['device_id']:
        raise ApiError(403, 'DEVICE_SCOPE_MISMATCH', '게이트웨이 소속 장치만 처리할 수 있습니다.')
    if senior_id is not None and target['senior_id'] != str(senior_id):
        raise ApiError(403, 'DEVICE_SCOPE_MISMATCH', '장치와 어르신 매핑이 일치하지 않습니다.')
    return target


def status_data(db, senior):
    devices = many(db, 'SELECT connection_status FROM devices WHERE senior_id=?', (senior['senior_id'],))
    offline = sum(d['connection_status']=='OFFLINE' for d in devices)
    return {'senior_id':senior['senior_id'], 'risk_level':senior['current_risk_level'], 'last_updated_at':senior['last_status_updated_at'],
            'status_event_id':senior['status_event_id'], 'data_freshness':'STALE' if offline or not devices else 'FRESH',
            'device_summary':{'online':len(devices)-offline,'offline':offline}}


def medication_data(row):
    result = dict(row)
    result['days_of_week'] = json.loads(result.pop('days_of_week_json'))
    result['is_active'] = bool(result['is_active'])
    result.pop('deleted_at', None)
    result['timezone'] = 'Asia/Seoul'
    return result


def valid_dates(start, end):
    for value in (start,end):
        if value is not None and value.tzinfo is None:
            raise ApiError(422, 'VALIDATION_ERROR', '시각에는 시간대를 포함해야 합니다.')
    if start and end and start >= end:
        raise ApiError(422, 'INVALID_DATE_RANGE', 'start_at은 end_at보다 빨라야 합니다.')


def save_event(db, payload, kind):
    value = payload.model_dump(mode='json')
    content = dumps(value)
    existing = one(db, 'SELECT payload_json FROM ingested_events WHERE event_id=?', (str(payload.event_id),))
    if existing:
        if existing['payload_json'] != content:
            raise ApiError(409, 'EVENT_ID_CONFLICT', '같은 event_id에 다른 데이터를 보낼 수 없습니다.')
        return False
    if payload.occurred_at > now() + timedelta(minutes=5):
        raise ApiError(422, 'FUTURE_EVENT', '이벤트 시각은 서버 시각보다 5분 이상 미래일 수 없습니다.')
    insert(db, 'ingested_events', event_id=str(payload.event_id), device_id=str(payload.device_id), senior_id=str(payload.senior_id),
           event_type=kind, occurred_at=iso(payload.occurred_at), received_at=iso(), payload_json=content)
    return True


def notify(db, senior_id, event_id, kind, title, body, deep_link):
    for user in many(db, 'SELECT guardian_id FROM senior_guardians WHERE senior_id=?', (senior_id,)):
        has_token = one(db, 'SELECT 1 FROM push_tokens WHERE user_id=?', (user['guardian_id'],))
        insert(db, 'notifications', notification_id=uid(), senior_id=senior_id, user_id=user['guardian_id'], source_event_id=event_id,
               notification_type=kind, title=title, body=body, deep_link=deep_link,
               delivery_status='MOCK_QUEUED' if has_token else 'NO_TOKEN', created_at=iso())


def risk_ingest(db, payload):
    event_id, senior_id = str(payload.event_id), str(payload.senior_id)
    with transaction(db):
        fresh = save_event(db, payload, 'RISK')
        if not fresh:
            existing = one(db, 'SELECT incident_id FROM incidents WHERE event_id=?', (event_id,))
            return {'event_id':event_id,'duplicate':True,'incident_id':existing['incident_id'] if existing else None}
        sensor_id, incident_id, t = uid(), None, iso(payload.occurred_at)
        insert(db, 'sensor_logs', log_id=sensor_id, event_id=event_id, piezo_impact_val=payload.piezo_impact_val,
               tof_estimated_height_mm=payload.tof_estimated_height_mm, ai_fall_confidence=payload.ai_fall_confidence)
        if payload.risk_level != m.Risk.NORMAL:
            incident_id = uid()
            insert(db, 'incidents', incident_id=incident_id, event_id=event_id, senior_id=senior_id, event_time=t,
                   risk_level=payload.risk_level.value, summary=payload.summary, status='CANCELLED' if payload.gesture_cancelled else 'OPEN', created_at=iso())
            insert(db, 'incident_details', incident_id=incident_id, sensor_log_id=sensor_id, gesture_cancelled=int(payload.gesture_cancelled), snapshot_image_url=None)
        senior = require_record(one(db, 'SELECT * FROM seniors WHERE senior_id=?', (senior_id,)))
        # Preserve chronological state when delayed packets arrive. The event ID breaks equal-time ties.
        newer = (t, event_id) > (senior['last_status_updated_at'], senior['status_event_id'] or '')
        effective = 'NORMAL' if payload.gesture_cancelled else payload.risk_level.value
        if newer:
            db.execute('UPDATE seniors SET current_risk_level=?, last_status_updated_at=?, status_event_id=? WHERE senior_id=?', (effective,t,event_id,senior_id))
            feed(db, senior_id, 'STATUS_CHANGED', {'risk_level':effective,'last_updated_at':t,'status_event_id':event_id}, t)
        if incident_id and not payload.gesture_cancelled:
            feed(db, senior_id, 'INCIDENT_ALERT', {'incident_id':incident_id,'risk_level':payload.risk_level.value,'summary':payload.summary}, t)
            if payload.risk_level == m.Risk.DANGER:
                notify(db, senior_id, event_id, 'FALL_ALERT', '낙상 위험 발생', payload.summary, 'safehome://incidents/'+incident_id)
    return {'event_id':event_id,'duplicate':False,'incident_id':incident_id}


def heartbeat_ingest(db, payload):
    device_id, senior_id, event_id = str(payload.device_id), str(payload.senior_id), str(payload.event_id)
    with transaction(db):
        if not save_event(db, payload, 'HEARTBEAT'):
            return {'event_id':event_id,'duplicate':True}
        device = require_record(one(db, 'SELECT * FROM devices WHERE device_id=?', (device_id,)))
        t = iso(payload.occurred_at)
        if not device['last_heartbeat_at'] or t >= device['last_heartbeat_at']:
            db.execute('UPDATE devices SET connection_status=?,last_heartbeat_at=? WHERE device_id=?', (payload.connection_status,t,device_id))
            if device['connection_status'] != payload.connection_status:
                insert(db, 'device_logs', log_id=uid(), device_id=device_id, status=payload.connection_status, reason='HEARTBEAT', occurred_at=t)
                feed(db, senior_id, 'DEVICE_STATUS_CHANGED', {'device_id':device_id,'connection_status':payload.connection_status}, t)
    return {'event_id':event_id,'duplicate':False}


def pattern_ingest(db, payload):
    event_id, senior_id = str(payload.event_id), str(payload.senior_id)
    with transaction(db):
        if not save_event(db, payload, 'PATTERN'):
            return {'event_id':event_id,'duplicate':True,'pattern_id':one(db,'SELECT pattern_id FROM lifestyle_patterns WHERE event_id=?',(event_id,))['pattern_id']}
        pattern_id = uid()
        insert(db, 'lifestyle_patterns', pattern_id=pattern_id, event_id=event_id, senior_id=senior_id,
               pattern_type=payload.pattern_type, observation_start_time=iso(payload.observation_start_time),
               observation_end_time=iso(payload.observation_end_time), metric_value=payload.metric_value,
               metric_unit=payload.metric_unit, summary_note=payload.summary_note, is_anomaly=int(payload.is_anomaly), created_at=iso())
        feed(db, senior_id, 'PATTERN_UPDATED', {'pattern_id':pattern_id,'pattern_type':payload.pattern_type,'is_anomaly':payload.is_anomaly})
        if payload.is_anomaly:
            notify(db, senior_id, event_id, 'PATTERN_ALERT', '생활 패턴 변화', payload.summary_note, 'safehome://patterns/'+pattern_id)
    return {'event_id':event_id,'duplicate':False,'pattern_id':pattern_id}


def schedule_windows(schedule, current):
    local = current.astimezone(KST)
    for day in (local.date()-timedelta(days=1), local.date()):
        start = datetime.fromisoformat(f"{day.isoformat()}T{schedule['med_time']}:00+09:00")
        if ['MON','TUE','WED','THU','FRI','SAT','SUN'][day.weekday()] in json.loads(schedule['days_of_week_json']):
            yield day.isoformat(), start, start + timedelta(minutes=schedule['activity_window_minutes'])


def activity_ingest(db, payload):
    event_id, senior_id = str(payload.event_id), str(payload.senior_id)
    with transaction(db):
        if not save_event(db, payload, 'ACTIVITY'):
            # A retry returns the original command IDs; all commands retain their activity log reference.
            ids = many(db, 'SELECT c.command_id FROM speaker_commands c JOIN medication_logs l ON c.log_id=l.log_id WHERE l.source_event_id=?', (event_id,))
            return {'event_id':event_id,'duplicate':True,'command_ids':[x['command_id'] for x in ids]}
        ids = []
        gateway = one(db, "SELECT device_id FROM devices WHERE senior_id=? AND device_type='GATEWAY'", (senior_id,))
        # Old replayed activity must never produce an immediate speaker instruction.
        if (now()-payload.occurred_at).total_seconds() > 120:
            return {'event_id':event_id,'duplicate':False,'command_ids':[]}
        for schedule in many(db, 'SELECT * FROM medication_schedules WHERE senior_id=? AND is_active=1 AND deleted_at IS NULL', (senior_id,)):
            for date, start, end in schedule_windows(schedule, payload.occurred_at):
                if not (start <= payload.occurred_at < end and now() < end):
                    continue
                if one(db,'SELECT 1 FROM medication_logs WHERE schedule_id=? AND scheduled_date=?',(schedule['schedule_id'],date)):
                    continue
                log_id, command_id = uid(), uid()
                insert(db,'medication_logs',log_id=log_id,schedule_id=schedule['schedule_id'],source_event_id=event_id,scheduled_date=date,activity_detected_at=iso(payload.occurred_at),voice_played_at=None,play_status='PENDING')
                insert(db,'speaker_commands',command_id=command_id,device_id=gateway['device_id'],log_id=log_id,voice_id=schedule['voice_id'],status='PENDING',created_at=iso(),expires_at=iso(end),acknowledged_at=None)
                feed(db, senior_id, 'SPEAKER_COMMAND_CREATED', {'command_id':command_id,'schedule_id':schedule['schedule_id'],'voice_id':schedule['voice_id']})
                ids.append(command_id)
    return {'event_id':event_id,'duplicate':False,'command_ids':ids}


def maintenance(db):
    current = now()
    with transaction(db):
        for device in many(db,"SELECT * FROM devices WHERE connection_status='ONLINE' AND last_heartbeat_at < ?", (iso(current-timedelta(seconds=90)),)):
            db.execute("UPDATE devices SET connection_status='OFFLINE' WHERE device_id=?", (device['device_id'],))
            insert(db,'device_logs',log_id=uid(),device_id=device['device_id'],status='OFFLINE',reason='HEARTBEAT_TIMEOUT',occurred_at=iso(current))
            feed(db,device['senior_id'],'DEVICE_STATUS_CHANGED',{'device_id':device['device_id'],'connection_status':'OFFLINE'})
        for command in many(db,"SELECT * FROM speaker_commands WHERE status='PENDING' AND expires_at<=?",(iso(current),)):
            db.execute("UPDATE speaker_commands SET status='EXPIRED' WHERE command_id=?",(command['command_id'],))
            db.execute("UPDATE medication_logs SET play_status='FAILED' WHERE log_id=?",(command['log_id'],))
        for schedule in many(db,'SELECT * FROM medication_schedules WHERE is_active=1 AND deleted_at IS NULL'):
            for date, start, end in schedule_windows(schedule,current):
                if start <= current < end:
                    senior=one(db,"SELECT s.user_id FROM seniors s JOIN users u ON u.user_id=s.user_id WHERE s.senior_id=? AND u.role='SENIOR'",(schedule['senior_id'],))
                    user_id=senior['user_id'] if senior else None
                    if user_id and not one(db,"SELECT 1 FROM notifications WHERE user_id=? AND schedule_id=? AND scheduled_date=? AND notification_type='MEDICATION_REMINDER'",(user_id,schedule['schedule_id'],date)):
                        has_token=one(db,'SELECT 1 FROM push_tokens WHERE user_id=?',(user_id,))
                        notification_id=uid()
                        insert(db,'notifications',notification_id=notification_id,senior_id=schedule['senior_id'],user_id=user_id,source_event_id=None,
                               schedule_id=schedule['schedule_id'],scheduled_date=date,notification_type='MEDICATION_REMINDER',title='복약 시간 안내',
                               body=f"{schedule['med_time']} {schedule['medication_name']} 복약 시간입니다.",deep_link='safehome://medications/'+schedule['schedule_id'],
                               delivery_status='MOCK_QUEUED' if has_token else 'NO_TOKEN',created_at=iso(current))
                        feed(db,schedule['senior_id'],'MEDICATION_REMINDER',{'notification_id':notification_id,'schedule_id':schedule['schedule_id'],'scheduled_date':date,'medication_name':schedule['medication_name'],'med_time':schedule['med_time']})
                if end <= current and schedule['created_at'] <= iso(end):
                    db.execute("INSERT OR IGNORE INTO medication_logs(log_id,schedule_id,scheduled_date,play_status) VALUES(?,?,?,'NO_ACTIVITY')",(uid(),schedule['schedule_id'],date))


def sync_data(db,user,senior_id,after,limit):
    authorize(db,user,senior_id)
    latest = db.execute('SELECT COALESCE(MAX(cursor),0) FROM event_feed').fetchone()[0]
    if after > latest:
        raise ApiError(409,'CURSOR_AHEAD','서버 초기화 등으로 커서가 유효하지 않습니다. 커서를 0으로 초기화하세요.')
    audience=" AND event_type IN ('MEDICATION_REMINDER','MEDICATION_UPDATED','MEDICATION_DELETED')" if user['role']=='SENIOR' else " AND event_type!='MEDICATION_REMINDER'" if user['role']=='GUARDIAN' else ''
    rows = many(db,'SELECT * FROM event_feed WHERE senior_id=? AND cursor>? AND cursor<=?'+audience+' ORDER BY cursor LIMIT ?', (str(senior_id),after,latest,limit+1))
    more, rows = len(rows)>limit, rows[:limit]
    events = [{**{k:v for k,v in row.items() if k!='data_json'},'data':json.loads(row['data_json'])} for row in rows]
    return {'events':events,'next_cursor':rows[-1]['cursor'] if more else latest,'latest_cursor':latest,'has_more':more}


ADMIN_EVENT_TYPES=('STATUS_CHANGED','INCIDENT_ALERT','DEVICE_STATUS_CHANGED','PATTERN_UPDATED')


def admin_sync_data(db,user,after,limit):
    if user['role']!='ADMIN':
        raise ApiError(403,'FORBIDDEN','관리자 권한이 필요합니다.')
    bounds=db.execute('SELECT COALESCE(MIN(cursor),0),COALESCE(MAX(cursor),0) FROM event_feed').fetchone()
    earliest,latest=bounds
    if after>latest:
        raise ApiError(409,'CURSOR_AHEAD','서버 기록이 변경되었습니다. 전체 현황을 다시 조회하고 커서를 새로 시작하세요.')
    if after and earliest and after<earliest-1:
        raise ApiError(409,'CURSOR_EXPIRED','이전 기록의 보관 범위를 벗어났습니다. 전체 현황과 사고 목록을 다시 조회하세요.')
    rows=many(db,"""SELECT f.*,s.name AS senior_name FROM event_feed f
        JOIN seniors s ON s.senior_id=f.senior_id
        WHERE f.cursor>? AND f.cursor<=? AND f.event_type IN (?,?,?,?)
        ORDER BY f.cursor LIMIT ?""",(after,latest,*ADMIN_EVENT_TYPES,limit+1))
    more,rows=len(rows)>limit,rows[:limit]
    events=[{**{k:v for k,v in row.items() if k!='data_json'},'data':json.loads(row['data_json'])} for row in rows]
    return {'events':events,'next_cursor':rows[-1]['cursor'] if more else latest,'latest_cursor':latest,'has_more':more}


def create_app(db_path=None, run_maintenance=True, enable_mock_auth=None, auth_session_ttl_seconds=3600):
    if auth_session_ttl_seconds <= 0:
        raise ValueError('Session lifetime must be positive')
    @asynccontextmanager
    async def lifespan(app):
        app.state.db = connect(db_path or os.getenv('DATABASE_PATH',str(ROOT/'data'/'mock.sqlite3')))
        seed(app.state.db)
        async def ticker():
            while True:
                await asyncio.sleep(2)
                try:
                    maintenance(app.state.db)
                except Exception:
                    logger.exception('Maintenance failed')
        task = asyncio.create_task(ticker()) if run_maintenance else None
        yield
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        app.state.db.close()

    errors={code:{'model':m.Error,'description':desc} for code,desc in [(400,'잘못된 요청'),(401,'인증 실패'),(403,'권한 없음'),(404,'대상 없음'),(409,'중복 키·상태 충돌'),(422,'입력 검증 실패'),(429,'로그인 재시도 제한'),(500,'서버 오류')]}
    app=FastAPI(title='5조 안심 서비스 — M1 Mock API',version='1.5.0',description='가상 데이터 전용 서버. 어르신 본인에게 복약 안내, 보호자에게 낙상·패턴 알림을 구분합니다. 실제 푸시 발송은 수행하지 않습니다.',lifespan=lifespan,responses=errors,swagger_ui_parameters={'persistAuthorization':True})
    origins=os.getenv('CORS_ORIGINS','http://localhost:3000,http://localhost:5173,http://localhost:8081,http://127.0.0.1:5173').split(',')
    app.state.enable_mock_auth = os.getenv('ENABLE_MOCK_AUTH', '1') == '1' if enable_mock_auth is None else enable_mock_auth
    app.add_middleware(CORSMiddleware,allow_origins=origins,allow_methods=['GET','POST','PATCH','DELETE','OPTIONS'],allow_headers=['Authorization','Content-Type','Idempotency-Key','X-Request-ID'],expose_headers=['X-Request-ID'],allow_credentials=False)

    @app.middleware('http')
    async def request_id(request,call_next):
        request.state.request_id=uid()
        try:
            response=await call_next(request)
        except Exception:
            logger.exception('Unhandled request error')
            response=error_response(request,500,'INTERNAL_ERROR','서버 처리 중 오류가 발생했습니다.')
        response.headers['X-Request-ID']=request.state.request_id
        return response

    def error_response(request,status,code,message,details=None):
        return JSONResponse(status_code=status,content={'error_code':code,'message':message,'details':details or [],'request_id':getattr(request.state,'request_id',uid()),'timestamp':iso()},headers={'WWW-Authenticate':'Bearer'} if status==401 else None)

    @app.exception_handler(ApiError)
    async def api_error(request,exc):
        return error_response(request,exc.status,exc.code,exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        details=[{'field':'.'.join(str(x) for x in e['loc']),'message':e['msg']} for e in exc.errors()]
        return error_response(request,422,'VALIDATION_ERROR','입력 형식이나 필수 항목을 확인하세요.',details)

    @app.exception_handler(HTTPException)
    async def http_error(request,exc):
        return error_response(request,exc.status_code,'NOT_FOUND' if exc.status_code==404 else 'HTTP_ERROR',str(exc.detail))

    @app.exception_handler(Exception)
    async def internal_error(request,exc):
        logger.exception('Unhandled request error',exc_info=exc)
        return error_response(request,500,'INTERNAL_ERROR','서버 처리 중 오류가 발생했습니다.')

    @app.get('/health',tags=['운영'])
    async def health(request:Request):
        request.app.state.db.execute('SELECT 1')
        return {'status':'ok','mode':'mock','version':'1.5.0'}

    @app.post('/api/v1/auth/mock-login',response_model=m.LoginResult,tags=['인증'])
    async def login(body:m.Login):
        if not app.state.enable_mock_auth:
            raise ApiError(403, 'MOCK_AUTH_DISABLED', '연습용 자동 로그인은 꺼져 있습니다.')
        role,user_id={'admin-demo':('ADMIN',ADMIN),'guardian-demo':('GUARDIAN',GUARDIAN),'senior-demo':('SENIOR',SENIOR_USER)}[body.account_id]
        return {'access_token':body.account_id+'-token','role':role,'user_id':user_id}

    @app.post('/api/v1/auth/login',response_model=m.PasswordLoginResult,tags=['인증'],summary='아이디·비밀번호 로그인')
    async def password_login(body:m.PasswordLogin,request:Request,response:Response):
        response.headers['Cache-Control']='no-store'
        # Password verification runs in this single-process application's serialized request path.
        return auth.login(request.app.state.db,body.account_id,body.password.get_secret_value(),request.client.host if request.client else 'unknown',auth_session_ttl_seconds)

    @app.get('/api/v1/auth/me',response_model=m.Profile,tags=['인증'],summary='내 로그인 정보 확인')
    async def me(response:Response,user=Depends(current_user)):
        response.headers['Cache-Control']='no-store'
        return {key:user[key] for key in ('user_id','account_id','user_name','role')}

    @app.post('/api/v1/auth/logout',status_code=204,tags=['인증'],summary='비밀번호 로그인 세션 종료')
    async def password_logout(request:Request,credentials:Annotated[HTTPAuthorizationCredentials | None,Depends(bearer)]):
        token=credentials.credentials if credentials and credentials.scheme.lower()=='bearer' else ''
        auth.logout(request.app.state.db,token)
        return Response(status_code=204,headers={'Cache-Control':'no-store'})

    @app.get('/api/v1/seniors',response_model=m.SeniorList,tags=['조회'])
    async def seniors(request:Request,user=Depends(current_user)):
        db=request.app.state.db
        condition='' if user['role']=='ADMIN' else 'WHERE s.user_id=?' if user['role']=='SENIOR' else 'JOIN senior_guardians g ON g.senior_id=s.senior_id WHERE g.guardian_id=?'
        rows=many(db,"SELECT s.senior_id,s.name,s.timezone,u.phone_number FROM seniors s LEFT JOIN users u ON u.user_id=s.user_id AND u.role='SENIOR' "+condition+' ORDER BY s.senior_id',() if user['role']=='ADMIN' else (user['user_id'],))
        return {'items':rows}

    @app.get('/api/v1/seniors/{senior_id}/status',response_model=m.Status,tags=['조회'])
    async def status(senior_id:UUID,request:Request,user=Depends(current_user)):
        maintenance(request.app.state.db)
        return status_data(request.app.state.db,authorize(request.app.state.db,user,senior_id))

    @app.get('/api/v1/admin/seniors',response_model=m.AdminSeniorPage,tags=['관리자'],summary='전체 어르신 상태·장치 연결 현황 조회')
    async def admin_seniors(request:Request,response:Response,page:int=Query(1,ge=1),size:int=Query(20,ge=1,le=100),user=Depends(admin_user)):
        db=request.app.state.db
        maintenance(db)
        total=db.execute('SELECT COUNT(*) FROM seniors').fetchone()[0]
        # One aggregate query, including seniors with no registered device.
        rows=many(db,"""
            SELECT s.senior_id,s.name,s.current_risk_level AS risk_level,
                   s.last_status_updated_at AS last_updated_at,s.status_event_id,
                   COALESCE(d.online,0) AS online,COALESCE(d.offline,0) AS offline
            FROM seniors s
            LEFT JOIN (
                SELECT senior_id,
                       SUM(CASE WHEN connection_status='ONLINE' THEN 1 ELSE 0 END) AS online,
                       SUM(CASE WHEN connection_status='OFFLINE' THEN 1 ELSE 0 END) AS offline
                FROM devices GROUP BY senior_id
            ) d ON d.senior_id=s.senior_id
            ORDER BY s.name COLLATE BINARY,s.senior_id
            LIMIT ? OFFSET ?
        """,(size,(page-1)*size)) if (page-1)*size < total else []
        items=[{'senior_id':row['senior_id'],'name':row['name'],'risk_level':row['risk_level'],
                'last_updated_at':row['last_updated_at'],'status_event_id':row['status_event_id'],
                'data_freshness':'STALE' if row['offline'] or not row['online'] else 'FRESH',
                'device_summary':{'online':row['online'],'offline':row['offline']}} for row in rows]
        response.headers['Cache-Control']='no-store'
        return {'items':items,'page':page,'size':size,'total':total,'total_pages':(total+size-1)//size}

    @app.get('/api/v1/seniors/{senior_id}/devices',response_model=m.DeviceList,tags=['조회'])
    async def devices(senior_id:UUID,request:Request,user=Depends(current_user)):
        db=request.app.state.db
        authorize(db,user,senior_id)
        maintenance(db)
        return {'items':many(db,'SELECT * FROM devices WHERE senior_id=? ORDER BY device_type',(str(senior_id),))}

    @app.get('/api/v1/seniors/{senior_id}/incidents',response_model=m.IncidentPage,tags=['사고'])
    async def incidents(senior_id:UUID,request:Request,page:int=Query(1,ge=1),size:int=Query(20,ge=1,le=100),start_at:datetime|None=None,end_at:datetime|None=None,user=Depends(current_user)):
        db=request.app.state.db
        authorize(db,user,senior_id)
        valid_dates(start_at,end_at)
        where,args='senior_id=?',[str(senior_id)]
        if start_at:
            where+=' AND event_time>=?';args.append(iso(start_at))
        if end_at:
            where+=' AND event_time<?';args.append(iso(end_at))
        total=db.execute('SELECT COUNT(*) FROM incidents WHERE '+where,args).fetchone()[0]
        rows=many(db,'SELECT * FROM incidents WHERE '+where+' ORDER BY event_time DESC,incident_id DESC LIMIT ? OFFSET ?',args+[size,(page-1)*size])
        return {'items':rows,'page':page,'size':size,'total':total,'total_pages':(total+size-1)//size}

    @app.get('/api/v1/incidents/{incident_id}',response_model=m.IncidentDetail,tags=['사고'])
    async def incident(incident_id:UUID,request:Request,user=Depends(current_user)):
        db=request.app.state.db
        row=require_record(one(db,'SELECT * FROM incidents WHERE incident_id=?',(str(incident_id),)))
        authorize(db,user,row['senior_id'])
        evidence=one(db,'SELECT s.piezo_impact_val,s.tof_estimated_height_mm,s.ai_fall_confidence,d.gesture_cancelled,d.snapshot_image_url FROM incident_details d JOIN sensor_logs s ON s.log_id=d.sensor_log_id WHERE d.incident_id=?',(str(incident_id),))
        return {**row,'evidence':evidence,**assessment_details(db,str(incident_id))}

    @app.get('/api/v1/admin/incidents',response_model=m.AdminIncidentPage,tags=['관리자'],summary='전체 사고 목록·기간·대상별 조회')
    async def admin_incidents(request:Request,response:Response,page:int=Query(1,ge=1),size:int=Query(20,ge=1,le=100),senior_id:UUID|None=None,start_at:datetime|None=None,end_at:datetime|None=None,user=Depends(admin_user)):
        valid_dates(start_at,end_at)
        clauses,args=[],[]
        if senior_id is not None:
            require_record(one(request.app.state.db,'SELECT 1 FROM seniors WHERE senior_id=?',(str(senior_id),)))
            clauses.append('i.senior_id=?');args.append(str(senior_id))
        if start_at is not None:
            clauses.append('i.event_time>=?');args.append(iso(start_at))
        if end_at is not None:
            clauses.append('i.event_time<?');args.append(iso(end_at))
        where=' WHERE '+' AND '.join(clauses) if clauses else ''
        db=request.app.state.db
        total=db.execute('SELECT COUNT(*) FROM incidents i'+where,args).fetchone()[0]
        rows=many(db,'SELECT i.*,s.name AS senior_name FROM incidents i JOIN seniors s ON s.senior_id=i.senior_id'+where+' ORDER BY i.event_time DESC,i.incident_id DESC LIMIT ? OFFSET ?',args+[size,(page-1)*size]) if (page-1)*size<total else []
        response.headers['Cache-Control']='no-store'
        return {'items':rows,'page':page,'size':size,'total':total,'total_pages':(total+size-1)//size}

    @app.get('/api/v1/admin/sync',response_model=m.AdminSyncResult,tags=['관리자'],summary='관리자 경고 기록 재조회·실시간 복구 기준점')
    async def admin_sync(request:Request,response:Response,after_cursor:int=Query(0,ge=0,le=9_007_199_254_740_991),limit:int=Query(100,ge=1,le=100),user=Depends(admin_user)):
        maintenance(request.app.state.db)
        response.headers['Cache-Control']='no-store'
        return admin_sync_data(request.app.state.db,user,after_cursor,limit)

    @app.get('/api/v1/seniors/{senior_id}/patterns',response_model=m.PatternList,tags=['생활 패턴'])
    async def patterns(senior_id:UUID,request:Request,start_at:datetime|None=None,end_at:datetime|None=None,user=Depends(current_user)):
        db=request.app.state.db
        authorize(db,user,senior_id)
        valid_dates(start_at,end_at)
        where,args='senior_id=?',[str(senior_id)]
        if start_at:
            where+=' AND observation_end_time>?';args.append(iso(start_at))
        if end_at:
            where+=' AND observation_start_time<?';args.append(iso(end_at))
        return {'items':many(db,'SELECT * FROM lifestyle_patterns WHERE '+where+' ORDER BY observation_start_time DESC,pattern_id DESC',args)}

    @app.get('/api/v1/voices',response_model=m.VoiceList,tags=['복약'])
    async def voices(request:Request,user=Depends(current_user)):
        return {'items':many(request.app.state.db,'SELECT * FROM voice_assets WHERE is_active=1 ORDER BY voice_id')}

    @app.get('/api/v1/seniors/{senior_id}/medications',response_model=list[m.Medication],tags=['복약'])
    async def medications(senior_id:UUID,request:Request,user=Depends(current_user)):
        db=request.app.state.db
        authorize(db,user,senior_id)
        return [medication_data(x) for x in many(db,'SELECT * FROM medication_schedules WHERE senior_id=? AND deleted_at IS NULL ORDER BY med_time,schedule_id',(str(senior_id),))]

    @app.post('/api/v1/seniors/{senior_id}/medications',response_model=m.Medication,status_code=201,tags=['복약'])
    async def create_medication(senior_id:UUID,body:m.MedicationCreate,request:Request,idempotency_key:Annotated[str,Header(min_length=8,max_length=128)],user=Depends(current_user)):
        db=request.app.state.db
        authorize(db,user,senior_id)
        route=f'/seniors/{senior_id}/medications'
        fingerprint=hashlib.sha256(dumps(body.model_dump(mode='json')).encode()).hexdigest()
        with transaction(db):
            existing=one(db,'SELECT * FROM idempotency_keys WHERE principal_id=? AND route_key=? AND idempotency_key=?',(user['user_id'],route,idempotency_key))
            if existing:
                if existing['request_hash']!=fingerprint:
                    raise ApiError(409,'IDEMPOTENCY_KEY_CONFLICT','동일 키에 다른 요청 본문을 사용할 수 없습니다.')
                return json.loads(existing['response_json'])
            schedule_id,t=uid(),iso()
            values=body.model_dump()
            values['days_of_week_json']=dumps(values.pop('days_of_week'))
            values['is_active']=int(values['is_active'])
            insert(db,'medication_schedules',schedule_id=schedule_id,senior_id=str(senior_id),**values,created_at=t,updated_at=t,deleted_at=None)
            result=medication_data(one(db,'SELECT * FROM medication_schedules WHERE schedule_id=?',(schedule_id,)))
            insert(db,'idempotency_keys',principal_id=user['user_id'],route_key=route,idempotency_key=idempotency_key,request_hash=fingerprint,response_json=dumps(result),created_at=t)
            feed(db,str(senior_id),'MEDICATION_UPDATED',{'schedule_id':schedule_id,'operation':'CREATED'})
        return result

    @app.patch('/api/v1/medications/{schedule_id}',response_model=m.Medication,tags=['복약'])
    async def patch_medication(schedule_id:UUID,body:m.MedicationPatch,request:Request,user=Depends(current_user)):
        db=request.app.state.db
        row=require_record(one(db,'SELECT * FROM medication_schedules WHERE schedule_id=? AND deleted_at IS NULL',(str(schedule_id),)))
        authorize(db,user,row['senior_id'])
        values=body.model_dump(exclude_unset=True)
        if 'days_of_week' in values:
            values['days_of_week_json']=dumps(values.pop('days_of_week'))
        values['updated_at']=iso()
        with transaction(db):
            db.execute('UPDATE medication_schedules SET '+','.join(k+'=?' for k in values)+' WHERE schedule_id=?',list(values.values())+[str(schedule_id)])
            # Cancel pending commands when the schedule is changed, so old settings cannot play.
            db.execute("UPDATE medication_logs SET play_status='FAILED' WHERE log_id IN (SELECT log_id FROM speaker_commands WHERE status='PENDING' AND log_id IN (SELECT log_id FROM medication_logs WHERE schedule_id=?))",(str(schedule_id),))
            db.execute("UPDATE speaker_commands SET status='EXPIRED' WHERE status='PENDING' AND log_id IN (SELECT log_id FROM medication_logs WHERE schedule_id=?)",(str(schedule_id),))
            feed(db,row['senior_id'],'MEDICATION_UPDATED',{'schedule_id':str(schedule_id),'operation':'UPDATED'})
        return medication_data(one(db,'SELECT * FROM medication_schedules WHERE schedule_id=?',(str(schedule_id),)))

    @app.delete('/api/v1/medications/{schedule_id}',status_code=204,tags=['복약'])
    async def delete_medication(schedule_id:UUID,request:Request,user=Depends(current_user)):
        db=request.app.state.db
        row=require_record(one(db,'SELECT * FROM medication_schedules WHERE schedule_id=?',(str(schedule_id),)))
        authorize(db,user,row['senior_id'])
        if not row['deleted_at']:
            with transaction(db):
                db.execute('UPDATE medication_schedules SET is_active=0,deleted_at=?,updated_at=? WHERE schedule_id=?',(iso(),iso(),str(schedule_id)))
                db.execute("UPDATE medication_logs SET play_status='FAILED' WHERE log_id IN (SELECT log_id FROM speaker_commands WHERE status='PENDING' AND log_id IN (SELECT log_id FROM medication_logs WHERE schedule_id=?))",(str(schedule_id),))
                db.execute("UPDATE speaker_commands SET status='EXPIRED' WHERE status='PENDING' AND log_id IN (SELECT log_id FROM medication_logs WHERE schedule_id=?)",(str(schedule_id),))
                feed(db,row['senior_id'],'MEDICATION_DELETED',{'schedule_id':str(schedule_id)})
        return Response(status_code=204)

    @app.get('/api/v1/seniors/{senior_id}/medication-logs',response_model=m.MedicationLogList,tags=['복약'])
    async def medication_logs(senior_id:UUID,request:Request,user=Depends(current_user)):
        db=request.app.state.db
        authorize(db,user,senior_id)
        return {'items':many(db,'SELECT l.* FROM medication_logs l JOIN medication_schedules s ON s.schedule_id=l.schedule_id WHERE s.senior_id=? ORDER BY l.scheduled_date DESC,l.log_id',(str(senior_id),))}

    @app.post('/api/v1/notifications/token',response_model=m.PushTokenResult,tags=['알림'])
    async def push_token(body:m.PushTokenCreate,request:Request,user=Depends(current_user)):
        db=request.app.state.db
        with transaction(db):
            collision=one(db,'SELECT * FROM push_tokens WHERE provider=? AND push_token=?',(body.provider,body.push_token))
            if collision and (collision['user_id']!=user['user_id'] or collision['installation_id']!=str(body.installation_id)):
                raise ApiError(409,'TOKEN_ALREADY_REGISTERED','이미 다른 사용자 또는 설치에 등록된 토큰입니다.')
            row=one(db,'SELECT * FROM push_tokens WHERE user_id=? AND installation_id=?',(user['user_id'],str(body.installation_id)))
            token_id=row['token_id'] if row else uid()
            db.execute('INSERT INTO push_tokens VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id,installation_id) DO UPDATE SET provider=excluded.provider,push_token=excluded.push_token,device_os=excluded.device_os,updated_at=excluded.updated_at',(token_id,user['user_id'],str(body.installation_id),body.provider,body.push_token,body.device_os,iso()))
        return {k:v for k,v in one(db,'SELECT * FROM push_tokens WHERE token_id=?',(token_id,)).items() if k not in ('user_id','push_token')}

    @app.delete('/api/v1/notifications/token/{installation_id}',status_code=204,tags=['알림'])
    async def delete_token(installation_id:UUID,request:Request,user=Depends(current_user)):
        request.app.state.db.execute('DELETE FROM push_tokens WHERE user_id=? AND installation_id=?',(user['user_id'],str(installation_id)))
        return Response(status_code=204)

    @app.get('/api/v1/notifications',response_model=m.NotificationList,tags=['알림'])
    async def notifications(request:Request,user=Depends(current_user)):
        audience=" AND notification_type='MEDICATION_REMINDER'" if user['role']=='SENIOR' else ''
        return {'items':many(request.app.state.db,'SELECT * FROM notifications WHERE user_id=?'+audience+' ORDER BY created_at DESC',(user['user_id'],))}

    @app.get('/api/v1/seniors/{senior_id}/sync',response_model=m.SyncResult,tags=['동기화'])
    async def sync(senior_id:UUID,request:Request,after_cursor:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=500),user=Depends(current_user)):
        return sync_data(request.app.state.db,user,senior_id,after_cursor,limit)

    @app.get('/api/v1/patterns/{pattern_id}',response_model=m.Pattern,tags=['생활 패턴'])
    async def pattern_detail(pattern_id:UUID,request:Request,user=Depends(current_user)):
        db=request.app.state.db
        row=require_record(one(db,'SELECT * FROM lifestyle_patterns WHERE pattern_id=?',(str(pattern_id),)))
        authorize(db,user,row['senior_id'])
        return row

    for path,model,handler in [('risk-events',m.RiskEvent,risk_ingest),('activities',m.ActivityEvent,activity_ingest),('heartbeats',m.Heartbeat,heartbeat_ingest),('patterns',m.PatternInput,pattern_ingest)]:
        def make_ingest(model,handler):
            async def endpoint(body,request:Request,device=Depends(device_user)):
                db=request.app.state.db
                scope_device(db,device,body.device_id,body.senior_id)
                return handler(db,body)
            endpoint.__annotations__['body']=model
            return endpoint
        app.add_api_route('/api/v1/ingest/'+path,make_ingest(model,handler),methods=['POST'],response_model=m.IngestResult,tags=['게이트웨이'],name='ingest_'+path.replace('-','_'))

    @app.post('/api/v1/ingest/fall-assessments',response_model=m.FallAssessmentResult,tags=['게이트웨이'],summary='같은 사건의 1차 의심·카메라 재검증 수신')
    async def ingest_fall_assessment(body:m.FallAssessment,request:Request,device=Depends(device_user)):
        scope_device(request.app.state.db,device,body.device_id,body.senior_id)
        return process_assessment(request.app.state.db,body,save_event,notify)

    @app.post('/api/v1/mock/fall-assessments',response_model=m.FallAssessmentResult,tags=['시연'],summary='카메라 재검증 흐름 관리자 모의 입력')
    async def mock_fall_assessment(body:m.FallAssessment,request:Request,user=Depends(admin_user)):
        db=request.app.state.db
        authorize(db,user,body.senior_id)
        target=require_record(one(db,'SELECT * FROM devices WHERE device_id=?',(str(body.device_id),)))
        if target['senior_id']!=str(body.senior_id):
            raise ApiError(403,'DEVICE_SCOPE_MISMATCH','장치와 대상이 일치하지 않습니다.')
        return process_assessment(db,body,save_event,notify)

    @app.get('/api/v1/devices/{device_id}/commands',response_model=m.CommandList,tags=['게이트웨이'])
    async def commands(device_id:UUID,request:Request,device=Depends(device_user)):
        db=request.app.state.db
        scope_device(db,device,device_id)
        maintenance(db)
        return {'items':many(db,"SELECT * FROM speaker_commands WHERE device_id=? AND status='PENDING' ORDER BY created_at",(str(device_id),))}

    @app.post('/api/v1/commands/{command_id}/ack',response_model=m.Command,tags=['게이트웨이'])
    async def ack(command_id:UUID,body:m.CommandAck,request:Request,device=Depends(device_user)):
        db=request.app.state.db
        maintenance(db)
        row=require_record(one(db,'SELECT * FROM speaker_commands WHERE command_id=?',(str(command_id),)))
        target=scope_device(db,device,row['device_id'])
        if row['status'] == body.status:
            return row
        if row['status'] != 'PENDING':
            raise ApiError(409,'COMMAND_ALREADY_FINAL','명령이 이미 종료되었거나 만료되었습니다.')
        t=iso()
        with transaction(db):
            db.execute('UPDATE speaker_commands SET status=?,acknowledged_at=? WHERE command_id=?',(body.status,t,str(command_id)))
            db.execute('UPDATE medication_logs SET play_status=?,voice_played_at=? WHERE log_id=?',(body.status,t if body.status=='SUCCESS' else None,row['log_id']))
            feed(db,target['senior_id'],'SPEAKER_COMMAND_ACK',{'command_id':str(command_id),'status':body.status})
        return one(db,'SELECT * FROM speaker_commands WHERE command_id=?',(str(command_id),))

    @app.post('/api/v1/mock/scenarios',response_model=m.ScenarioResult,tags=['시연'])
    async def scenario(body:m.Scenario,request:Request,user=Depends(admin_user)):
        db=request.app.state.db
        authorize(db,user,body.senior_id)
        event_id,senior_id=uid(),str(body.senior_id)
        gateway=require_record(one(db,"SELECT * FROM devices WHERE senior_id=? AND device_type='GATEWAY'",(senior_id,)))
        base={'event_id':event_id,'senior_id':senior_id,'device_id':gateway['device_id'],'occurred_at':now()}
        kind=body.scenario
        if kind in ('NORMAL','CAUTION','DANGER','CANCELLED'):
            result=risk_ingest(db,m.RiskEvent(**base,risk_level='DANGER' if kind=='CANCELLED' else kind,gesture_cancelled=kind=='CANCELLED',piezo_impact_val=850,tof_estimated_height_mm=200,ai_fall_confidence=0.96,summary='시연: '+kind))
        elif kind in ('OFFLINE','ONLINE'):
            # Each component reports its own heartbeat; the scenario simulates all components.
            result=None
            for target in many(db,'SELECT device_id FROM devices WHERE senior_id=?',(senior_id,)):
                value={**base,'device_id':target['device_id'],'event_id':event_id if result is None else uid()}
                item=heartbeat_ingest(db,m.Heartbeat(**value,connection_status=kind))
                if result is None:
                    result=item
        elif kind=='PATTERN':
            result=pattern_ingest(db,m.PatternInput(**base,pattern_type='NIGHT_ACTIVITY_SURGE',observation_start_time=now()-timedelta(hours=8),observation_end_time=now(),metric_value=18,metric_unit='count',summary_note='시연: 야간 활동 증가',is_anomaly=True))
        else:
            result=activity_ingest(db,m.ActivityEvent(**base,activity_type='WAKE_UP'))
        return {'scenario':kind,'event_id':event_id,'result':result}

    @app.post('/api/v1/mock/reset',response_model=m.ResetResult,tags=['시연'])
    async def reset(request:Request,user=Depends(admin_user)):
        db=request.app.state.db
        saved_passwords=many(db,'SELECT user_id,password_hash FROM users WHERE password_hash IS NOT NULL')
        # Keep cursor monotonic across reset; AUTOINCREMENT sqlite_sequence is intentionally retained.
        tables=['auth_sessions','auth_login_attempts','incident_assessment_events','idempotency_keys','event_feed','notifications','push_tokens','speaker_commands','medication_logs','medication_schedules','lifestyle_patterns','incident_details','incidents','sensor_logs','ingested_events','device_logs','devices','senior_guardians','voice_assets','seniors','users']
        with transaction(db):
            db.execute('UPDATE seniors SET status_event_id=NULL')
            for table in tables:
                db.execute('DELETE FROM '+table)
        seed(db)
        for saved in saved_passwords:
            db.execute('UPDATE users SET password_hash=? WHERE user_id=?',(saved['password_hash'],saved['user_id']))
        return {'reset':True,'message':'가상 데이터를 초기화했습니다. 클라이언트에서 목록을 다시 조회하고 커서를 0으로 초기화하세요.'}

    @app.post('/api/v1/mock/medication-tick',response_model=m.ReminderTickResult,tags=['시연'])
    async def medication_tick(request:Request,user=Depends(admin_user)):
        db=request.app.state.db
        before=db.execute("SELECT COUNT(*) FROM notifications WHERE notification_type='MEDICATION_REMINDER'").fetchone()[0]
        maintenance(db)
        after=db.execute("SELECT COUNT(*) FROM notifications WHERE notification_type='MEDICATION_REMINDER'").fetchone()[0]
        return {'generated_count':after-before,'processed_at':iso()}

    @app.websocket('/ws/live')
    async def live(ws:WebSocket):
        await ws.accept()
        try:
            body=await asyncio.wait_for(ws.receive_json(),10)
            if body.get('action')!='SUBSCRIBE':
                raise ApiError(400,'INVALID_SUBSCRIPTION','첫 메시지는 SUBSCRIBE여야 합니다.')
            db=ws.app.state.db
            user=user_from_token(db,body.get('access_token',''),ws.app.state.enable_mock_auth)
            try:
                senior_id=str(UUID(body.get('senior_id','')))
                cursor=int(body.get('after_cursor',0))
                if cursor<0:
                    raise ValueError()
            except (ValueError,TypeError,AttributeError):
                raise ApiError(422,'VALIDATION_ERROR','senior_id(UUID)와 0 이상의 after_cursor가 필요합니다.')
            authorize(db,user,senior_id)
            sync_data(db,user,senior_id,cursor,1)
            await ws.send_json({'type':'SUBSCRIBED','senior_id':senior_id,'after_cursor':cursor})
            while True:
                user=user_from_token(db,body.get('access_token',''),ws.app.state.enable_mock_auth)
                result=sync_data(db,user,senior_id,cursor,100)
                for event in result['events']:
                    await ws.send_json({'type':'EVENT',**event})
                cursor=result['next_cursor']
                # Cursor messages allow clients to persist progress even without visible events.
                await ws.send_json({'type':'CURSOR','cursor':cursor,'has_more':result['has_more']})
                if result['has_more']:
                    continue
                try:
                    message=await asyncio.wait_for(ws.receive_json(),0.25)
                    if message.get('action')=='PING':
                        await ws.send_json({'type':'PONG','timestamp':iso()})
                except asyncio.TimeoutError:
                    pass
        except ApiError as exc:
            await ws.send_json({'type':'ERROR','error_code':exc.code,'message':exc.message})
            await ws.close(code=4401 if exc.status==401 else 4403 if exc.status==403 else 4400)
        except (ValueError,TypeError,AttributeError,asyncio.TimeoutError):
            await ws.send_json({'type':'ERROR','error_code':'INVALID_MESSAGE','message':'유효한 JSON 구독 메시지를 10초 안에 보내세요.'})
            await ws.close(code=4400)
        except WebSocketDisconnect:
            pass

    @app.websocket('/ws/admin/live')
    async def admin_live(ws:WebSocket):
        await ws.accept()
        try:
            raw=await asyncio.wait_for(ws.receive_json(),10)
            try:
                subscription=m.AdminSubscription.model_validate(raw)
            except ValidationError:
                raise ApiError(422,'INVALID_SUBSCRIPTION','SUBSCRIBE, 토큰, 0 이상의 정수 after_cursor를 확인하세요.')
            db=ws.app.state.db
            user=user_from_token(db,subscription.access_token,ws.app.state.enable_mock_auth)
            # Both the HTTP replay route and this socket enforce administrator scope.
            initial=admin_sync_data(db,user,subscription.after_cursor,100)
            initial_latest=initial['latest_cursor']
            cursor=subscription.after_cursor
            await ws.send_json({'type':'SUBSCRIBED','after_cursor':cursor,'latest_cursor':initial_latest})
            while True:
                user=user_from_token(db,subscription.access_token,ws.app.state.enable_mock_auth)
                result=admin_sync_data(db,user,cursor,100)
                for event in result['events']:
                    await ws.send_json({'type':'EVENT',**event,'replayed':event['cursor']<=initial_latest})
                cursor=result['next_cursor']
                await ws.send_json({'type':'CURSOR','cursor':cursor,'has_more':result['has_more']})
                if result['has_more']:
                    continue
                try:
                    message=await asyncio.wait_for(ws.receive_json(),0.25)
                    if not isinstance(message,dict) or message.get('action')!='PING':
                        raise ApiError(400,'INVALID_MESSAGE','구독 후에는 PING 메시지만 보낼 수 있습니다.')
                    await ws.send_json({'type':'PONG','timestamp':iso()})
                except asyncio.TimeoutError:
                    pass
        except ApiError as exc:
            await ws.send_json({'type':'ERROR','error_code':exc.code,'message':exc.message})
            await ws.close(code=4401 if exc.status==401 else 4403 if exc.status==403 else 4409 if exc.status==409 else 4400)
        except (ValueError,TypeError,AttributeError,asyncio.TimeoutError):
            await ws.send_json({'type':'ERROR','error_code':'INVALID_MESSAGE','message':'유효한 JSON 구독 메시지를 10초 안에 보내세요.'})
            await ws.close(code=4400)
        except WebSocketDisconnect:
            pass

    app.mount('/assets',StaticFiles(directory=ROOT/'static'),name='assets')
    @app.get('/admin-test',include_in_schema=False)
    async def admin_test():
        return FileResponse(ROOT/'static'/'admin-test.html',headers={'Cache-Control':'no-store'})
    @app.get('/login-test',include_in_schema=False)
    async def login_test():
        return FileResponse(ROOT/'static'/'login-test.html',headers={'Cache-Control':'no-store'})
    app.mount('/design',StaticFiles(directory=ROOT/'docs',html=True),name='design')

    @app.get('/',include_in_schema=False)
    async def index():
        return FileResponse(ROOT/'static'/'index.html')

    summaries={'health':'서버 상태 확인','login':'가상 계정 로그인','seniors':'접근 가능한 어르신 목록','status':'현재 위험·장치 상태 조회','devices':'장치 연결 상태 조회','incidents':'사고 이력 페이징 조회','incident':'사고 상세와 센서 근거 조회','patterns':'생활 패턴 기간 조회','pattern_detail':'생활 패턴 상세 조회','voices':'안내 음성 목록','medications':'복약 일정 목록','create_medication':'복약 일정 등록','patch_medication':'복약 일정 부분 수정','delete_medication':'복약 일정 삭제','medication_logs':'음성 안내 이력 조회','push_token':'푸시 토큰 등록·갱신','delete_token':'설치별 푸시 토큰 해제','notifications':'가상 푸시 처리 기록 조회','sync':'커서 이후 이벤트 동기화','commands':'미완료 음성 명령 조회','ack':'음성 재생 결과 응답','scenario':'가상 센서 시나리오 실행','reset':'가상 데이터 초기화','ingest_risk_events':'센서·AI 위험 이벤트 수신','ingest_activities':'재실·기상·활동 이벤트 수신','ingest_heartbeats':'장치 하트비트 수신','ingest_patterns':'AI 생활 패턴 결과 수신'}
    for route in app.routes:
        if route.name in summaries:
            route.summary=summaries[route.name]
    original_openapi=app.openapi
    def openapi_with_examples():
        spec=original_openapi()
        path=ROOT/'docs'/'examples.json'
        if path.exists():
            for key,example in json.loads(path.read_text(encoding='utf-8')).items():
                method,route=key.split(' ',1)
                operation=spec.get('paths',{}).get(route,{}).get(method.lower())
                if not operation:
                    continue
                content=operation.get('responses',{}).get(str(example['status']),{}).get('content',{}).get('application/json')
                if content is not None and example['response'] is not None:
                    content['example']=example['response']
                if example['request'] is not None:
                    operation['requestBody']['content']['application/json']['example']=example['request']
        return spec
    app.openapi=openapi_with_examples
    return app


app=create_app()
