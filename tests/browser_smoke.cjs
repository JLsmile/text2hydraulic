// Optional development test: PLAYWRIGHT_MODULE points to an installed Playwright module.
// Uses browser-only fixtures, never launches a model or writes fake run artifacts.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async () => {
  const browser = await chromium.launch({headless:true,
    ...(process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH} : {})});
  const page = await browser.newPage({viewport:{width:1512,height:1080}});
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
  await page.route('**/api/runs',route=>route.fulfill({json:[]}));
  await page.goto(process.env.DEMO_URL || 'http://127.0.0.1:8766');
  await page.waitForFunction(()=>document.querySelector('#environment').textContent.includes('ready'));
  await page.click('[data-example="table"]');
  assert(await page.locator('#start').isEnabled());
  await page.click('#settings-button');
  assert(await page.locator('#settings-dialog').isVisible());
  const setupResponse=page.waitForResponse(r=>r.url().endsWith('/api/environment/setup'));
  await page.click('#recheck');
  assert.equal((await setupResponse).status(),200);
  await page.waitForFunction(()=>!document.querySelector('#recheck').disabled);
  assert(await page.locator('.setup-note').isVisible());
  let savedMode=null;
  await page.route('**/api/settings/permissions',route=>{savedMode=route.request().postDataJSON().permission_mode;return route.fulfill({json:{permission_mode:savedMode,terminal_permission_mode:savedMode,message:'Permission settings saved.'}});});
  await page.selectOption('#permission-mode','bypassPermissions');
  await page.click('#save-permissions');
  await page.waitForFunction(()=>document.querySelector('#permission-saved').textContent.includes('saved'));
  assert.equal(savedMode,'bypassPermissions');
  assert((await page.locator('#permission-help').innerText()).includes('approval prompts are skipped'));
  await page.click('[data-close="settings-dialog"]');
  // Stream fragments must not leak runtime internals or file paths.
  const parsed=await page.evaluate(()=>{
    let id=1;
    const send=data=>handleClaude(data,{id:id++,time:new Date().toISOString()});
    send({type:'stream_event',event:{type:'message_start',message:{id:'test-message'}}});
    for(const [index,type,value] of [[0,'thinking','Private reasoning'],[1,'text','Now let me also add an issues entry about the velocity discrepancy:']]){
      send({type:'stream_event',event:{type:'content_block_start',index,content_block:{type}}});
      send({type:'stream_event',event:{type:'content_block_delta',index,delta:{type:type==='text'?'text_delta':'thinking_delta',[type]:value}}});
      send({type:'assistant',message:{id:'test-message',content:[{type,[type]:value}]}});
      send({type:'stream_event',event:{type:'content_block_stop',index}});
    }
    const tool=(id,name,input)=>send({type:'assistant',message:{id,content:[{type:'tool_use',id,name,input}]}});
    tool('tool1','Read',{file_path:'/home/private/project/memory.json'});
    tool('tool2','Skill',{skill:'text2hydraulic'});
    tool('tool3','Read',{file_path:'.claude/skills/text2hydraulic/SKILL.md'});
    send({type:'user',message:{content:[{type:'tool_result',tool_use_id:'tool1',content:'<script>window.injected=true</script> secret file contents'}]}});
    send({type:'system',subtype:'init',model:'claude-test'});
    send({type:'assistant',message:{id:'internal',content:[{type:'text',text:'I will invoke the text2hydraulic skill using Claude Code.'}]}});
    send({type:'stream_event',event:{type:'content_block_start',index:2,content_block:{type:'text'}}});
    send({type:'stream_event',event:{type:'content_block_delta',index:2,delta:{type:'text_delta',text:'Reading /home/private/'}}});
    if(document.querySelector('#log').textContent.includes('/home/'))throw Error('Partial path leaked');
    send({type:'stream_event',event:{type:'content_block_delta',index:2,delta:{type:'text_delta',text:'project/memory.json'}}});
    send({type:'stream_event',event:{type:'content_block_stop',index:2}});
    if(activityText('Check velocity at 0.1 m/s.')!=='Check velocity at 0.1 m/s.')throw Error('Unit was changed');
    return [...document.querySelectorAll('.log-entry pre')].map(e=>e.textContent);
  });
  assert.equal(parsed.filter(t=>t.startsWith('Now let me')).length,1);
  assert.equal(parsed.filter(t=>t==='Read 1 file').length,1);
  assert(!/claude|skill|private|memory\.json|secret file|reasoning/i.test(parsed.join(' ')));
  assert.equal(await page.evaluate(()=>window.injected),undefined);
  assert.equal(await page.evaluate(()=>/[\u4e00-\u9fff]/.test(document.body.innerText)),false);
  await page.click('[data-log="tool"]');
  assert.equal(await page.locator('.log-entry:not(.hidden-filter)').count(),1);
  await page.click('[data-log="all"]');
  await page.evaluate(()=>{
    drawResponse({variable:'worktable.v',points:[[0,0],[1,.1],[2,.2]],samples:3,minimum:0,maximum:.2,last:.2});
    renderReport('# Test report\n<script>window.injected=true</script>\n```modelica\nmodel Test end Test;\n```');
  });
  assert.equal(await page.locator('#response-chart polyline').count(),1);
  assert.equal(await page.locator('#report h3').innerText(),'Test report');
  assert.equal(await page.evaluate(()=>window.injected),undefined);
  // Follow survives wheel scrolling and DOM/layout updates; only the button pauses.
  await page.evaluate(()=>{for(let i=0;i<70;i++)addEntry(`scroll-${i}`,'assistant','ACTIVITY',`Operation ${i}: checking simulation results.`,new Date().toISOString());});
  const atBottom=()=>{const log=document.querySelector('#log');return log.scrollHeight-log.clientHeight-log.scrollTop<3;};
  await page.waitForFunction(atBottom);
  await page.evaluate(()=>{const log=document.querySelector('#log');log.scrollTop=0;log.dispatchEvent(new WheelEvent('wheel',{deltaY:-100}));addEntry('scroll-latest','assistant','ACTIVITY','The latest operation.',new Date().toISOString());});
  await page.waitForFunction(atBottom);
  assert.equal(await page.locator('#follow').getAttribute('aria-pressed'),'true');
  await page.evaluate(()=>{document.querySelector('#follow').click();document.querySelector('#log').scrollTop=0;addEntry('scroll-paused','assistant','ACTIVITY','An operation while paused.',new Date().toISOString());});
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.equal(await page.locator('#log').evaluate(e=>e.scrollTop),0);
  await page.evaluate(()=>document.querySelector('#follow').click());
  await page.waitForFunction(atBottom);
  // Mock only the desktop-launch boundary; never launch an actual model in tests.
  await page.route('**/api/runs/abcdef123456/terminal',r=>r.fulfill({json:{opened:false,command:'cd /demo/run && runtime --resume saved-session --permission-mode default',message:'Run this command in a terminal on the computer hosting the demo.'}}));
  await page.evaluate(()=>setRun({id:'abcdef123456',status:'running',created:new Date().toISOString(),prompt:'Test'}));
  assert(await page.locator('#open-session').isDisabled());
  await page.evaluate(()=>setRun({status:'failed',finished:new Date().toISOString()}));
  await page.click('#open-session');
  await page.locator('#session-dialog').waitFor({state:'visible'});
  assert(await page.locator('#session-dialog').isVisible());
  assert((await page.locator('#session-command').textContent()).includes('--permission-mode default'));
  await page.click('[data-close="session-dialog"]');
  // Dropdowns read only variables supplied by the real API; mocked data stays in this browser.
  let renderCalls=0;
  await page.route('**/api/runs/abcdef123456/model?*',r=>{renderCalls++;return r.fulfill({json:{file:'Circuit.mo',files:['Circuit.mo'],key:'a'.repeat(64),status:renderCalls===1?'rendering':'ready',message:'Library icons with model placement.',components:2,connections:1,width:640,height:480,warnings:[]}});});
  await page.route('**/api/runs/abcdef123456/model-image?*',r=>r.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect x="10" y="10" width="620" height="460" fill="white"/><circle cx="320" cy="240" r="70" fill="none" stroke="black"/></svg>'}));
  await page.route('**/api/runs/abcdef123456/signals?*',r=>{const variable=new URL(r.request().url()).searchParams.get('variable')||'worktable.v';return r.fulfill({json:{file:'response.csv',files:['response.csv','other.csv'],variables:['worktable.v','worktable.s','cylinder.port_a.p'],variable,points:variable.endsWith('.p')?[[0,1e6],[1,2e6]]:[[0,0],[1,.1]],samples:2,minimum:0,maximum:variable.endsWith('.p')?2e6:.1,last:.1}});});
  await page.evaluate(()=>refreshCharts());
  assert.equal(await page.locator('#model-count').textContent(),'Rendering');
  await page.waitForFunction(()=>document.querySelector('#model-chart image'));
  assert(renderCalls>=2);
  assert.equal(await page.locator('#model-chart image').count(),1);
  assert((await page.locator('#diagram-download').getAttribute('href')).includes('diagram.svg'));
  assert.equal(await page.locator('#model-layout').count(),0);
  const fit=await page.locator('#model-chart').getAttribute('viewBox');await page.click('#zoom-in');
  assert.notEqual(await page.locator('#model-chart').getAttribute('viewBox'),fit);
  await page.click('#model-fit');assert.equal(await page.locator('#model-chart').getAttribute('viewBox'),fit);
  await page.selectOption('#signal-variable','cylinder.port_a.p');
  await page.waitForFunction(()=>document.querySelector('#signal-title').textContent==='cylinder.port_a.p');
  assert.equal(await page.locator('#signal-unit').textContent(),'Pa');
  assert.equal(await page.locator('#response-chart polyline').count(),1);
  const boxes=await page.locator('.dashboard > .panel').evaluateAll(nodes=>nodes.map(n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y};}));
  assert.equal(boxes[0].y,boxes[1].y);assert.equal(boxes[2].y,boxes[3].y);assert.equal(boxes[0].x,boxes[2].x);assert(boxes[0].y<boxes[2].y);
  await page.screenshot({path:process.env.SCREENSHOT_PATH||'/tmp/t2h-single-browser-test.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  assert.deepEqual(errors,[]);
  await browser.close();
  console.log('Browser checks passed: desktop/mobile, examples, settings, safe activity summaries and stream deduplication, tool filter, four-panel layout, native model image, render polling and zoom, selectable simulation signals, and safe report rendering.');
})().catch(error=>{console.error(error);process.exit(1);});
