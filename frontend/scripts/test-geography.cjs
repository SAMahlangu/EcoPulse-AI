const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const ts = require('typescript');
let state = [], cursor = 0, effects = [], requests = [];
const items = [
  {code:'ONE',name:'First reference',province_id:'province-one',id:'canonical-uuid',category:'A'},
  {code:'OLD',name:'Second municipality',province_id:'province-one',id:'second-uuid',category:'B'},
];
const react = {
  createElement:(type,props,...children)=>({type,props:props || {},children:children.flat(Infinity)}),
  useState:initial=>{const index=cursor++; if (!(index in state)) state[index]=initial; return [state[index],value=>{state[index]=value;}];},
  useMemo:fn=>fn(), useEffect:fn=>effects.push(fn),
};
const moduleObject={exports:{}};
const source=ts.transpileModule(fs.readFileSync('src/components/geography/GeographySelects.tsx','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText;
vm.runInNewContext(source,{React:react,exports:moduleObject.exports,require:name=>{
  if(name==='react') return react;
  if(name==='@tanstack/react-query') return {useQuery:options=>{requests.push(options);return {data:{items,total:items.length},isSuccess:true};}};
  return {getJson:async()=>({items,total:items.length})};
}});
let selected='canonical-uuid';
function render(provinceId='province-one') {cursor=0;effects=[];return moduleObject.exports.MunicipalitySelect({provinceId,value:selected,onChange:value=>{selected=value;}});}
function find(node,type){if(node?.type===type)return node;for(const child of node?.children||[]){const found=find(child,type);if(found)return found;}}
let tree=render();
assert.equal(find(tree,'select').props.value,'canonical-uuid');
assert.equal(find(tree,'select').children[1].props.value,'canonical-uuid');
assert.equal(find(tree,'select').children[2].props.value,'second-uuid');
assert.equal(find(tree,'select').children[2].children.join(''),'Second municipality');
find(tree,'select').props.onChange({target:{value:'second-uuid'}});assert.equal(selected,'second-uuid');
find(tree,'input').props.onChange({target:{value:'Second'}});
tree=render();assert.equal(find(tree,'select').children.length,2);
selected='canonical-uuid';tree=render('province-two');effects.forEach(fn=>fn());assert.equal(selected,'');
tree=render('province-two');assert.equal(find(tree,'select').props.value,'');
assert.equal(requests.at(-1).queryKey[2],'province-two');
assert.equal(requests.at(-1).staleTime,0);
assert.equal(requests.at(-1).refetchOnMount,'always');
assert.equal(requests.at(-1).queryKey[3],'canonical-all');
console.log('Dropdown checks passed: UUID values, name-only labels, search, province reset and query scoping.');
