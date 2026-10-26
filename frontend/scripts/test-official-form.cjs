const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const ts=require('typescript');
let states=[],cursor=0,pending=[];
const posted=[],invalidated=[];
const municipalityItems=Array.from({length:25},(_,i)=>({id:`municipality-${i+1}`,name:`Municipality ${i+1}`,province:'Test province',code:`M${i+1}`}));
const react={
 createElement:(type,props,...children)=>({type,props:props||{},children:children.flat(Infinity)}),
 useState:initial=>{const i=cursor++;if(!(i in states))states[i]=initial;return [states[i],value=>{states[i]=value;}];},
 useMemo:fn=>fn(),lazy:()=>()=>null,
};
const query={
 useQuery:()=>({data:{items:municipalityItems},isSuccess:true}),
 useQueryClient:()=>({invalidateQueries:options=>invalidated.push(options)}),
 useMutation:options=>({isPending:false,mutate:()=>{const promise=Promise.resolve(options.mutationFn()).then(()=>options.onSuccess?.());pending.push(promise);},reset:()=>{}}),
};
const mod={exports:{}};
const source=ts.transpileModule(fs.readFileSync('src/pages/PortalDashboards.tsx','utf8')+'\nexport { Municipalities };',{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText;
vm.runInNewContext(source,{React:react,exports:mod.exports,require:name=>{
 if(name==='react')return react;
 if(name==='@tanstack/react-query')return query;
 if(name==='../services/auth')return {useAuth:()=>({user:{id:'fixture-admin',role:'admin'}})};
 if(name==='../services/api')return {postJson:async(path,body)=>{posted.push({path,body});return {id:'new-official'};}};
 if(name.includes('GeographySelects'))return {MunicipalitySelect:'MunicipalitySelect',ProvinceSelect:'ProvinceSelect'};
 return {};
}});
const render=()=>{cursor=0;return mod.exports.Municipalities();};
function find(node,predicate){if(node&&typeof node==='object'&&predicate(node))return node;for(const child of node?.children||[]){const result=find(child,predicate);if(result)return result;}}
function fill(label,value){const tree=render();const field=find(tree,x=>x.type==='label'&&x.children[0]===label);find(field,x=>x.type==='input').props.onChange({target:{value}});}
(async()=>{
 let initial=render();
 let table=find(initial,x=>x.type==='table');
 assert.equal(find(table,x=>x.type==='tbody').children.length,10);
 find(initial,x=>x.type==='button'&&x.children[0]==='Next').props.onClick();
 initial=render();table=find(initial,x=>x.type==='table');
 assert.equal(find(table,x=>x.type==='tbody').children.length,10);
 assert.equal(find(find(table,x=>x.type==='tbody').children[0],x=>x.type==='b').children[0],'Municipality 11');
 find(initial,x=>x.type==='input'&&x.props['aria-label']==='Search municipality table').props.onChange({target:{value:'Municipality 25'}});
 initial=render();table=find(initial,x=>x.type==='table');
 assert.equal(find(table,x=>x.type==='tbody').children.length,1);
 find(initial,x=>x.type==='input'&&x.props['aria-label']==='Search municipality table').props.onChange({target:{value:''}});
 fill('Full name','Test Official');fill('Email address','official@example.test');fill('Password','Test-only-pass-987!');fill('Confirm password','different');
 let tree=render();find(tree,x=>x.type==='ProvinceSelect').props.onChange('province-uuid');
 tree=render();find(tree,x=>x.type==='MunicipalitySelect').props.onChange('municipality-uuid');
 tree=render();assert.equal(find(tree,x=>x.type==='button').props.disabled,true);
 fill('Confirm password','Test-only-pass-987!');tree=render();assert.equal(find(tree,x=>x.type==='button').props.disabled,false);
 find(tree,x=>x.type==='form').props.onSubmit({preventDefault:()=>{}});await Promise.all(pending);
 assert.equal(posted.length,1);assert.equal(posted[0].path,'/admin/municipal-officials');
 assert.equal(posted[0].body.municipality_id,'municipality-uuid');assert.equal(posted[0].body.province_id,'province-uuid');
 assert.equal(posted[0].body.password,'Test-only-pass-987!');
 tree=render();const passwordField=find(tree,x=>x.type==='label'&&x.children[0]==='Password');assert.equal(find(passwordField,x=>x.type==='input').props.value,'');
 assert.ok(invalidated.some(x=>x.queryKey.includes('/admin/municipal-memberships')));
 console.log('Official form checks passed: 10-row pagination, table search, password confirmation, UUID assignment, direct account endpoint, password clearing and membership refresh.');
})().catch(error=>{console.error(error);process.exitCode=1;});
