let state, proposal, busy=false, pendingRequest=null;
const $=id=>document.getElementById(id);
function entry(text,kind='story') { const el=document.createElement('div'); el.className='entry '+kind; el.textContent=text; $('history').append(el); $('history').scrollTop=$('history').scrollHeight; }
function render(s) {
  state=s; $('mode').textContent=s.mode==='simulation'?'모의 해석 · 실제 API 호출 없음':s.mode==='openai'?'OpenAI 해석 모드':'로컬 · API 호출 없음';
  $('status').replaceChildren();
  for(const [label,value] of [['위치',s.location_name],['시간',s.time],['진행',s.turn+' 턴']]) { const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;$('status').append(dt,dd); }
  $('strikes').textContent=`규칙 위반 ${s.violations} / 3`;
  document.querySelectorAll('.lives span').forEach((el,i)=>el.classList.toggle('used',i<s.violations));
  $('inventory').replaceChildren();for(const item of s.inventory){const el=document.createElement('li');el.textContent=item;$('inventory').append(el);}
  $('rules').replaceChildren(); const rules=Object.values(s.rules_found);for(const rule of rules.length?rules:['아직 규칙을 찾지 못했다.']){const el=document.createElement('li');el.textContent=rule;$('rules').append(el);}
  $('scene').textContent=s.scene;
  $('ending').hidden=!s.game_over; $('ending-title').textContent=s.ending==='death'?'조사원 사망 · GAME OVER':s.ending==='escaped'?'조사 완료 · 정상 탈출':'날이 밝았다 · 조사 종료';
  $('form').hidden=s.game_over; $('show-choices').hidden=!s.event||s.game_over; $('choices').replaceChildren();
  controls();
}
function controls(){for(const id of ['send','message','reset','restart','confirm','cancel','show-choices'])$(id).disabled=busy; $('message').disabled=busy||Boolean(proposal); $('send').disabled=busy||Boolean(proposal);}
async function api(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,cache:'no-store'});const data=await r.json();return {ok:r.ok,data};}
async function send(payload){
  if(busy)return;busy=true;controls();$('retry').hidden=true;$('notice').textContent='네 말을 듣고 있습니다…';pendingRequest=payload;
  try{
    const {ok,data}=await api('/api/turn',payload);
    if(data.state)render(data.state);
    if(!ok){entry(data.message||'요청을 처리하지 못했습니다.','error'); pendingRequest=null; proposal=null; $('proposal').hidden=true;}
    else{
      pendingRequest=null; proposal=data.proposal||null;$('proposal').hidden=!proposal;
      if(proposal)$('proposal-label').textContent=proposal.label;
      entry((data.replayed?'[이미 처리한 요청] ':'')+data.message,data.status==='violation'||data.status==='game_over'?'violation':data.status==='interpretation_error'?'error':'story');
      if(data.hint)entry('숲이 남긴 힌트\n'+data.hint,'hint');
    }
    $('notice').textContent=state.game_over?'조사가 끝났습니다. 새 조사를 시작하면 처음부터 다시 플레이할 수 있습니다.':'의도를 잘못 이해했다면 실행 전에 다시 설명할 수 있습니다. 이해 오류는 위반이 아닙니다.';
  }catch{
    entry('연결이 끊겼습니다. 아래 버튼으로 같은 요청을 다시 보내면 중복으로 판정하지 않습니다.','error');$('retry').hidden=false;
    $('notice').textContent='이전 요청의 처리 여부를 확인하기 전에는 새 행동을 보내지 않습니다.';
  }finally{busy=false;controls();if(pendingRequest){$('message').disabled=true;$('send').disabled=true;}else if(!proposal&&!state.game_over)$('message').focus();}
}
function payload(operation,extra){return {operation,request_id:crypto.randomUUID(),game_id:state.game_id,expected_revision:state.revision,...extra};}
async function newGame(){
  if(busy)return;busy=true;controls();
  try{const {ok,data}=await api('/api/new',{});if(!ok)throw Error(); proposal=null;pendingRequest=null;$('history').replaceChildren();$('proposal').hidden=true;$('retry').hidden=true;render(data.state);$('notice').textContent='새 조사가 시작됐습니다. 무엇을 할지 한 가지씩 말해 주세요.';entry('22:00. 선발 조사팀의 흔적과 규칙을 확보하고 해가 뜨기 전 탈출하라.\n첫 두 번의 규칙 위반에는 힌트가 남는다. 세 번째에는 돌아올 수 없다.');}
  catch{entry('새 조사를 시작하지 못했습니다. 서버 연결을 확인하세요.','error');}
  finally{busy=false;controls();}
}
$('form').addEventListener('submit',event=>{event.preventDefault();if(!state||busy||pendingRequest)return;const text=$('message').value.trim();if(!text)return;entry(text,'user');$('message').value='';send(payload('input',{text}));});
$('message').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('form').requestSubmit();}});
$('confirm').onclick=()=>{if(proposal&&!busy)send(payload('confirm',{proposal_id:proposal.id}));};
$('cancel').onclick=()=>{if(proposal&&!busy)send(payload('cancel',{proposal_id:proposal.id}));};
$('reset').onclick=newGame;$('restart').onclick=newGame;$('retry').onclick=()=>{if(pendingRequest)send(pendingRequest);};
$('show-choices').onclick=async()=>{if(busy)return;try{const {ok,data}=await api('/api/choices');if(!ok)return;$('choices').replaceChildren();for(const choice of data.choices){const b=document.createElement('button');b.textContent=choice.text+'. '+choice.label;b.onclick=()=>{if(busy||proposal||pendingRequest)return;entry(choice.label,'user');send(payload('input',{text:choice.text}));};$('choices').append(b);}}catch{entry('명령 목록을 읽지 못했습니다.','error');}};
(async()=>{try{const {ok,data}=await api('/api/state');if(ok){render(data.state);entry('이전 조사 상태를 불러왔습니다. 위반 횟수와 시간은 그대로입니다.');}else await newGame();}catch{entry('로컬 게임 서버에 연결하지 못했습니다. 실행 상태를 확인하세요.','error');}})();
