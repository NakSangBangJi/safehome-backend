const $=id=>document.getElementById(id);
const key='safehome-password-session';
let session=null;
try{session=JSON.parse(sessionStorage.getItem(key)||'null');}catch{sessionStorage.removeItem(key);}
const roles={GUARDIAN:'보호자',SENIOR:'어르신 본인',ADMIN:'관리자'};
function clear(){session=null;sessionStorage.removeItem(key);$('identity').textContent='아직 로그인하지 않았습니다.';$('expiry').textContent='';$('result').textContent='로그인 정보가 없습니다.';}
function notice(text,error=false){$('notice').textContent=text;$('notice').classList.toggle('error',error);}
async function request(path,method='GET',body){
  const response=await fetch('/api/v1'+path,{method,cache:'no-store',headers:{...(session?{Authorization:'Bearer '+session.access_token}:{}),...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});
  const data=response.status===204?null:await response.json();
  if(!response.ok){if(response.status===401&&path!=='/auth/login')clear();throw new Error(data.message||'요청에 실패했습니다.');}
  return data;
}
async function profile(){const data=await request('/auth/me');$('identity').textContent=data.user_name+' · '+roles[data.role];$('expiry').textContent=session?'로그인 만료: '+new Date(session.expires_at).toLocaleString('ko-KR'):'';return data;}
async function run(action){document.querySelectorAll('button').forEach(b=>b.disabled=true);try{await action();}catch(error){notice(error.message||'서버에 연결할 수 없습니다. 서버가 켜져 있는지 확인하세요.',true);}finally{document.querySelectorAll('button').forEach(b=>b.disabled=false);}}
$('loginForm').addEventListener('submit',e=>{e.preventDefault();run(async()=>{
  // Replacing a login revokes the previous session in this tab first.
  if(session){try{await request('/auth/logout','POST');}catch{}clear();}
  const password=$('password').value;
  try{session=await request('/auth/login','POST',{account_id:$('account').value.trim(),password});}finally{$('password').value='';}
  sessionStorage.setItem(key,JSON.stringify(session));await profile();notice('로그인 성공! 아래의 “내 어르신 목록 보기”를 눌러보세요.');$('result').textContent='로그인 성공 (로그인 열쇠는 화면에 표시하지 않습니다.)';
});});
$('me').onclick=()=>run(async()=>{const data=await profile();$('result').textContent=JSON.stringify(data,null,2);notice('현재 로그인된 계정을 확인했습니다.');});
$('seniors').onclick=()=>run(async()=>{const data=await request('/seniors');$('result').textContent=JSON.stringify(data,null,2);notice('조회 가능한 어르신 '+data.items.length+'명\n'+data.items.map(x=>x.name).join('\n'));});
$('logout').onclick=()=>run(async()=>{if(!session){notice('이미 로그아웃되어 있습니다.');return;}await request('/auth/logout','POST');clear();notice('로그아웃했습니다. “내 로그인 확인”을 누르면 다시 로그인이 필요하다는 안내가 나옵니다.');});
if(session)run(async()=>{await profile();notice('이 탭의 로그인을 유지하고 있습니다.');});
