// Run the real UI script with minimal DOM/API boundaries; no browser dependency.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const nodes = new Map();
function node() {
  return {value:'', classList:{toggle(){}}, setAttribute(){}, append(){}, replaceChildren(){}};
}
let scheduled = 0;
const context = vm.createContext({
  console, encodeURIComponent,
  document:{querySelector(selector){
    if(!nodes.has(selector))nodes.set(selector,node());
    return nodes.get(selector);
  }, querySelectorAll(){return [];}, createElement:node},
  MutationObserver:class{observe(){}}, ResizeObserver:class{observe(){}},
  requestAnimationFrame(){return 1;}, setInterval(){},
  setTimeout(){return ++scheduled;}, clearTimeout(){}
});
const source = fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
vm.runInContext(source.replace(/^init\(\)\.catch.*$/m,''),context);
vm.runInContext(`
  run={id:'test',status:'running'};
  let preferred='ProbeFlowVars.mo';
  const requests=[];
  api=async url=>{
    const file=decodeURIComponent(url.split('file=')[1].split('&')[0]);
    requests.push(file);
    return {file:file||preferred,files:['ProbeFlowVars.mo','WorktableHydraulics.mo'],status:'failed',message:'Test response'};
  };
`,context);
(async()=>{
  await vm.runInContext('refreshModel()',context);
  assert.equal(nodes.get('#model-file').value,'ProbeFlowVars.mo');
  vm.runInContext("preferred='WorktableHydraulics.mo'",context);
  await vm.runInContext('refreshModel()',context);
  assert.equal(nodes.get('#model-file').value,'WorktableHydraulics.mo', 'Automatic selection must follow the final model');
  assert.equal(vm.runInContext('requests.at(-1)',context),'');

  // Only an explicit dropdown choice pins the model across subsequent refreshes.
  nodes.get('#model-file').value='ProbeFlowVars.mo';
  nodes.get('#model-file').onchange();
  await vm.runInContext('refreshModel()',context);
  assert.equal(nodes.get('#model-file').value,'ProbeFlowVars.mo');
  assert.equal(vm.runInContext('requests.at(-1)',context),'ProbeFlowVars.mo');

  vm.runInContext("renderState({artifacts:[{name:'WorktableHydraulics.mo',size:1}],memory:{}})",context);
  const before=scheduled;
  vm.runInContext("renderState({artifacts:[{name:'WorktableHydraulics.mo',size:1}],memory:{files:{mo_file:'WorktableHydraulics.mo'}}})",context);
  assert.equal(scheduled,before+1,'A preferred-model update alone must refresh the diagram');
  console.log('Model selection checks passed: automatic follow, manual selection, memory-only updates.');
})().catch(error=>{console.error(error);process.exitCode=1;});
