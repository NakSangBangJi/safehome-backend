import {api,ensureAdmin,date} from './admin-test.js?v=1.5.0';
import {showDetail} from './admin-operations.js?v=1.5.0';
const $=id=>document.getElementById(id);
const statuses={PENDING_VERIFICATION:'카메라 대기',COMPLETED:'판단 완료',CAMERA_UNAVAILABLE:'카메라 실패 · 의심 유지',CAMERA_TIMEOUT:'시간 초과 · 의심 유지',INSUFFICIENT_DATA:'데이터 부족 · 의심 유지'};
let workflow=null,lastPayload=null,busy=false;
function controls(){document.querySelectorAll('[data-assessment]').forEach(button=>{const action=button.dataset.assessment;button.disabled=busy||(action==='REPEAT'?!lastPayload:action==='PRELIMINARY'?false:!workflow?.accepted||workflow.finalized);});$('assessmentDetail').disabled=busy||!workflow?.accepted;}
async function render(result){
  workflow.accepted=true;workflow.finalized=result.assessment.analysis_status==='COMPLETED';
  const decision=result.assessment.confirmed_fall;
  $('assessmentNotice').classList.remove('error');
  $('assessmentNotice').textContent=`대상: ${workflow.name}\n같은 사건 ID: ${result.incident_id}\n${statuses[result.assessment.analysis_status]}${decision===null?'':decision?' · 낙상 확정':' · 오탐 취소'}\n`+(result.duplicate?'중복 요청입니다. 새 사고·알림을 추가하지 않았습니다.':result.state_updated?'이 사건의 현재 상태를 갱신했습니다.':'사건 기록을 갱신하고 기존 현재 상태를 유지했습니다.');
  $('assessmentResponse').textContent=JSON.stringify(result,null,2);
  const detail=await api('/incidents/'+result.incident_id);$('assessmentHistory').replaceChildren();
  for(const entry of detail.assessment_history){const line=document.createElement('li');line.textContent=`${date(entry.occurred_at)} · ${entry.stage==='PRELIMINARY'?'1차 의심':'재검증'} · ${statuses[entry.analysis_status]}${entry.applied?'':' · 중복, 미반영'}`;$('assessmentHistory').append(line);}
}
async function submit(action){
  if(busy)return;busy=true;controls();
  try{
    await ensureAdmin();
    if(action!=='REPEAT'){
      if(action==='PRELIMINARY'){
        const select=$('scenarioSenior');if(!select.value)throw new Error('시연 대상 목록이 준비된 뒤 다시 눌러주세요.');
        const devices=(await api('/seniors/'+select.value+'/devices')).items;const gateway=devices.find(d=>d.device_type==='GATEWAY');
        if(!gateway)throw new Error('이 대상에 게이트웨이가 없습니다.');
        workflow={incident_id:crypto.randomUUID(),senior_id:select.value,device_id:gateway.device_id,name:select.selectedOptions[0].textContent,accepted:false,finalized:false};
      }
      const first=action==='PRELIMINARY',completed=action==='CONFIRMED'||action==='CANCELLED';
      lastPayload={event_id:crypto.randomUUID(),incident_id:workflow.incident_id,senior_id:workflow.senior_id,device_id:workflow.device_id,occurred_at:new Date().toISOString(),stage:first?'PRELIMINARY':'FINAL',analysis_status:first?'PENDING_VERIFICATION':completed?'COMPLETED':action==='TIMEOUT'?'CAMERA_TIMEOUT':'CAMERA_UNAVAILABLE',confirmed_fall:completed?action==='CONFIRMED':null,camera_confidence:null,summary:first?'시연: 1차 낙상 의심':completed?(action==='CONFIRMED'?'시연: 카메라 낙상 확인':'시연: 카메라 오탐 취소'):action==='TIMEOUT'?'시연: 카메라 시간 초과':'시연: 카메라 검증 불가'};
    }
    const result=await api('/mock/fall-assessments',lastPayload);await render(result);
  }catch(error){$('assessmentNotice').classList.add('error');$('assessmentNotice').textContent=error.message;}
  finally{busy=false;controls();}
}
document.querySelectorAll('[data-assessment]').forEach(button=>button.onclick=()=>submit(button.dataset.assessment));
$('assessmentDetail').onclick=async()=>{await showDetail('incident',workflow.incident_id,workflow.name);location.hash='detail';};
controls();
