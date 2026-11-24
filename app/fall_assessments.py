"""Proposed two-stage camera workflow. Existing one-shot risk ingest stays supported."""
import json

from .db import feed, insert, iso, many, one, transaction, uid
from .errors import ApiError


def _latest(db, incident_id):
    return one(db,'''SELECT e.*,a.applied FROM incident_assessment_events a
        JOIN ingested_events e ON e.event_id=a.event_id
        WHERE a.incident_id=? AND a.applied=1 ORDER BY a.assessment_seq DESC LIMIT 1''',(incident_id,))


def _state(row):
    payload=json.loads(row['payload_json'])
    return {'stage':payload['stage'],'analysis_status':payload['analysis_status'],
            'confirmed_fall':payload['confirmed_fall'],'camera_confidence':payload['camera_confidence'],
            'last_event_id':row['event_id'],'updated_at':row['received_at']}


def assessment_details(db, incident_id):
    current=_latest(db,incident_id)
    if not current:
        return {'assessment':None,'assessment_history':[],'assessment_history_has_more':False}
    rows=many(db,'''SELECT e.*,a.applied FROM incident_assessment_events a
        JOIN ingested_events e ON e.event_id=a.event_id
        WHERE a.incident_id=? ORDER BY a.assessment_seq DESC LIMIT 51''',(incident_id,))
    records=[]
    for row in reversed(rows[:50]):
        payload=json.loads(row['payload_json'])
        records.append({key:row[key] for key in ('event_id','device_id','occurred_at','received_at')})
        records[-1].update({key:payload[key] for key in ('stage','analysis_status','confirmed_fall','camera_confidence','summary')})
        records[-1]['applied']=bool(row['applied'])
    return {'assessment':_state(current),'assessment_history':records,'assessment_history_has_more':len(rows)>50}


def _result(db, payload, duplicate, state_updated):
    incident_id=str(payload.incident_id)
    return {'event_id':str(payload.event_id),'incident_id':incident_id,'duplicate':duplicate,
            'state_updated':state_updated,'incident_status':one(db,'SELECT status FROM incidents WHERE incident_id=?',(incident_id,))['status'],
            'assessment':_state(_latest(db,incident_id))}


def _gateway(db, device_id):
    device=one(db,'SELECT * FROM devices WHERE device_id=?',(device_id,))
    if not device:
        raise ApiError(404,'NOT_FOUND','장치를 찾을 수 없습니다.')
    return device['device_id'] if device['device_type']=='GATEWAY' else device['parent_device_id']


def _change_state(db, senior_id, event_id, occurred_at, risk_level):
    db.execute('UPDATE seniors SET current_risk_level=?,last_status_updated_at=?,status_event_id=? WHERE senior_id=?',
               (risk_level,occurred_at,event_id,senior_id))
    feed(db,senior_id,'STATUS_CHANGED',{'risk_level':risk_level,'last_updated_at':occurred_at,'status_event_id':event_id},occurred_at)


