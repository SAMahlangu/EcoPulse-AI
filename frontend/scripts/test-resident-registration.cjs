const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),ts=require('typescript');
let state=[],cursor=0,ready=true;const calls=[],destinations=[];
const react={createElement:(type,props,...children)=>({type,props:props||{},children:children.flat(Infinity)}),useState:initial=>{const i=cursor++;if(!(i in state))state[i]=initial;return[state[i],value=>{state[i]=value;}];}};
const auth={user:null,configured:true,register:async(...args)=>{calls.push(['register',...args]);return ready;},login:async(...args)=>{calls.push(['login',...args]);return{role:'resident'};}};
const mod={exports:{}};
const source=ts.transpileModule(fs.readFileSync('src/pages/PublicPages.tsx','utf8').replaceAll('import.meta.env','({})'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText;
vm.runInNewContext(source,{React:react,exports:mod.exports,FormData:class{get(name){return{full_name:'Test Resident',email:'resident@example.test',password:'Test-only-password-987!'}[name];}},require:name=>{
 if(name==='react')return react;
 if(name==='react-router-dom')return{useNavigate:()=>path=>destinations.push(path)};
 if(name==='../services/auth')return{useAuth:()=>auth};return{};
}});
function find(node,type){if(node?.type===type)return node;for(const child of node?.children||[]){const result=find(child,type);if(result)return result;}}
(async()=>{
 let tree=mod.exports.AuthPage({mode:'register'});await find(tree,'form').props.onSubmit({preventDefault:()=>{},currentTarget:{}});
 assert.equal(calls[0][0],'register');assert.equal(calls[1][0],'login');assert.equal(destinations[0],'/resident');
 assert.ok(!state.some(value=>typeof value==='string'&&value.includes('Check your inbox')));
 ready=false;state=[];cursor=0;calls.length=0;destinations.length=0;
 tree=mod.exports.AuthPage({mode:'register'});await find(tree,'form').props.onSubmit({preventDefault:()=>{},currentTarget:{}});
 assert.equal(calls.length,1);assert.equal(destinations.length,0);assert.ok(state.some(value=>typeof value==='string'&&value.includes('Check your inbox')));
 console.log('Resident registration checks passed: immediate sign-in for confirmed accounts; email confirmation messaging preserved for deployed registration.');
})().catch(error=>{console.error(error);process.exitCode=1;});
