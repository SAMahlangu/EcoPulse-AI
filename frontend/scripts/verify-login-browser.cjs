// Real browser verification via a separate, localhost-only Edge CDP session.
// Credentials arrive on stdin, never in command arguments or printed output.
const assert=require('node:assert/strict');
const input=[];
process.stdin.on('data',chunk=>input.push(chunk));
process.stdin.on('end',async()=>{
 let socket;
 try{
  const credentials=JSON.parse(Buffer.concat(input).toString('utf8'));
  const targets=await (await fetch('http://127.0.0.1:9229/json/list')).json();
  const target=targets.find(t=>t.type==='page');assert.ok(target,'Diagnostic browser is not running');
  socket=new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
  let sequence=0;const pending=new Map();const requests=[];
  socket.addEventListener('message',event=>{
   const message=JSON.parse(event.data);
   if(message.id){const item=pending.get(message.id);if(item){pending.delete(message.id);message.error?item.reject(new Error(message.error.message)):item.resolve(message.result);}}
   if(message.method==='Network.requestWillBeSent')requests.push(message.params.request.url);
  });
  const send=(method,params={})=>new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const response=await send('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(response.exceptionDetails)throw new Error('Browser script failed');return response.result.value;};
  const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  await send('Network.enable');await send('Page.enable');
  await evaluate("localStorage.removeItem('ecopulse.auth.session')");
  await send('Page.navigate',{url:'http://localhost:5173/login'});
  let ready=false;
  for(let i=0;i<50;i++){ready=await evaluate("Boolean(document.querySelector('form.auth-form input[name=email]'))");if(ready)break;await delay(200);}
  assert.ok(ready,'Login form did not render');
  await evaluate(`(()=>{document.querySelector('input[name=email]').value=${JSON.stringify(credentials.email)};document.querySelector('input[name=password]').value=${JSON.stringify(credentials.password)};document.querySelector('form.auth-form').requestSubmit();})()`);
  let state;
  for(let i=0;i<225;i++){
   state=await evaluate("({path:location.pathname,error:document.querySelector('.auth-message')?.textContent??''})");
   if(state.path.startsWith('/resident'))break;
   if(state.error)throw new Error(state.error);
   await delay(200);
  }
  assert.ok(state.path.startsWith('/resident'),'Login did not reach the resident portal');
  assert.ok(requests.some(url=>url.includes('/auth/gateway/token')),'Browser did not use the local auth gateway');
  assert.ok(!requests.some(url=>url.includes('.supabase.co/auth/v1/')),'Browser still depends on direct Supabase DNS');
  console.log('PASS: browser form submitted, local auth gateway succeeded and resident portal opened');
  await send('Page.navigate',{url:'http://localhost:5173/resident/report-incident'});
  let report=false;
  for(let i=0;i<70;i++){report=await evaluate("Boolean(document.querySelector('.incident-wizard')&&document.querySelector('.leaflet-container'))");if(report)break;await delay(200);}
  assert.ok(report,'Resident infrastructure reporting form/map did not render');
  console.log('PASS: authenticated infrastructure reporting form and interactive map rendered');
 }catch(error){console.error('Browser verification failed:',error.message);process.exitCode=1;}
 finally{socket?.close();}
});
