const $ = id => document.getElementById(id);
let token = '', seniorId = '', socket, generation = 0, reconnectTimer, events = [];
const cursors = new Map();
const newKey = () => globalThis.crypto?.randomUUID?.() || `mock-${Date.now()}-${Math.random().toString(36).slice(2)}`;
let pendingCreate = null;
function notice(text, error = false) { $('notice').textContent = text; $('notice').className = error ? 'error' : ''; }
async function api(path, {method='GET', body, admin=false, key}={}) {
  const response = await fetch('/api/v1' + path, {method, headers: {
    Authorization:'Bearer ' + (admin ? 'admin-demo-token' : token),
    ...(body ? {'Content-Type':'application/json'} : {}), ...(key ? {'Idempotency-Key':key} : {})
  }, ...(body ? {body:JSON.stringify(body)} : {})});
  if (response.status === 204) return;
  const result = await response.json();
  if (!response.ok) { const error = new Error(`${response.status} ${result.error_code}: ${result.message}`); error.status = response.status; throw error; }
  return result;
}
function safely(action) { return async (...args) => { try { await action(...args); } catch (e) { notice(e.message, true); } }; }
function cell(text) { const el=document.createElement('td'); el.textContent=text; return el; }
async function refresh() {
  const selected=seniorId;
  const [status, meds, incidents] = await Promise.all([api(`/seniors/${selected}/status`), api(`/seniors/${selected}/medications`), api(`/seniors/${selected}/incidents?size=5`)]);
  if (selected!==seniorId) return;
  $('risk').textContent=status.risk_level; $('risk').className='badge '+status.risk_level;
  $('health').textContent=`연결 ${status.device_summary.online} / 끊김 ${status.device_summary.offline}`;
  $('updated').textContent='마지막 상태 갱신: '+new Date(status.last_updated_at).toLocaleString('ko-KR');
  $('freshness').textContent=status.data_freshness==='STALE'?'장치 연결을 확인하세요. 마지막 위험 상태를 유지하고 있습니다.':'장치 연결 정상';
  $('statusJson').textContent=JSON.stringify(status,null,2);
  $('meds').replaceChildren();
  for (const med of meds) {
    const row=document.createElement('tr'); row.append(cell(med.medication_name),cell(med.med_time));
    const actions=cell(''); const edit=document.createElement('button'); edit.textContent=med.is_active?'끄기':'켜기'; edit.className='alt';
    edit.onclick=safely(async()=>{await api(`/medications/${med.schedule_id}`,{method:'PATCH',body:{is_active:!med.is_active}});await refresh();});
    const remove=document.createElement('button');remove.textContent='삭제';remove.className='alt';remove.onclick=safely(async()=>{await api(`/medications/${med.schedule_id}`,{method:'DELETE'});await refresh();});
    actions.append(edit,document.createTextNode(' '),remove);row.append(actions);$('meds').append(row);
  }
  if (!meds.length) {const row=document.createElement('tr');row.append(cell('등록된 일정이 없습니다.'));$('meds').append(row);}
  $('incidents').replaceChildren();
  for (const item of incidents.items) {const p=document.createElement('p');const button=document.createElement('button');button.className='alt';button.textContent=`${item.risk_level} · ${item.status} · ${new Date(item.event_time).toLocaleTimeString('ko-KR')}`;button.onclick=safely(async()=>{$('incidentJson').hidden=false;$('incidentJson').textContent=JSON.stringify(await api(`/incidents/${item.incident_id}`),null,2);});p.append(button);$('incidents').append(p);}
  if (!incidents.items.length) $('incidents').textContent='사고 이력이 없습니다.';
}
function connect() {
  generation++; const mine=generation; clearTimeout(reconnectTimer); if(socket)socket.close();
  const selected=seniorId, cursorKey=$('role').value+'/'+selected;
  socket=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/ws/live`);
  $('wsState').textContent='연결 중';
  socket.onopen=()=>{if(mine===generation)socket.send(JSON.stringify({action:'SUBSCRIBE',access_token:token,senior_id:selected,after_cursor:cursors.get(cursorKey)||0}));};
  socket.onmessage=event=>{
    if(mine!==generation)return;const msg=JSON.parse(event.data);
    if(msg.type==='SUBSCRIBED')$('wsState').textContent='실시간 연결됨';
    if(msg.type==='EVENT'){cursors.set(cursorKey,msg.cursor);events.unshift(msg);events=events.slice(0,20);$('events').textContent=events.map(x=>JSON.stringify(x,null,2)).join('\n');safely(refresh)();}
    if(msg.type==='CURSOR')cursors.set(cursorKey,msg.cursor);
    if(msg.type==='ERROR'){notice(msg.message,true);if(msg.error_code==='CURSOR_AHEAD')cursors.set(cursorKey,0);}
  };
  socket.onclose=event=>{if(mine!==generation)return;$('wsState').textContent='연결 끊김';if(![4401,4403].includes(event.code))reconnectTimer=setTimeout(connect,1500);};
}
async function changeSenior() {seniorId=$('senior').value;$('seniorId').textContent=seniorId+' · 전화번호: '+($('senior').selectedOptions[0]?.dataset.phone||'미등록');events=[];$('events').textContent='이벤트 대기 중';$('incidentJson').hidden=true;pendingCreate=null;await refresh();connect();}
async function login() {
  generation++;if(socket)socket.close();clearTimeout(reconnectTimer);
  const result=await api('/auth/mock-login',{method:'POST',body:{account_id:$('role').value}});token=result.access_token;
  const self=$('role').value==='senior-demo';document.querySelectorAll('[data-senior-hide]').forEach(el=>el.hidden=self);$('medForm').hidden=false;$('reset').hidden=self;
  const list=await api('/seniors');$('senior').replaceChildren();
  for(const item of list.items){const option=document.createElement('option');option.value=item.senior_id;option.textContent=item.name;option.dataset.phone=item.phone_number||'';$('senior').append(option);}
  await changeSenior();notice('연결되었습니다. 상태 조회와 시연 버튼을 사용할 수 있습니다.');
}
$('role').onchange=safely(login);$('senior').onchange=safely(changeSenior);$('refresh').onclick=safely(refresh);$('reconnect').onclick=connect;
for(const button of document.querySelectorAll('[data-scenario]'))button.onclick=safely(async()=>{$('scenarioJson').textContent=JSON.stringify(await api('/mock/scenarios',{method:'POST',admin:true,body:{senior_id:seniorId,scenario:button.dataset.scenario}}),null,2);await refresh();notice(button.textContent+' 시연을 실행했습니다.');});
$('medTime').value=new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date());
$('medForm').onsubmit=safely(async event=>{event.preventDefault();const body={medication_name:$('medName').value,med_time:$('medTime').value,days_of_week:['MON','TUE','WED','THU','FRI','SAT','SUN'],voice_id:'TYPE_A',activity_window_minutes:30,is_active:true};const fingerprint=seniorId+JSON.stringify(body);if(!pendingCreate||pendingCreate.fingerprint!==fingerprint)pendingCreate={fingerprint,key:newKey()};await api(`/seniors/${seniorId}/medications`,{method:'POST',key:pendingCreate.key,body});pendingCreate=null;await refresh();notice('복약 일정을 등록했습니다.');});
$('registerPush').onclick=safely(async()=>{await api('/notifications/token',{method:'POST',body:{installation_id:$('role').value==='senior-demo'?'00000000-0000-4000-8000-000000000502':$('role').value==='admin-demo'?'00000000-0000-4000-8000-000000000503':'00000000-0000-4000-8000-000000000501',provider:'EXPO',push_token:'ExponentPushToken[m1-'+$('role').value+']',device_os:'ANDROID'}});notice('가상 푸시 토큰을 등록했습니다.');});
$('loadExtras').onclick=safely(async()=>{$('extras').textContent=JSON.stringify({...($('role').value==='senior-demo'?{}:{patterns:await api(`/seniors/${seniorId}/patterns`)}),notifications:await api('/notifications'),medication_logs:await api(`/seniors/${seniorId}/medication-logs`)},null,2);});
$('reset').onclick=safely(async()=>{await api('/mock/reset',{method:'POST',admin:true});cursors.clear();await login();notice('가상 데이터를 초기화했습니다.');});
$('medTick').onclick=safely(async()=>{const result=await api('/mock/medication-tick',{method:'POST',admin:true});notice('새 복약 안내 '+result.generated_count+'건을 기록했습니다. 기록 조회에서 확인하세요.');});
await safely(login)();
