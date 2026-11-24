const $=id=>document.getElementById(id);
const riskNames={NORMAL:'정상',CAUTION:'주의',DANGER:'위험'};
const koreanTime=new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',dateStyle:'medium',timeStyle:'medium'});
let token='',currentPage=1,totalPages=0,busy=false;
function date(value){const parsed=new Date(value);return Number.isNaN(parsed.valueOf())?'시각 확인 필요':koreanTime.format(parsed);}
function controls(){for(const id of ['refresh','size'])$(id).disabled=busy;$('prev').disabled=busy||currentPage<=1;$('next').disabled=busy||currentPage>=totalPages;}
async function api(path,body){
  const response=await fetch('/api/v1'+path,{cache:'no-store',...(body?{method:'POST',body:JSON.stringify(body)}:{}),headers:{...(body?{'Content-Type':'application/json'}:{}),...(token?{Authorization:'Bearer '+token}:{})}});
  const result=await response.json();
  if(!response.ok){if(response.status===401)token='';const error=new Error(result.message||'전체 현황을 불러오지 못했습니다.');error.code=result.error_code;error.status=response.status;throw error;}
  return result;
}
function draw(items){
  $('rows').replaceChildren();
  if(!items.length){const row=document.createElement('tr'),cell=document.createElement('td');cell.colSpan=4;cell.textContent='이 페이지에 표시할 어르신이 없습니다.';row.append(cell);$('rows').append(row);return;}
  for(const item of items){
    const row=document.createElement('tr');
    const name=document.createElement('td');name.textContent=item.name;
    const noJudgment=item.status_event_id===null,missingField=item.status_event_id===undefined;
    const risk=document.createElement('td'),badge=document.createElement('span');
    badge.className='badge '+(noJudgment||missingField?'UNASSESSED':item.risk_level);
    badge.textContent=missingField?'판단 기록 확인 필요':noJudgment?'판단 기록 없음':riskNames[item.risk_level]||'확인 필요';risk.append(badge);
    const updated=document.createElement('td');updated.textContent=missingField?'서버 재시작 후 다시 조회하세요.':noJudgment?'—':date(item.last_updated_at);
    const connection=document.createElement('td'),{online,offline}=item.device_summary;
    connection.textContent=online+offline===0?'등록된 장치 없음':`연결 ${online} / 끊김 ${offline}`;
    const detail=document.createElement('small');detail.textContent=item.data_freshness==='STALE'?'연결 상태 확인 필요':'모든 장치 연결됨';connection.append(detail);
    row.append(name,risk,updated,connection);$('rows').append(row);
  }
}
async function load(page=1){
  if(busy)return;busy=true;controls();$('notice').classList.remove('error');$('notice').textContent='전체 현황을 불러오는 중입니다.';
  try{
    if(!token)token=(await api('/auth/mock-login',{account_id:'admin-demo'})).access_token;
    const data=await api(`/admin/seniors?page=${page}&size=${$('size').value}`);
    currentPage=data.page;totalPages=data.total_pages;draw(data.items);
    $('total').textContent=`전체 ${data.total}명 · 현재 ${data.items.length}명 표시`;
    $('page').textContent=totalPages?`${currentPage} / ${totalPages} 페이지`:'0 / 0 페이지';
    $('notice').textContent=data.total===0?'등록된 어르신이 없습니다.':'관리자 권한으로 전체 현황을 조회했습니다.';
    $('loadedAt').textContent='이 화면의 조회 시각: '+date(new Date());
    $('response').textContent=JSON.stringify(data,null,2);
  }catch(error){
    $('rows').replaceChildren();$('total').textContent='조회 실패';$('page').textContent='조회 실패';$('loadedAt').textContent='';$('response').textContent='조회한 응답이 없습니다.';totalPages=0;currentPage=1;
    $('notice').classList.add('error');$('notice').textContent=(error instanceof TypeError?'서버에 연결할 수 없습니다. 서버가 켜져 있는지 확인하세요.':error.message)+' 이 확인 화면은 개발용 자동 로그인이 켜진 서버에서 사용합니다.';
  }finally{busy=false;controls();}
}
$('refresh').onclick=()=>load(currentPage);$('size').onchange=()=>load(1);$('prev').onclick=()=>load(currentPage-1);$('next').onclick=()=>load(currentPage+1);
async function ensureAdmin(){if(!token)token=(await api('/auth/mock-login',{account_id:'admin-demo'})).access_token;return token;}
export {api,load,date,ensureAdmin};
