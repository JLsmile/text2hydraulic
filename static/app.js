'use strict';
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const phases = [['req_analysis','Requirements analysis','design'],['circuit_design','Circuit design','design'],['param_calc','Parameter sizing','design'],['modeling','Modelica modeling','simulation'],['simulation_setup','Simulation setup','simulation'],['circuit_check','Circuit review','review'],['simulation','Simulation','simulation'],['output','Results analysis','simulation'],['done','Report & archive','simulation']];
const statuses = {starting:'Starting',running:'Running',completed:'Run finished',failed:'Run failed',stopped:'Stopped',interrupted:'Interrupted',timeout:'Timed out'};
const examples = {
  table:'Design a horizontal hydraulic worktable. Workpiece mass: 500 kg; friction coefficient: 0.15; maximum speed: 0.1 m/s; stroke: 0.5 m; maximum system pressure: 10 MPa. Complete circuit design, sizing, Modelica modeling and OpenModelica simulation. Report actual velocity, stroke coverage and acceptance results.',
  press:'Design a vertical hydraulic lifting system. Load mass: 1000 kg; maximum upward speed: 0.05 m/s; stroke: 0.8 m; maximum system pressure: 16 MPa. State assumptions about gravity and missing parameters. Complete circuit design, sizing, modeling and simulation, and retain the model and results.',
  pusher:'Design a heavy-duty horizontal hydraulic pusher. Workpiece mass: 10000 kg; friction coefficient: 0.2; maximum speed: 0.08 m/s; stroke: 1.2 m; maximum system pressure: 16 MPa. Complete circuit design, sizing and simulation. Report velocity error, stroke coverage and any failed acceptance checks.'
};
let run = null, memory = {}, files = [], source = null, selectedLog = 'all', follow = true;
let entries = new Map(), streamMessages = new Map(), streamBlocks = new Map(), seenPhases = new Set(), eventCount = 0, lastEvent = 0, reportText = '', ready = false;
let chartTimer = null, loadGeneration = 0, toastTimer = null, followFrame = null;
const pretty = (x) => typeof x === 'string' ? x : JSON.stringify(x, null, 2);
const terminal = () => run && !['starting','running'].includes(run.status);
function el(tag, cls, text){const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;}
function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,6000);}
async function api(path, options){const response=await fetch(path,options);const data=await response.json();if(!response.ok)throw new Error(data.error||'Request failed');return data;}
const post = (path,data) => api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
$$('[data-close]').forEach(b=>b.onclick=()=>document.getElementById(b.dataset.close).close());
$('#prompt').oninput=()=>{$('#char-count').textContent=`${$('#prompt').value.length} characters`;updateControls();};
$$('[data-example]').forEach(b=>b.onclick=()=>{$('#prompt').value=examples[b.dataset.example];$('#prompt').oninput();});
function updateControls(){const running=run&&!terminal();$('#start').disabled=Boolean(running)||!ready||!$('#prompt').value.trim();$('#start').firstChild.textContent=running?'Design in progress ':'Start design ';$('#stop').hidden=!running;$('#prompt').disabled=Boolean(running);$$('[data-example]').forEach(b=>b.disabled=Boolean(running));$('#export').disabled=!run;$('#open-session').disabled=!run||Boolean(running);$('#open-session').title=running?'Stop the background run to continue in an interactive terminal.':'Open this conversation in a terminal to authorize operations.';}
async function checkHealth(repair=false){
  const button=$('#recheck');button.disabled=true;button.textContent=repair?'Detecting…':'Checking…';
  try{const data=repair?await post('/api/environment/setup',{}):await api('/api/health');ready=data.ready;$('#permission-mode').value=data.permission_mode||'inherit';updatePermissionHelp();$('#environment').classList.toggle('ready',ready);$('#environment').lastChild.textContent=ready?'Local environment ready':'Environment setup needed';
    const names={claude:'Design runtime',openmodelica:'OpenModelica',openhydraulics:'OpenHydraulics library',skill:'Design workflow',timeout:'Linux timeout'};
    $('#health-details').replaceChildren();Object.entries(data.checks).forEach(([key,value])=>{const row=el('div','health-row');row.append(el('strong','',names[key]),el('span',value?'':'bad',value?'✓ Available':'Missing / invalid path'));$('#health-details').append(row);});
    if(data.renderer){const row=el('div','health-row');row.append(el('strong','','Model diagram renderer'),el('span',data.renderer.available?'':'bad',data.renderer.available?'✓ Available':'Dependencies needed'));$('#health-details').append(row);if(!data.renderer.available)$('#health-details').append(el('p','setup-warning',data.renderer.message));}
    if(data.renderer?.commands){
      const box=el('div','renderer-install');
      box.append(el('p','','Manual installation commands — nothing is installed automatically.'),el('pre','',data.renderer.commands));
      const copy=el('button','','Copy commands');copy.type='button';copy.onclick=async()=>{try{await navigator.clipboard.writeText(data.renderer.commands);toast('Commands copied.');}catch(e){toast('Select and copy the commands manually.');}};
      box.append(copy,el('p','','If venv or Cairo is missing, install your distribution’s python3-venv / Cairo packages first (Ubuntu/Debian: sudo apt install python3-venv libcairo2). Then select Auto-detect.'));
      $('#health-details').append(box);
    }
    if(data.setup?.message)$('#health-details').append(el('p','setup-note',data.setup.message));
    for(const [key,tool] of Object.entries(data.setup?.tools||{})){if(!tool.available)$('#health-details').append(el('p','setup-warning',`${key==='claude'?'Design runtime':'OpenModelica'}: ${tool.message}`));}
    $('#health-details').append(el('p','health-path',`OpenHydraulics: ${data.package}`),el('p','',`Runtime discovery does not verify authentication or model availability. Check local settings if a run cannot start.`));
  }catch(e){ready=false;$('#environment').lastChild.textContent='Server disconnected';toast(e.message);}finally{button.disabled=false;button.textContent='Auto-detect';}updateControls();
}
$('#settings-button').onclick=()=>{$('#settings-dialog').showModal();checkHealth();};$('#recheck').onclick=()=>checkHealth(true);
function renderPhases(){const phase=phases.find(p=>p[0]===memory.meta?.phase);$('#phase-label').textContent=phase?phase[1]:'Ready for a new design';}
function number(value,digits=4){if(value===null||value===undefined||!Number.isFinite(Number(value)))return '—';const n=Number(value);return n!==0&&(Math.abs(n)<.0001||Math.abs(n)>1e7)?n.toExponential(3):n.toLocaleString('en-US',{maximumFractionDigits:digits});}
function factValue(key){return memory.facts?.workpiece?.[key]?.value;}
function fileUrl(name){return `/api/runs/${run.id}/file?name=${encodeURIComponent(name)}`;}
let visualSignature='', modelSelection='', signalSelection='', signalVariable='', modelTicket=0, signalTicket=0;
function renderState(data){memory=data.memory||{};files=data.artifacts||[];renderPhases();renderResults();const signature=JSON.stringify({artifacts:files.filter(f=>/\.(mo|csv)$/.test(f.name)),model:memory.files?.mo_file});if(signature!==visualSignature){visualSignature=signature;clearTimeout(chartTimer);chartTimer=setTimeout(refreshCharts,450);}}
function renderResults(){const done=memory.acceptance?.done_allowed===true, accepted=memory.acceptance?.strict_reproducible_success===true;const badge=$('#acceptance-badge');badge.textContent=done?(accepted?'Checks passed':'Checks need review'):'Acceptance pending';badge.className=`badge ${done?(accepted?'good':'bad'):''}`;}
function inlineText(node,text){
  const parts=text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  for(const part of parts){if(part.startsWith('**')&&part.endsWith('**'))node.append(el('strong','',part.slice(2,-2)));else if(part.startsWith('`')&&part.endsWith('`'))node.append(el('code','',part.slice(1,-1)));else node.append(document.createTextNode(part));}
}
function renderReport(text){
  reportText=text;$('#report-button').disabled=!text;const article=$('#report');article.replaceChildren();const lines=text.split('\n');
  const cells=line=>line.trim().replace(/^\||\|$/g,'').split('|').map(v=>v.trim());
  for(let i=0;i<lines.length;i++){
    const line=lines[i];
    if(line.startsWith('```')){const buffer=[];while(++i<lines.length&&!lines[i].startsWith('```'))buffer.push(lines[i]);article.append(el('pre','',buffer.join('\n')));continue;}
    if(line.includes('|')&&i+1<lines.length&&/^\s*\|?\s*:?-{3,}/.test(lines[i+1])){
      const table=el('table');const head=el('thead');const row=el('tr');cells(line).forEach(value=>{const cell=el('th');inlineText(cell,value);row.append(cell);});head.append(row);table.append(head);i++;
      const body=el('tbody');while(i+1<lines.length&&lines[i+1].includes('|')){const row=el('tr');cells(lines[++i]).forEach(value=>{const cell=el('td');inlineText(cell,value);row.append(cell);});body.append(row);}table.append(body);const wrap=el('div','report-table');wrap.append(table);article.append(wrap);continue;
    }
    const heading=line.match(/^(#{1,6})\s+(.+)$/),bullet=line.match(/^\s*[-*]\s+(.+)$/);
    if(heading){const node=el(heading[1].length<3?'h3':'h4');inlineText(node,heading[2]);article.append(node);}
    else if(bullet){const node=el('p','report-bullet');inlineText(node,'• '+bullet[1]);article.append(node);}
    else if(line.trim()){const node=el('p');inlineText(node,line);article.append(node);}
  }
}
function addEntry(key,kind,label,text,time,append=false){
  $('#console-empty')?.remove();let entry=entries.get(key);if(!entry){const node=el('section',`log-entry ${kind}`);node.dataset.kind=kind;const header=el('header');header.append(el('strong','',label),el('time','',new Date(time||Date.now()).toLocaleTimeString('en-GB',{hour12:false})));const pre=el('pre');node.append(header,pre);$('#log').append(node);entry={node,pre,text:''};entries.set(key,entry);}
  entry.text=append?entry.text+text:text;entry.pre.textContent=entry.text;entry.node.classList.toggle('hidden-filter',selectedLog!=='all'&&kind!==selectedLog);$('#event-count').textContent=`${entries.size} activities`;scrollToLatest();return entry;
}
// Presentation text is buffered until a full content block arrives. This avoids
// briefly exposing a path or a workflow-loading sentence split across chunks.
function activityText(text){
  const internal=/(?:claude|\bskills?\b|\.claude|SKILL\.md|技能)/i;
  return String(text||'').replace(/```[\s\S]*?(?:```|$)/g,'').split('\n')
    .filter(line=>!internal.test(line))
    .map(line=>line
      .replace(/\[([^\]]+)\]\([^)]*\)/g,'$1')
      .replace(/(^|[\s("'`])(?:[A-Za-z]:[\\/]|\\\\|file:\/\/|~\/|\.{1,2}\/|\/)[^\s<>"'`]+/g,'$1[file]')
      .replace(/\b[\w.-]+(?:[\\/][\w. -]+)+\.[A-Za-z0-9]+\b/g,'[file]')
      .replace(/\b[\w.-]+\.(?:jsonl?|md|mo|mos|csv|mat|log|txt|py|sh|ya?ml|toml|xml|png|svg|pdf)\b/gi,'[file]')
      .replace(/`([^`]+)`/g,'$1').trim())
    .filter(line=>line&&!/^\s*[$>{}\[\]]/.test(line))
    .join('\n').trim();
}
function showNote(key,text,time){const safe=activityText(text);if(safe)addEntry(key,'assistant','ACTIVITY',safe,time);}
const toolEntries=new Map();
function showOperation(key,block,time){
  const name=block.name||'';
  if(/skill|agent|task|todo|plan|think|mcp/i.test(name))return;
  const input=block.input||{};
  if(/\.claude|[\\/]skills?[\\/]|SKILL\.md/i.test(String(input.file_path||input.path||input.pattern||'')))return;
  const summaries={Read:'Read 1 file',Write:'Write 1 file',Edit:'Update 1 file',MultiEdit:'Update 1 file',
    Glob:'Find files',Grep:'Search file contents',Bash:'Run 1 command',NotebookEdit:'Update 1 notebook',
    WebFetch:'Read 1 reference',WebSearch:'Search references'};
  const text=summaries[name];
  if(!text)return;
  addEntry(key,'tool','OPERATION',text,time);
  if(block.id)toolEntries.set(block.id,key);
}
function handleClaude(data,event){
  if(!data||typeof data!=='object')return;
  const parent=data.parent_tool_use_id||'main';
  if(data.type==='stream_event'){
    const ev=data.event||{};
    if(ev.type==='message_start')streamMessages.set(parent,ev.message?.id||String(event.id));
    const msg=streamMessages.get(parent)||parent;
    const blocks=streamBlocks.get(msg)||[];
    let block=blocks.find(b=>b.index===(ev.index||0));
    if(ev.type==='content_block_start'){
      block={...ev.content_block,key:`${msg}-${ev.index||0}`,index:ev.index||0,text:ev.content_block?.text||'',partial:''};
      blocks.push(block);streamBlocks.set(msg,blocks);
    }else if(ev.type==='content_block_delta'&&block){
      if(ev.delta?.type==='text_delta')block.text+=ev.delta.text||'';
      if(ev.delta?.type==='input_json_delta')block.partial+=ev.delta.partial_json||'';
    }else if(ev.type==='content_block_stop'&&block){
      if(block.type==='text')showNote(block.key,block.text,event.time);
      if(block.type==='tool_use'){
        if(block.partial){try{block.input=JSON.parse(block.partial);}catch{return;}}
        showOperation(block.key,block,event.time);
      }
    }
  }else if(data.type==='assistant'){
    const message=data.message||{},used=new Set();
    (message.content||[]).forEach((b,i)=>{
      const candidates=(streamBlocks.get(message.id)||[]).filter(x=>x.type===b.type&&!used.has(x.key));
      const matched=candidates.find(x=>b.id&&x.id===b.id)||candidates.find(x=>x.text===b.text)||(message.content.length===1?candidates.at(-1):candidates[0]);
      const key=matched?.key||`${message.id||event.id}-${i}`;used.add(key);
      if(b.type==='text')showNote(key,b.text,event.time);
      if(b.type==='tool_use')showOperation(key,b,event.time);
    });
  }else if(data.type==='user'){
    const content=data.message?.content;
    if(Array.isArray(content))for(const b of content){
      const key=toolEntries.get(b.tool_use_id),entry=entries.get(key);
      if(b.type==='tool_result'&&b.is_error&&entry&&!entry.text.endsWith(' — failed'))
        addEntry(key,'tool','OPERATION',entry.text+' — failed',event.time);
    }
  }else if(data.type==='result'){
    if(data.result)renderReport(data.result);
    else if(data.errors)renderReport('The run could not finish. See the exported records for diagnostic details.');
    if(data.permission_denials?.length)addEntry('permission-denied','status','STATUS','An operation was blocked by local permissions.',event.time);
  }
  // System events, internal reasoning, raw tool results and invocation details
  // remain in the saved records and never enter the presentation activity feed.
}
const activityStatuses={starting:'Starting the design workflow.',running:'Processing the operating requirements.',
 completed:'Design run finished. Review the results and acceptance checks.',failed:'The run could not finish. See the exported records for details.',
 stopped:'Run stopped. Existing results have been retained.',interrupted:'Run interrupted. Existing results have been retained.',
 timeout:'The run reached its time limit. Existing results have been retained.'};

function setRun(data){run={...run,...data};$('#run-status').textContent=statuses[run.status]||run.status;$('#run-status').className=`badge ${run.status==='completed'?'good':['failed','timeout','interrupted'].includes(run.status)?'bad':''}`;$('#live-dot').classList.toggle('running',!terminal());$('#run-id').textContent=`SESSION / ${run.id}`;updateControls();renderPhases();renderResults();tick();}
function receive(event){if(event.id<=lastEvent)return;lastEvent=event.id;eventCount++;switch(event.kind){case 'claude':handleClaude(event.data,event);break;case 'stderr':addEntry('runtime-notice','status','STATUS','A runtime diagnostic was recorded. See exported records for details.',event.time);break;case 'state':renderState(event.data);break;case 'status':setRun(event.data);addEntry(`status-${event.id}`,'status','STATUS',activityStatuses[event.data.status]||'Run status updated.',event.time);break;}}
async function loadRun(id){const generation=++loadGeneration;source?.close();source=null;const data=await api(`/api/runs/${id}`);if(generation!==loadGeneration)return;run=data.run;setFollow(true);const wasLive=!terminal();memory={};files=[];entries=new Map();streamMessages=new Map();streamBlocks=new Map();toolEntries.clear();seenPhases=new Set();eventCount=0;lastEvent=0;reportText='';visualSignature='';modelSelection='';signalSelection='';signalVariable='';modelTicket++;signalTicket++;resetVisuals();$('#report-button').disabled=true;$('#log').replaceChildren();$('#report').textContent='The final report will appear when the run finishes.';$('#event-count').textContent='0 activities';$('#prompt').value=run.prompt;$('#char-count').textContent=`${run.prompt.length} characters`;localStorage.setItem('t2h.single.lastRun',id);setRun(run);renderState(data);$('#connection').textContent='Connecting…';
 source=new EventSource(`/api/runs/${id}/events`);const currentSource=source;
 currentSource.onopen=()=>{$('#connection').textContent='Live connection established';};currentSource.onmessage=e=>{try{if(generation===loadGeneration)receive(JSON.parse(e.data));}catch(error){toast(`Activity parsing error: ${error.message}`);}};
 currentSource.onerror=()=>{$('#connection').textContent='Disconnected. Reconnecting…';};currentSource.addEventListener('end',async()=>{currentSource.close();$('#connection').textContent='Run archived';try{const final=await api(`/api/runs/${id}`);if(generation!==loadGeneration)return;setRun(final.run);renderState(final);if(!reportText)await loadSavedReport();refreshCharts();history();if(wasLive&&final.run.status==='completed'){toast('Run finished. The report and simulation evidence are ready.');}}catch(error){toast(error.message);}});
 if(terminal())loadSavedReport();refreshCharts();
}
async function loadSavedReport(){const id=run?.id;if(!id||!files.some(f=>f.name==='final-report.md'))return;const response=await fetch(fileUrl('final-report.md'));if(response.ok){const text=await response.text();if(run?.id===id)renderReport(text);}}
$('#start').onclick=async()=>{try{$('#start').disabled=true;const data=await post('/api/runs',{prompt:$('#prompt').value});await loadRun(data.id);history();}catch(e){toast(e.message);updateControls();}};
$('#stop').onclick=async()=>{if(!run)return;try{await post(`/api/runs/${run.id}/stop`,{});toast('Stopping the run. Existing records will be retained.');}catch(e){toast(e.message);}};
function scrollToLatest(){
  if(!follow||followFrame!==null)return;
  followFrame=requestAnimationFrame(()=>{followFrame=null;if(follow){const log=$('#log');log.scrollTop=log.scrollHeight;}});
}
function setFollow(enabled){
  follow=enabled;$('#follow').classList.toggle('active',follow);$('#follow').textContent=follow?'↓ Auto-follow on':'↓ Resume follow';
  $('#follow').setAttribute('aria-pressed',String(follow));$('#follow').title=follow?'Pause automatic scrolling':'Jump to the latest activity and keep following';
  if(follow)scrollToLatest();
}
$('#follow').onclick=()=>setFollow(!follow);
// Scrolling up never silently disables following. Only the explicit button pauses it.
new MutationObserver(scrollToLatest).observe($('#log'),{childList:true,subtree:true,characterData:true});
new ResizeObserver(scrollToLatest).observe($('#log'));
document.fonts?.ready.then(scrollToLatest);
setFollow(true);
function updatePermissionHelp(){
  const mode=$('#permission-mode').value;
  const detail=mode==='bypassPermissions'?'Tool approval prompts are skipped. Folder trust is a separate setting.':mode==='inherit'?'Uses the runtime’s local permission settings, including any globally configured bypass mode.':'Uses the selected permission mode.';
  $('#permission-help').textContent=detail+' Applies to new runs and newly opened sessions; active processes keep their current mode.';
}
$('#permission-mode').onchange=()=>{$('#permission-saved').textContent='';updatePermissionHelp();};
$('#save-permissions').onclick=async()=>{
  const button=$('#save-permissions');button.disabled=true;
  try{const data=await post('/api/settings/permissions',{permission_mode:$('#permission-mode').value});$('#permission-saved').textContent=data.message;toast('Permission settings saved.');}
  catch(error){$('#permission-saved').textContent=error.message;toast(error.message);}finally{button.disabled=false;}
};
$$('[data-log]').forEach(b=>b.onclick=()=>{selectedLog=b.dataset.log;$$('[data-log]').forEach(x=>x.classList.toggle('selected',x===b));entries.forEach(({node})=>node.classList.toggle('hidden-filter',selectedLog!=='all'&&node.dataset.kind!==selectedLog));scrollToLatest();});
$('#copy-log').onclick=async()=>{try{await navigator.clipboard.writeText([...entries.values()].map(x=>x.text).join('\n\n'));toast('Activity copied');}catch{toast('Unable to copy. Use Export run to download records.');}};
$('#open-session').onclick=async()=>{
  if(!run||!terminal())return;
  const button=$('#open-session');button.disabled=true;button.textContent='Opening…';
  try{const result=await post(`/api/runs/${run.id}/terminal`,{});$('#session-message').textContent=result.message;$('#session-command').textContent=result.command;$('#session-dialog').showModal();}
  catch(error){toast(error.message);}finally{button.textContent='Open session ↗';updateControls();}
};
$('#copy-session-command').onclick=async()=>{try{await navigator.clipboard.writeText($('#session-command').textContent);toast('Terminal command copied.');}catch{toast('Select and copy the manual terminal command.');}};
$('#export').onclick=()=>{if(run)location.href=`/api/runs/${run.id}/export`;};
async function history(){try{const records=await api('/api/runs');$('#history-count').textContent=records.length;$('#history-list').replaceChildren();if(!records.length)$('#history-list').append(el('p','muted','No runs yet. Submit requirements to create a saved run.'));records.forEach(record=>{const b=el('button','history-item');b.append(el('strong','',record.prompt),el('small','',`${new Date(record.created).toLocaleString('en-GB')} · ${record.id} · ${statuses[record.status]||record.status}`));b.onclick=()=>{$('#history-dialog').close();loadRun(record.id).catch(e=>toast(e.message));};$('#history-list').append(b);});return records;}catch(e){toast(e.message);return [];}}
$('#history-button').onclick=()=>{history();$('#history-dialog').showModal();};
function tick(){if(!run){$('#elapsed').textContent='00:00';return;}const seconds=Math.max(0,Math.floor(((terminal()?Date.parse(run.finished||run.created):Date.now())-Date.parse(run.created))/1000));$('#elapsed').textContent=`${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;}
setInterval(tick,1000);
function svgNode(tag,attributes={},text){const node=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(attributes).forEach(([k,v])=>node.setAttribute(k,v));if(text!==undefined)node.textContent=text;return node;}
function options(selector,values,selected,placeholder){const select=$(selector);select.replaceChildren();for(const value of values){const option=el('option','',value);option.value=value;select.append(option);}if(!values.length)select.append(el('option','',placeholder));else select.value=selected;select.disabled=!values.length;}
let sourceModelData=null, modelPoll=null, imageKey='';
let fitBox=[0,0,640,300], currentBox=[...fitBox], plotted=null;
function setBox(box){currentBox=box;$('#model-chart').setAttribute('viewBox',box.join(' '));}
function resetModel(){clearTimeout(modelPoll);modelPoll=null;sourceModelData=null;imageKey='';$('#model-chart').replaceChildren();$('#model-empty').hidden=false;$('#model-empty h3').textContent='From requirements to a circuit.';$('#model-empty p').textContent='The diagram will be rendered from the model’s component icons and layout annotations.';$('#model-download').hidden=true;$('#diagram-download').hidden=true;$('#render-model').disabled=true;$('#model-count').textContent='Awaiting model';$('#model-note').textContent='Library icons with the model’s original placement and connection lines.';}
function resetVisuals(){resetModel();options('#model-file',[],'','Waiting for .mo file');options('#signal-file',[],'','Waiting for CSV');options('#signal-variable',[],'','No signals yet');$('#csv-download').hidden=true;drawResponse({points:[]});}
function drawModel(data){
 sourceModelData=data;const svg=$('#model-chart'),busy=['queued','rendering','waiting'].includes(data.status);
 $('#render-model').disabled=!data.file||busy;$('#render-model').textContent=busy?'Rendering…':'↻ Render';
 $('#model-count').textContent=data.status==='ready'?`${data.components} components · ${data.connections} connections`:({queued:'Queued',rendering:'Rendering',waiting:'Updating',failed:'Render failed',unavailable:'Setup needed'}[data.status]||'Awaiting model');
 $('#model-note').textContent=[data.message||'Library icons with model Placement and Line annotations.',...(data.warnings||[])].join(' ');
 $('#diagram-download').hidden=data.status!=='ready';
 if(data.status!=='ready'){
   imageKey='';svg.replaceChildren();$('#model-empty').hidden=false;
   $('#model-empty h3').textContent=busy?'Rendering the hydraulic diagram…':data.status==='empty'?'From requirements to a circuit.':'Model diagram unavailable';
   $('#model-empty p').textContent=data.message||'Waiting for a generated model.';return;
 }
 $('#model-empty').hidden=true;
 const imageURL=`/api/runs/${run.id}/model-image?key=${encodeURIComponent(data.key)}&format=svg`;
 $('#diagram-download').href=fileUrl(`model-previews/${data.key}/diagram.svg`);$('#diagram-download').download=(data.file||'model').split('/').pop().replace(/\.mo$/i,'')+'-diagram.svg';
 if(imageKey===imageURL)return;
 imageKey=imageURL;svg.replaceChildren();const width=Number(data.width)||1200,height=Number(data.height)||900;
 fitBox=[0,0,width,height];setBox([...fitBox]);
 const picture=svgNode('image',{href:imageURL,x:0,y:0,width,height,preserveAspectRatio:'xMidYMid meet'});
 picture.addEventListener('error',()=>{if(imageKey!==imageURL)return;$('#model-empty').hidden=false;$('#model-empty h3').textContent='Unable to load model diagram';$('#model-empty p').textContent='Select Render to regenerate the image.';});svg.append(picture);
}
function zoomModel(factor){const [x,y,w,h]=currentBox;const width=w*factor;if(width<fitBox[2]/8||width>fitBox[2]*4)return;setBox([x+(w-width)/2,y+(h-h*factor)/2,width,h*factor]);}
$('#zoom-in').onclick=()=>zoomModel(.8);$('#zoom-out').onclick=()=>zoomModel(1.25);$('#model-fit').onclick=()=>setBox([...fitBox]);
let drag=null;$('#model-chart').onpointerdown=e=>{if(e.target.closest('.model-node'))return;const svg=$('#model-chart'),matrix=svg.getScreenCTM();if(!matrix)return;const point=new DOMPoint(e.clientX,e.clientY).matrixTransform(matrix.inverse());drag={x:point.x,y:point.y,box:[...currentBox],matrix:matrix.inverse()};svg.setPointerCapture(e.pointerId);};$('#model-chart').onpointermove=e=>{if(!drag)return;const point=new DOMPoint(e.clientX,e.clientY).matrixTransform(drag.matrix);setBox([drag.box[0]+drag.x-point.x,drag.box[1]+drag.y-point.y,drag.box[2],drag.box[3]]);};$('#model-chart').onpointerup=()=>drag=null;$('#model-chart').onpointercancel=()=>drag=null;
function signalUnit(name){if(/\.s$/.test(name))return 'm';if(/\.v$/.test(name))return 'm/s';if(/\.a$/.test(name))return 'm/s²';if(/\.p$/.test(name))return 'Pa';if(/\.q$/.test(name))return 'm³/s';if(/\.m_flow$/.test(name))return 'kg/s';if(/\.f$/.test(name))return 'N';return '';}
function drawResponse(data){
 const svg=$('#response-chart');svg.replaceChildren();$('#chart-tooltip').hidden=true;plotted=null;const points=data.points||[],variable=data.variable||'';
 $('#signal-title').textContent=variable||'Time response';$('#signal-unit').textContent=signalUnit(variable);$('#signal-unit').title='Unit inferred from standard Modelica variable naming; verify custom variable units in the model.';
 for(const [id,value] of [['min',data.minimum],['max',data.maximum],['last',data.last],['samples',data.samples]])$('#signal-'+id).textContent=number(value);
 $('#chart-note').textContent=data.file?`${data.samples} valid samples · ${points.length} preview points · Full data retained in CSV.`:'Simulation curves will appear when real CSV results are available.';
 if(!points.length){svg.append(svgNode('text',{x:320,y:110,'text-anchor':'middle'},data.file?'No finite samples for this parameter':'Waiting for simulation data'));return;}
 const left=64,right=614,top=18,bottom=184;
 let xmin=Math.min(...points.map(p=>p[0])),xmax=Math.max(...points.map(p=>p[0])),ymin=Math.min(...points.map(p=>p[1])),ymax=Math.max(...points.map(p=>p[1]));
 let target=variable==='worktable.v'?factValue('speed_max_m_s'):variable==='worktable.s'?factValue('stroke_m'):null;
 if(target!=null&&Number.isFinite(Number(target))){target=Number(target);ymin=Math.min(ymin,target);ymax=Math.max(ymax,target);}else target=null;
 if(xmax===xmin)xmax=xmin+1;if(ymax===ymin){const pad=Math.abs(ymin)*.1||1;ymin-=pad;ymax+=pad;}const margin=(ymax-ymin)*.1;ymin-=margin;ymax+=margin;
 const x=v=>left+(v-xmin)/(xmax-xmin)*(right-left),y=v=>bottom-(v-ymin)/(ymax-ymin)*(bottom-top);
 for(let i=0;i<=4;i++){const yy=top+(bottom-top)*i/4;svg.append(svgNode('line',{x1:left,x2:right,y1:yy,y2:yy,stroke:'#e6eeec'}),svgNode('text',{x:left-9,y:yy+3,'text-anchor':'end'},number(ymax-(ymax-ymin)*i/4,3)));const t=xmin+(xmax-xmin)*i/4;svg.append(svgNode('text',{x:x(t),y:202,'text-anchor':'middle'},number(t,2)));}
 if(target!==null){svg.append(svgNode('line',{x1:left,x2:right,y1:y(target),y2:y(target),stroke:'#c29e68','stroke-dasharray':'5 4'}),svgNode('text',{x:right,y:y(target)-4,'text-anchor':'end',fill:'#aa8655'},'Target'));}
 const path=points.map(p=>`${x(p[0])},${y(p[1])}`).join(' ');
 svg.append(svgNode('polyline',{points:path,fill:'none',stroke:'#008d83','stroke-width':2,'stroke-linejoin':'round'}),svgNode('text',{x:340,y:219,'text-anchor':'middle'},'Time (s)'));
 const cursor=svgNode('circle',{r:3.5,fill:'#008d83',stroke:'white','stroke-width':1.5,visibility:'hidden'});svg.append(cursor);plotted={points,x,y,xmin,xmax,left,right,cursor};
}
$('#response-chart').onpointermove=e=>{if(!plotted)return;const svg=$('#response-chart'),matrix=svg.getScreenCTM();if(!matrix)return;const loc=new DOMPoint(e.clientX,e.clientY).matrixTransform(matrix.inverse());const d=plotted,t=d.xmin+(loc.x-d.left)/(d.right-d.left)*(d.xmax-d.xmin);let point=d.points[0];for(const p of d.points)if(Math.abs(p[0]-t)<Math.abs(point[0]-t))point=p;d.cursor.setAttribute('cx',d.x(point[0]));d.cursor.setAttribute('cy',d.y(point[1]));d.cursor.setAttribute('visibility','visible');$('#chart-tooltip').textContent=`t = ${number(point[0])} s · ${number(point[1],6)} ${signalUnit(signalVariable)}`;$('#chart-tooltip').hidden=false;};$('#response-chart').onpointerleave=()=>{if(plotted)plotted.cursor.setAttribute('visibility','hidden');$('#chart-tooltip').hidden=true;};
async function refreshModel(retry=false){const id=run?.id;if(!id)return;clearTimeout(modelPoll);const ticket=++modelTicket;try{const data=await api(`/api/runs/${id}/model?file=${encodeURIComponent(modelSelection)}${retry?'&retry=1':''}`);if(run?.id!==id||ticket!==modelTicket)return;options('#model-file',data.files||[],data.file||'','Waiting for .mo file');$('#model-download').hidden=!data.file;if(data.file){$('#model-download').href=fileUrl(data.file);$('#model-download').download=data.file.split('/').pop();}drawModel(data);if(['queued','rendering','waiting'].includes(data.status))modelPoll=setTimeout(()=>{if(run?.id===id)refreshModel();},1500);}catch(e){if(ticket===modelTicket){drawModel({status:'failed',file:modelSelection,message:'Model preview request failed. Select Render to retry.'});toast(`Model preview: ${e.message}`);}}}
async function refreshSignals(){const id=run?.id;if(!id)return;const ticket=++signalTicket;try{const data=await api(`/api/runs/${id}/signals?file=${encodeURIComponent(signalSelection)}&variable=${encodeURIComponent(signalVariable)}`);if(run?.id!==id||ticket!==signalTicket)return;signalSelection=data.file||'';signalVariable=data.variable||'';options('#signal-file',data.files||[],signalSelection,'Waiting for CSV');options('#signal-variable',data.variables||[],signalVariable,'No numeric signals');$('#csv-download').hidden=!data.file;if(data.file){$('#csv-download').href=fileUrl(data.file);$('#csv-download').download=data.file.split('/').pop();}drawResponse(data);}catch(e){if(ticket===signalTicket)toast(`Simulation preview: ${e.message}`);}}
async function refreshCharts(){if(!run)return;await Promise.allSettled([refreshModel(),refreshSignals()]);}
$('#model-file').onchange=()=>{modelSelection=$('#model-file').value;resetModel();refreshModel();};$('#render-model').onclick=()=>refreshModel(true);$('#signal-file').onchange=()=>{signalSelection=$('#signal-file').value;refreshSignals();};$('#signal-variable').onchange=()=>{signalVariable=$('#signal-variable').value;refreshSignals();};$('#report-button').onclick=()=>$('#report-dialog').showModal();
async function init(){renderPhases();renderResults();resetVisuals();await checkHealth();const records=await history();const id=records.find(r=>['running','starting'].includes(r.status))?.id||localStorage.getItem('t2h.single.lastRun');if(id&&records.some(r=>r.id===id))await loadRun(id);updateControls();}
init().catch(e=>toast(e.message));
