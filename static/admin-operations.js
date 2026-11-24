import {api,load,date,ensureAdmin} from './admin-test.js?v=1.5.0';
const $=id=>document.getElementById(id);
const riskNames={NORMAL:'정상',CAUTION:'주의',DANGER:'위험'};
const eventNames={STATUS_CHANGED:'상태 변경',INCIDENT_ALERT:'사고 경고',DEVICE_STATUS_CHANGED:'장치 상태 변경',PATTERN_UPDATED:'생활 패턴 변화'};
const savedKey='safehome-admin-cursor-v1';
let cursor=0,socket=null,retryTimer=null,pingTimer=null,refreshTimer=null,liveWanted=true,retryDelay=1000,recovering=false,connectionGeneration=0;
let incidentPage=1,incidentPages=0,incidentBusy=false,pendingIncidentPage=null,scenarioBusy=false,eventCount=0;
function say(id,text,error=false){$(id).textContent=text;$(id).classList.toggle('error',error);}
function remember(value){if(Number.isSafeInteger(value)&&value>=cursor){cursor=value;sessionStorage.setItem(savedKey,String(cursor));}}
function incidentControls(){ $('incidentPrev').disabled=incidentBusy||incidentPage<=1;$('incidentNext').disabled=incidentBusy||incidentPage>=incidentPages;}
async function loadIncidents(page=incidentPage){
  if(incidentBusy){pendingIncidentPage=page;return;}incidentBusy=true;incidentControls();
  try{
    const params=new URLSearchParams({page:String(page),size:'10'});
    if($('incidentSenior').value)params.set('senior_id',$('incidentSenior').value);
    for(const [id,param] of [['startAt','start_at'],['endAt','end_at']])if($(id).value)params.set(param,new Date($(id).value+':00+09:00').toISOString());
    const data=await api('/admin/incidents?'+params);incidentPage=data.page;incidentPages=data.total_pages;
    $('incidentRows').replaceChildren();
    for(const item of data.items){
      const row=document.createElement('tr');
      for(const text of [item.senior_name,date(item.event_time),riskNames[item.risk_level],item.status==='CANCELLED'?'취소 기록':'발생 기록',item.summary]){const cell=document.createElement('td');cell.textContent=text;row.append(cell);}
      const cell=document.createElement('td'),button=document.createElement('button');button.textContent='상세 보기';button.onclick=()=>showDetail('incident',item.incident_id,item.senior_name);cell.append(button);row.append(cell);$('incidentRows').append(row);
    }
    if(!data.items.length){const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=6;cell.textContent='조회 조건에 해당하는 사고가 없습니다.';row.append(cell);$('incidentRows').append(row);}
    say('incidentNotice',`전체 ${data.total}건 · 현재 ${data.items.length}건 표시`);
    $('incidentPage').textContent=data.total_pages?`${data.page} / ${data.total_pages} 페이지`:'0 / 0 페이지';
  }catch(error){$('incidentRows').replaceChildren();incidentPages=0;say('incidentNotice',error.message,true);}
  finally{incidentBusy=false;incidentControls();if(pendingIncidentPage!==null){const pending=pendingIncidentPage;pendingIncidentPage=null;void loadIncidents(pending);}}
}
async function showDetail(kind,id,name){
  $('detail').hidden=false;$('detailTitle').textContent=(kind==='incident'?'사고 상세':'패턴 상세')+' · '+name;$('detailInfo').replaceChildren();$('detailJson').textContent='불러오는 중입니다.';
  try{
    const data=await api((kind==='incident'?'/incidents/':'/patterns/')+encodeURIComponent(id));
    const entries=kind==='incident'?[['발생 시각',date(data.event_time)],['위험 단계',riskNames[data.risk_level]],['기록 상태',data.status==='CANCELLED'?'취소 기록':'발생 기록'],['사건 설명',data.summary],['취소 제스처',data.evidence.gesture_cancelled?'감지됨':'감지 안 됨'],...(data.assessment?[['카메라 재검증 상태',data.assessment.analysis_status],['카메라 낙상 확인',data.assessment.confirmed_fall===null?'판단 보류':data.assessment.confirmed_fall?'낙상 확인':'오탐 취소']]:[])]:[['변화 종류',data.pattern_type],['관찰 시작',date(data.observation_start_time)],['관찰 종료',date(data.observation_end_time)],['관찰 정보',data.summary_note]];
    for(const [label,value] of entries){const p=document.createElement('p');p.textContent=label+': '+value;$('detailInfo').append(p);}
    $('detailJson').textContent=JSON.stringify(data,null,2);
  }catch(error){$('detailJson').textContent=error.message;}
}
function scheduleRefresh(){if(!refreshTimer)refreshTimer=setTimeout(()=>{refreshTimer=null;void load();void loadIncidents();},250);}
function eventReceived(message){
  if(message.cursor<=cursor)return;
  if(!eventCount)$('liveEvents').replaceChildren();
  const entry=document.createElement('div');entry.className='event';
  const label=message.data.stage==='FINAL'?(message.data.analysis_status==='COMPLETED'?(message.data.confirmed_fall?'재검증: 낙상 확정':'재검증: 오탐 취소'):'재검증: 판단 보류'):eventNames[message.event_type]||message.event_type;
  const summary=document.createElement('span');summary.textContent=`${date(message.occurred_at)} · ${message.senior_name} · ${label}`+(message.replayed?' · 다시 받은 기록':'');entry.append(summary);
  if(message.data.incident_id||message.data.pattern_id){const button=document.createElement('button');button.textContent='기록 상세';button.onclick=()=>showDetail(message.data.incident_id?'incident':'pattern',message.data.incident_id||message.data.pattern_id,message.senior_name);entry.append(button);}
  $('liveEvents').prepend(entry);eventCount++;
  while($('liveEvents').children.length>50)$('liveEvents').lastElementChild.remove();
  remember(message.cursor);scheduleRefresh();
}
function disconnect(){connectionGeneration++;clearTimeout(retryTimer);retryTimer=null;clearInterval(pingTimer);pingTimer=null;const old=socket;socket=null;if(old)old.close();}
async function recover(){
  if(recovering)return;recovering=true;disconnect();sessionStorage.removeItem(savedKey);
  say('liveNotice','기록 기준점이 바뀌어 전체 현황과 사고 목록을 다시 조회합니다.');
  try{const fresh=await api('/admin/sync?limit=1');cursor=fresh.latest_cursor;sessionStorage.setItem(savedKey,String(cursor));await Promise.all([load(),loadIncidents(1)]);if(liveWanted)connect();}
  catch(error){say('liveNotice',error.message,true);retry();}
  finally{recovering=false;}
}
function retry(){if(!liveWanted||retryTimer)return;say('liveStatus',`연결 끊김 · ${Math.ceil(retryDelay/1000)}초 후 다시 연결`);retryTimer=setTimeout(()=>{retryTimer=null;connect();},retryDelay);retryDelay=Math.min(retryDelay*2,15000);}
async function connect(){
  if(!liveWanted)return;disconnect();const generation=connectionGeneration;say('liveStatus','실시간 연결 중');
  try{
    const token=await ensureAdmin();if(!liveWanted||generation!==connectionGeneration)return;
    const ws=new WebSocket(location.origin.replace(/^http/,'ws')+'/ws/admin/live');socket=ws;
    ws.onopen=()=>{if(socket===ws)ws.send(JSON.stringify({action:'SUBSCRIBE',access_token:token,after_cursor:cursor}));};
    ws.onmessage=event=>{
      if(socket!==ws)return;
      try{
        const message=JSON.parse(event.data);
        if(message.type==='SUBSCRIBED'){
          retryDelay=1000;say('liveStatus','실시간 연결됨');say('liveNotice','전체 어르신의 상태·사고·장치·패턴 변화를 받고 있습니다.');
          clearInterval(pingTimer);pingTimer=setInterval(()=>{if(ws.readyState===WebSocket.OPEN)ws.send(JSON.stringify({action:'PING'}));},20000);scheduleRefresh();
        }else if(message.type==='EVENT')eventReceived(message);
        else if(message.type==='CURSOR')remember(message.cursor);
        else if(message.type==='ERROR'){
          if(['CURSOR_AHEAD','CURSOR_EXPIRED'].includes(message.error_code)){void recover();return;}
          liveWanted=false;disconnect();say('liveStatus','실시간 연결 중단');say('liveNotice',message.message,true);$('toggleLive').textContent='실시간 다시 연결';
        }
      }catch{say('liveNotice','실시간 기록을 읽지 못했습니다. 다시 연결합니다.',true);ws.close();}
    };
    ws.onclose=()=>{if(socket!==ws)return;socket=null;clearInterval(pingTimer);retry();};
    ws.onerror=()=>{if(socket===ws)say('liveNotice','서버 연결을 확인하고 있습니다. 연결이 돌아오면 자동으로 다시 시도합니다.',true);};
  }catch(error){say('liveNotice',error.message,true);retry();}
}
async function bootstrap(){
  try{
    await ensureAdmin();
    const targets=(await api('/seniors')).items;
    for(const item of targets)for(const id of ['scenarioSenior','incidentSenior']){const option=document.createElement('option');option.value=item.senior_id;option.textContent=item.name;$(id).append(option);}
    $('scenarioSenior').disabled=!targets.length;
    const stored=sessionStorage.getItem(savedKey);const saved=stored===null?null:Number(stored);
    if(saved!==null&&Number.isSafeInteger(saved)&&saved>=0){
      try{await api('/admin/sync?after_cursor='+saved+'&limit=1');cursor=saved;}
      catch(error){if(error.status!==409)throw error;cursor=(await api('/admin/sync?limit=1')).latest_cursor;sessionStorage.setItem(savedKey,String(cursor));}
    }else cursor=(await api('/admin/sync?limit=1')).latest_cursor;
    sessionStorage.setItem(savedKey,String(cursor));
    await Promise.all([load(),loadIncidents(1)]);$('toggleLive').disabled=false;
    say('scenarioNotice','준비됐습니다. 위험 시연을 누르면 사고 기록과 실시간 경고가 생깁니다.');
    connect();
  }catch(error){say('notice',error.message,true);say('incidentNotice','서버 연결을 확인하세요.',true);say('liveStatus','연결 준비 실패');say('scenarioNotice','서버를 다시 켜고 화면을 새로고침하세요.',true);}
}
document.querySelectorAll('[data-scenario]').forEach(button=>button.onclick=async()=>{
  if(scenarioBusy||!$('scenarioSenior').value)return;scenarioBusy=true;document.querySelectorAll('[data-scenario]').forEach(b=>b.disabled=true);
  try{await api('/mock/scenarios',{senior_id:$('scenarioSenior').value,scenario:button.dataset.scenario});say('scenarioNotice',button.textContent+'을 실행했습니다.'+(liveWanted?' 실시간 기록과 목록을 확인하세요.':' 실시간 다시 연결을 누르면 끊긴 동안의 기록을 받습니다.'));}
  catch(error){say('scenarioNotice',error.message,true);}
  finally{scenarioBusy=false;document.querySelectorAll('[data-scenario]').forEach(b=>b.disabled=false);}
});
$('incidentFilter').onsubmit=e=>{e.preventDefault();void loadIncidents(1);};$('incidentPrev').onclick=()=>loadIncidents(incidentPage-1);$('incidentNext').onclick=()=>loadIncidents(incidentPage+1);
$('toggleLive').onclick=()=>{liveWanted=!liveWanted;if(liveWanted){$('toggleLive').textContent='실시간 잠시 끊기';void connect();}else{disconnect();$('toggleLive').textContent='실시간 다시 연결';say('liveStatus','실시간 연결 일시 중지');say('liveNotice','시연을 실행한 뒤 다시 연결하면 끊긴 동안의 기록을 받습니다.');}};
window.addEventListener('pagehide',()=>{liveWanted=false;disconnect();clearTimeout(refreshTimer);});
bootstrap();
export {showDetail};
