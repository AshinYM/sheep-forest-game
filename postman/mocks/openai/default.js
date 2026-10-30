// Contract-derived response envelope, authored failure fixtures. Not a real LLM.
const http=require('http');
const server=http.createServer((req,res)=>{
  const send=(status,body)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(body));};
  if(req.method!=='POST'||req.url!=='/v1/responses')return send(501,{error:{message:'Endpoint not modeled'}});
  const body=req.body;
  if(!body||!body.model||!Array.isArray(body.input)||body.store!==false||body.max_output_tokens!==256||body.text?.format?.strict!==true||body.text?.format?.schema?.additionalProperties!==false)
    return send(400,{error:{message:'Forest request subset violated'}});
  const contentInput=body.input.find(x=>x.role==='user')?.content||'';
  const text=typeof contentInput==='string'?contentInput:contentInput.filter(x=>x.type==='input_text'||x.type==='text').map(x=>x.text).join('');
  if(text.includes('SIM_429'))return send(429,{error:{message:'Simulated rate limit'}});
  if(text.includes('SIM_500'))return send(500,{error:{message:'Simulated provider failure'}});
  if(text.includes('SIM_HTML')){res.writeHead(500,{'Content-Type':'text/html'});return res.end('<html>simulated proxy failure</html>');}
  if(text.includes('SIM_BAD_JSON')){res.writeHead(200,{'Content-Type':'application/json'});return res.end('{not valid json');}
  let interpretation={kind:'action',action:'stare',target:'luminous',item:null};
  if(text.includes('SIM_SAFE'))interpretation={kind:'action',action:'look_away',target:'luminous',item:null};
  if(text.includes('SIM_DIALOGUE'))interpretation={kind:'dialogue',action:'unknown',target:null,item:null};
  if(text.includes('SIM_AMBIGUOUS'))interpretation={kind:'ambiguous',action:'unknown',target:null,item:null};
  if(text.includes('SIM_TAMPER'))interpretation.violations=0;
  if(text.includes('SIM_BAD_TARGET'))interpretation.target='control_station';
  if(text.includes('SIM_UNKNOWN_ACTION'))interpretation.action='set_lives';
  const content=text.includes('SIM_REFUSAL')?[{type:'refusal',refusal:'simulated refusal'}]:[{type:'output_text',text:JSON.stringify(interpretation),annotations:[]}];
  const result={id:'resp_simulation',object:'response',model:'simulation-model',created_at:0,error:null,incomplete_details:null,usage:{input_tokens:10,output_tokens:20,total_tokens:30,input_tokens_details:{cached_tokens:0},output_tokens_details:{reasoning_tokens:0}},status:text.includes('SIM_INCOMPLETE')?'incomplete':'completed',output:[{id:'msg_simulation',type:'message',role:'assistant',status:'completed',content}]};
  if(text.includes('SIM_TIMEOUT'))return setTimeout(()=>send(200,result),350);
  return send(200,result);
});
server.listen(process.env.PORT||4567);