def process_assessment(db, payload, save_event, notify):
    event_id,incident_id,senior_id=str(payload.event_id),str(payload.incident_id),str(payload.senior_id)
    with transaction(db):
        fresh=save_event(db,payload,'FALL_ASSESSMENT')
        if not fresh:
            linked=one(db,'SELECT 1 FROM incident_assessment_events WHERE event_id=? AND incident_id=?',(event_id,incident_id))
            if not linked:
                raise ApiError(409,'EVENT_ID_CONFLICT','다른 입력에 사용된 event_id입니다.')
            return _result(db,payload,True,False)
        senior=one(db,'SELECT * FROM seniors WHERE senior_id=?',(senior_id,))
        if not senior:
            raise ApiError(404,'NOT_FOUND','어르신을 찾을 수 없습니다.')
        incident=one(db,'SELECT * FROM incidents WHERE incident_id=?',(incident_id,))
        t=iso(payload.occurred_at)
        state_updated=False
        if payload.stage=='PRELIMINARY':
            if incident:
                raise ApiError(409,'INCIDENT_ID_CONFLICT','같은 사건의 1차 의심을 새 event_id로 다시 만들 수 없습니다.')
            sensor_id=uid()
            insert(db,'sensor_logs',log_id=sensor_id,event_id=event_id,piezo_impact_val=None,tof_estimated_height_mm=None,ai_fall_confidence=None)
            insert(db,'incidents',incident_id=incident_id,event_id=event_id,senior_id=senior_id,event_time=t,
                   risk_level='CAUTION',summary=payload.summary,status='OPEN',created_at=iso())
            insert(db,'incident_details',incident_id=incident_id,sensor_log_id=sensor_id,gesture_cancelled=0,snapshot_image_url=None)
            insert(db,'incident_assessment_events',event_id=event_id,incident_id=incident_id,applied=1)
            if senior['status_event_id'] is None or (t,event_id)>(senior['last_status_updated_at'],senior['status_event_id']):
                _change_state(db,senior_id,event_id,t,'CAUTION');state_updated=True
            feed(db,senior_id,'INCIDENT_ALERT',{'incident_id':incident_id,'risk_level':'CAUTION','summary':payload.summary,
                 'stage':'PRELIMINARY','analysis_status':payload.analysis_status},t)
            return _result(db,payload,False,state_updated)

        current=_latest(db,incident_id)
        if not incident or not current:
            raise ApiError(409,'PRELIMINARY_REQUIRED','먼저 같은 incident_id의 1차 의심 기록을 보내세요.')
        initial=one(db,'SELECT * FROM ingested_events WHERE event_id=?',(incident['event_id'],))
        if incident['senior_id']!=senior_id or _gateway(db,initial['device_id'])!=_gateway(db,str(payload.device_id)):
            raise ApiError(403,'INCIDENT_SCOPE_MISMATCH','같은 대상·게이트웨이의 사건만 재검증할 수 있습니다.')
        previous=json.loads(current['payload_json'])
        if previous['analysis_status']=='COMPLETED':
            same=all(previous[key]==getattr(payload,key) for key in ('analysis_status','confirmed_fall','camera_confidence','summary'))
            if not same:
                raise ApiError(409,'ASSESSMENT_ALREADY_FINAL','확정된 최종 결과를 다른 내용으로 덮어쓸 수 없습니다.')
            # A sender using another transport event ID cannot duplicate alerts or incident state.
            insert(db,'incident_assessment_events',event_id=event_id,incident_id=incident_id,applied=0)
            return _result(db,payload,True,False)
        if t<initial['occurred_at'] or t<current['occurred_at']:
            raise ApiError(409,'OUT_OF_ORDER_ASSESSMENT','이전 처리보다 오래된 재검증 결과입니다.')
        owns_state=bool(one(db,'SELECT 1 FROM incident_assessment_events WHERE incident_id=? AND event_id=? AND applied=1',
                            (incident_id,senior['status_event_id'])))
        insert(db,'incident_assessment_events',event_id=event_id,incident_id=incident_id,applied=1)
        completed=payload.analysis_status=='COMPLETED'
        confirmed=completed and payload.confirmed_fall is True
        cancelled=completed and payload.confirmed_fall is False
        risk='DANGER' if confirmed else incident['risk_level']
        status='CANCELLED' if cancelled else 'OPEN'
        db.execute('UPDATE incidents SET risk_level=?,status=?,summary=? WHERE incident_id=?',(risk,status,payload.summary,incident_id))
        if completed and owns_state:
            _change_state(db,senior_id,event_id,t,'DANGER' if confirmed else 'NORMAL');state_updated=True
        # Reuse the existing administrator/guardian channel; clients re-read the canonical detail.
        feed(db,senior_id,'INCIDENT_ALERT',{'incident_id':incident_id,'risk_level':risk,'status':status,
             'summary':payload.summary,'stage':'FINAL','analysis_status':payload.analysis_status,
             'confirmed_fall':payload.confirmed_fall},t)
        if confirmed:
            notify(db,senior_id,event_id,'FALL_ALERT','카메라 재검증: 낙상 확인',payload.summary,'safehome://incidents/'+incident_id)
        return _result(db,payload,False,state_updated)
