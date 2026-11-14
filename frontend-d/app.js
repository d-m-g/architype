import {createSourcePreviewLoader,sourcePreviewKind,loadPDFPreview} from './local-file.js';
import {historyStore} from './history-state.js';
import {requestDrawing,requestJSON,getDrawingScene,getConnectionStatus,isDrawingPath,recoveryMessage,LIVE_REQUEST_TIMEOUT_MS} from './api-client.js';
import {publishEditor} from './editor-bridge.js';
import {scene as mockScene,scenarios,resolveMockQuestion,zoomAt,detectedAreas} from './mock-scene.js';
import {adaptScene,adaptAnswer,adaptAnswerEvidence,imageURL,summarizeScene} from './contracts.js';
let apiAbort=null, lastRequest=null;
let requestNumber=0,apiSelected=false,overviewMode=false;
let scene=mockScene,currentPage=0,shared=false,rawScene=null,rawAnswer=null,sourcePreview=null;
const evidenceFor=r=>[...new Set(r.evidence)].map(id=>scene.entities.find(e=>e.id===id)).filter(Boolean);
const drawingNames=new Map();
const drawingLabel=path=>drawingNames.get(path)??path;
const $=id=>document.getElementById(id), NS='http://www.w3.org/2000/svg';
let result=scenarios.windows,selected=null,view={x:0,y:0,w:1000,h:760};
const svg=$('drawing');
const previewLoader=createSourcePreviewLoader(path=>new Promise((resolve,reject)=>{
 const image=new Image();image.onload=()=>resolve({width:image.naturalWidth,height:image.naturalHeight});
 image.onerror=()=>reject(new Error('The uploaded source image could not be loaded. Check your sign-in and connection, then select the file again.'));
 image.src=imageURL(path);
}),loadPDFPreview);
function emptyDisplayScene(entities=[]){return {width:sourcePreview?.width??1000,height:sourcePreview?.height??760,page:1,entities,pages:[]};}
function showSourcePreview(path){
 previewLoader.select(path,preview=>{
  if(shared||!apiSelected)return;
  sourcePreview=preview;scene={...scene,width:preview.width??1000,height:preview.height??760};
  const image=svg.querySelector('image');image.setAttribute('href',preview.displayURL??imageURL(preview.image));image.setAttribute('width',preview.width);image.setAttribute('height',preview.height);
  $('canvas-label').textContent=preview.type==='pdf'?`PDF page 1 of ${preview.pageCount}. Preview only; API detections appear after analysis. The upload reader currently analyses page 1.`:'SOURCE PREVIEW - No extracted overlays';
  svg.setAttribute('aria-label','Source image preview without extracted evidence overlays. Arrow keys pan; plus and minus zoom.');
  $('source-pill').textContent='Team API · source preview; analysis separate';render();
 },error=>{if(!shared&&apiSelected)$('canvas-label').textContent=error.message;});
}
function clearSourcePreview(){previewLoader.cancel();sourcePreview=null;svg.closest('.canvas-wrap').hidden=false;}

function el(tag,attrs={}){const node=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))node.setAttribute(k,v);return node;}
function syncEditor(){publishEditor({wireScene:shared?rawScene:null,answer:result.raw??result,entities:scene.entities.map(e=>({...e,page:e.page??0})),selectedId:selected,pageIndex:currentPage,viewport:{...view},sourcePreview:shared?null:sourcePreview,mode:shared?'shared':apiSelected?'pending':'mock'});}
function updateView(publish=true){svg.setAttribute('viewBox',`${view.x} ${view.y} ${view.w} ${view.h}`);$('zoom-label').textContent=`${Math.round(scene.width*100/view.w)}%`;if(publish)syncEditor();}
function revealSourceEvidence(entity,scroll=true){
 const inspector=$('detected-source');inspector.hidden=true;inspector.replaceChildren();
 document.querySelectorAll('.evidence-source-detail').forEach(node=>node.remove());
 const row=[...$('evidence-list').querySelectorAll('[data-entity]')].find(node=>node.dataset.entity===entity.id);
 const details=document.createElement('span');details.className='evidence-source-detail';
 details.textContent=[entity.context,entity.quantityText,`Page ${(entity.page??0)+1}`,entity.sourceRef,entity.method,entity.uncertain?'Needs review':null].filter(Boolean).join(' - ');
 let target;
 if(row){row.querySelector('.evidence-text').append(details);target=row;}
 else{
  const title=document.createElement('h3');title.textContent=entity.label;
  const note=document.createElement('p');note.className='muted small';note.textContent='Selected detection - not cited in this answer';
  inspector.append(title,note,details);inspector.hidden=false;target=inspector;
 }
 if(scroll){target.scrollIntoView({block:'nearest',behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});target.focus({preventScroll:true});}
}
function select(id,focus=true){const entity=scene.entities.find(e=>e.id===id);if(!entity)return;selected=id;if(shared&&entity.page!==currentPage){setPage(entity.page);render();}selected=id;document.querySelectorAll('[data-entity]').forEach(n=>{const active=n.dataset.entity===id;n.classList.toggle('selected',active);n.setAttribute('aria-pressed',String(active));});revealSourceEvidence(entity,!focus);$('selection').textContent=`${entity.label} (${entity.type})${entity.quantityText?' - '+entity.quantityText:''} · ${entity.sourceRef} · Page ${(entity.page??(scene.page-1))+1} · ${entity.method}${entity.uncertain?' · Needs review':''}`;if(!entity.polygon.length){$('selection').textContent+=' · No drawable geometry';syncEditor();return;}if(!focus){syncEditor();return;}const xs=entity.polygon.map(p=>p[0]),ys=entity.polygon.map(p=>p[1]);view.x=(Math.min(...xs)+Math.max(...xs))/2-view.w/2;view.y=(Math.min(...ys)+Math.max(...ys))/2-view.h/2;updateView();}
function render(){if(overviewMode&&shared&&rawScene)result={...result,...summarizeScene(rawScene,currentPage),scope:`Page ${currentPage+1} of ${rawScene.pages.length}`};$('ask-submit').textContent=(shared||apiSelected)?'Ask live drawing':'Inspect mock evidence';selected=null;const entities=evidenceFor(result);$('answer').replaceChildren();$('answer').classList.toggle('empty',result.answer===null);$('answer').append(document.createTextNode(result.answer??'Not enough evidence'));if(result.unit){const unit=document.createElement('span');unit.textContent=` ${result.unit}`;$('answer').append(unit);}$('status').textContent=result.status;$('status').classList.toggle('warning',result.answer===null||entities.some(e=>e.uncertain));$('scope').textContent=result.scope;$('answer-confidence').textContent=result.confidenceText??'Not calibrated';$('explanation').textContent=result.explanation;$('formula').hidden=!result.formula;$('formula').textContent=result.formula??'';$('evidence-count').textContent=`${entities.length} linked`;$('evidence-list').replaceChildren();$('detected-source').hidden=true;$('detected-source').replaceChildren();$('overlays').replaceChildren();$('selection').textContent='Click a detected area to inspect it. Drag to pan; scroll or pinch to zoom; double-click to zoom in.';
if(shared){for(const entity of detectedAreas(scene.entities,currentPage,new Set(result.evidence))){const group=el('g',{class:'entity detected-area',role:'button',tabindex:'0','aria-label':`Detected ${entity.type}: ${entity.label}. Not cited in this answer.`,'aria-pressed':'false','data-entity':entity.id});const title=el('title');title.textContent=`${entity.label} - ${entity.quantityText??entity.type} - Detected area, not answer evidence`;group.append(title,el('polygon',{points:entity.polygon.map(p=>p.join(',')).join(' ')}));group.addEventListener('click',()=>select(entity.id,false));group.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();e.stopPropagation();select(entity.id,false);}});$('overlays').append(group);}}
entities.forEach((entity,i)=>{if(entity.polygon.length&&(!shared||entity.page===currentPage)){const group=el('g',{class:`entity${entity.uncertain?' uncertain':''}`,role:'button',tabindex:'0','aria-label':`${i+1}. ${entity.label}${entity.uncertain?', needs review':''}`,'aria-pressed':'false','data-entity':entity.id});group.append(el('polygon',{points:entity.polygon.map(p=>p.join(',')).join(' ')}));const [x,y]=entity.polygon[0];group.append(el('circle',{cx:x+12,cy:y+10,r:15}));const text=el('text',{x:x+12,y:y+10});text.textContent=i+1;group.append(text);group.addEventListener('click',()=>select(entity.id,false));group.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();e.stopPropagation();select(entity.id,false);}});$('overlays').append(group);}
const row=document.createElement('button');row.className=`evidence-row${entity.uncertain?' uncertain':''}`;row.dataset.entity=entity.id;row.setAttribute('aria-pressed','false');const number=document.createElement('span');number.className='evidence-number';number.textContent=i+1;const label=document.createElement('span');label.className='evidence-text';label.textContent=entity.label;const detail=document.createElement('small');detail.textContent=entity.uncertain?'Needs review · uncertain candidate':[entity.quantityText??(entity.quantity!==undefined?`${entity.quantity} ${entity.unit} · supplied mock quantity`:null),entity.context].filter(Boolean).join(' · ')||entity.sourceRef;label.append(detail);const arrow=document.createElement('span');arrow.className='row-arrow';arrow.setAttribute('aria-hidden','true');arrow.textContent='↗';row.append(number,label,arrow);if(shared)detail.textContent+=` · Page ${entity.page+1}${entity.polygon.length?'':' · No geometry'}`;if(entity.geometryUnavailable)detail.textContent+=` · Page ${entity.page+1} · No overlay geometry`;row.onclick=()=>{if(!$('overlay-toggle').checked){$('overlay-toggle').checked=true;$('overlays').style.display='';}select(entity.id);};$('evidence-list').append(row);});
if(!entities.length){const empty=document.createElement('p');empty.className='muted small';empty.textContent='No supporting entities for this question.';$('evidence-list').append(empty);}
$('trace-list').replaceChildren();for(const line of ((shared||apiSelected)?traceLines():['Match the exact sample question to a local demonstration fixture.',`Scope: ${result.scope}.`,entities.length?`Display ${entities.length} unique fixture entities on page 1.`:'Abstain: no supporting evidence supplied.'])){const li=document.createElement('li');li.textContent=line;$('trace-list').append(li);}document.querySelectorAll('[data-scenario]').forEach(b=>{const active=result===scenarios[b.dataset.scenario];b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});view={x:0,y:0,w:scene.width,h:scene.height};updateView();}
$('ask-form').onsubmit=e=>{e.preventDefault();if(shared||apiSelected){askAPI();return;}result=resolveMockQuestion($('question').value);render();recordHistory('mock',$('question').value);};document.querySelectorAll('[data-scenario]').forEach(b=>b.onclick=()=>{restoreMock();result=scenarios[b.dataset.scenario];$('question').value=result.question;render();recordHistory('mock',result.question);});$('overlay-toggle').onchange=e=>{$('overlays').style.display=e.target.checked?'':'none';};function zoom(f,anchor={x:view.x+view.w/2,y:view.y+view.h/2}){view=zoomAt(view,f,anchor,scene.width);updateView();}$('zoom-in').onclick=()=>zoom(1.25);$('zoom-out').onclick=()=>zoom(.8);$('fit').onclick=()=>{view={x:0,y:0,w:scene.width,h:scene.height};updateView();};function pan(direction){const step=view.w*.12;if(direction==='left')view.x-=step;if(direction==='right')view.x+=step;if(direction==='up')view.y-=step;if(direction==='down')view.y+=step;updateView();}document.querySelectorAll('[data-pan]').forEach(b=>b.onclick=()=>pan(b.dataset.pan));svg.addEventListener('keydown',e=>{if(e.target!==svg)return;const directions={ArrowLeft:'left',ArrowRight:'right',ArrowUp:'up',ArrowDown:'down'};if(directions[e.key]){e.preventDefault();pan(directions[e.key]);}if(e.key==='+'||e.key==='='){e.preventDefault();zoom(1.25);}if(e.key==='-'){e.preventDefault();zoom(.8);}});
function svgPoint(e){const p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;return p.matrixTransform(svg.getScreenCTM().inverse());}
// Paint once per animation frame; pointer movement must not rebuild the editor tree.
let viewFrame=0,viewSyncTimer;function scheduleView(){if(!viewFrame)viewFrame=requestAnimationFrame(()=>{viewFrame=0;updateView(false);});clearTimeout(viewSyncTimer);viewSyncTimer=setTimeout(syncEditor,150);}
const pointers=new Map();let gesture=null,suppressClick=false;
function startGesture(){
 const points=[...pointers.values()],a=points[0],b=points[1]??a;
 if(!a){gesture=null;svg.classList.remove('is-panning');return;}
 const matrix=svg.getScreenCTM()?.inverse();if(!matrix)return;
 const point=svg.createSVGPoint();point.x=(a.x+b.x)/2;point.y=(a.y+b.y)/2;
 gesture={view:{...view},matrix,anchor:point.matrixTransform(matrix),distance:Math.hypot(a.x-b.x,a.y-b.y),x:point.x,y:point.y};
}
svg.addEventListener('pointerdown',e=>{if(e.button!==0||(document.body.classList.contains('correcting-scene')||svg.classList.contains('calibrating')))return;if(!pointers.size)suppressClick=false;pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});e.target.setPointerCapture(e.pointerId);startGesture();});
svg.addEventListener('pointermove',e=>{
 if(!pointers.has(e.pointerId)||!gesture)return;pointers.set(e.pointerId,{x:e.clientX,y:e.clientY});
 const points=[...pointers.values()],a=points[0],b=points[1]??a,point=svg.createSVGPoint();point.x=(a.x+b.x)/2;point.y=(a.y+b.y)/2;
 if(Math.hypot(point.x-gesture.x,point.y-gesture.y)>4||points.length>1){suppressClick=true;svg.classList.add('is-panning');}
 if(!suppressClick)return;
 const current=point.matrixTransform(gesture.matrix),distance=Math.hypot(a.x-b.x,a.y-b.y);
 view=zoomAt(gesture.view,gesture.distance>0&&distance>0?distance/gesture.distance:1,gesture.anchor,scene.width);
 const ratio=view.w/gesture.view.w;view.x+=(gesture.anchor.x-current.x)*ratio;view.y+=(gesture.anchor.y-current.y)*ratio;scheduleView();
});
function endPointer(e){pointers.delete(e.pointerId);startGesture();if(!pointers.size)syncEditor();}
svg.addEventListener('pointerup',endPointer);svg.addEventListener('pointercancel',endPointer);svg.addEventListener('lostpointercapture',endPointer);
svg.addEventListener('click',e=>{if(suppressClick){e.preventDefault();e.stopImmediatePropagation();suppressClick=false;}},true);
svg.addEventListener('wheel',e=>{if((document.body.classList.contains('correcting-scene')||svg.classList.contains('calibrating')))return;e.preventDefault();const amount=e.deltaY*(e.deltaMode===1?16:e.deltaMode===2?svg.clientHeight:1);view=zoomAt(view,Math.exp(-Math.max(-150,Math.min(150,amount))*.004),svgPoint(e),scene.width);scheduleView();},{passive:false});
svg.addEventListener('dblclick',e=>{if((document.body.classList.contains('correcting-scene')||svg.classList.contains('calibrating')))return;e.preventDefault();zoom(e.shiftKey?.5:2,svgPoint(e));});
render();

// Dashboard deep links select an existing mock scenario; all viewer controls remain unchanged.
function openLinkedScenario(){const key=new URLSearchParams(location.hash.slice(1)).get('scenario');if(Object.hasOwn(scenarios,key)){restoreMock();result=scenarios[key];$('question').value=result.question;render();recordHistory('mock',result.question);}}
window.addEventListener('hashchange',openLinkedScenario);openLinkedScenario();

function traceLines(){return [...(result.trace??[]).map(t=>`${t.step} · ${t.model??'CPU'} · ${t.in_tokens??0}/${t.out_tokens??0} tokens · ${t.gpu_s??0} GPU s · $${t.usd??0} · ${t.ms??0} ms${t.cached?' · cached':''}`),`Query cost: $${result.cost_usd??0} (scene build excluded)`,rawScene?`Scene build: $${(rawScene.trace??[]).reduce((n,t)=>n+(t.usd??0),0)}`:'Scene build cost: unavailable', ...Object.entries(result.assumptions??{}).map(([k,v])=>`Assumption: ${k} = ${v}`)];}
function setPage(index){currentPage=index;const p=scene.pages[index];scene.width=p?.width??1000;scene.height=p?.height??760;const image=svg.querySelector('image');image.setAttribute('href',p?imageURL(p.image):'');image.setAttribute('width',scene.width);image.setAttribute('height',scene.height);$('page-select').value=String(index);}
function showShared(s,a,origin='imported'){overviewMode=false;const previousPage=currentPage,previousCamera={...view},oldPage=rawScene?.pages[previousPage],newPage=s.pages[previousPage];const keepCamera=shared&&rawScene.path===s.path&&oldPage?.image===newPage?.image&&oldPage?.width===newPage?.width&&oldPage?.height===newPage?.height;clearSourcePreview();historyStore.setDrawing('drawing:'+s.path,drawingLabel(s.path));$('history-view-note').hidden=true;const next=adaptScene(s,a),answer=adaptAnswer(a,next);scene=next;result=answer;rawScene=s;rawAnswer=a;shared=true;$('page-select').replaceChildren();scene.pages.forEach((p,i)=>{const option=document.createElement('option');option.value=i;option.textContent=`Page ${i+1}`;$('page-select').append(option);});$('page-select').hidden=false;setPage(keepCamera?previousPage:0);$('drawing-title').textContent=drawingLabel(s.path);$('mode-label').textContent=origin==='live'?'TEAM API':'IMPORTED EVIDENCE';$('answer-mode').textContent='ANSWER / REPORTED';$('source-pill').textContent=origin==='live'?'Team API · reported evidence':'Imported schema v1 · reported evidence';$('source-note').textContent='Source attribution is not supplied by schema v1; consult the dataset license before sharing.';$('trace-note').textContent='Reported trace and assumptions. Assumptions are read-only: the API has no override field.';$('canvas-label').textContent='Source drawing with detected areas and answer evidence.';svg.setAttribute('aria-label','Source page with evidence overlays; arrow keys pan, plus and minus zoom.');document.querySelector('.demo-note').textContent='Reported answer. Sample buttons below restore the synthetic demonstration.';$('api-path').value=s.path;$('contract-status').textContent=`Loaded ${s.builder}; searched for: ${s.covers.join(', ')}. ${answer.missing.length?'Unresolved evidence: '+answer.missing.join(', '):''}`;render();if(keepCamera){view=previousCamera;updateView();}}
function restoreMock(){clearSourcePreview();historyStore.setDrawing('mock:courtyard','Courtyard studio · mock');$('history-view-note').hidden=true;svg.setAttribute('aria-label','Synthetic ground floor. Use evidence list to select objects; arrow keys pan and plus or minus zoom.');apiAbort?.abort();apiAbort=null;setBusy(false);$('retry-request').hidden=true;rawScene=null;rawAnswer=null;apiSelected=false;requestNumber++;shared=false;scene=mockScene;currentPage=0;$('page-select').hidden=true;const img=svg.querySelector('image');img.setAttribute('href','plan.svg');img.setAttribute('width',1000);img.setAttribute('height',760);$('drawing-title').textContent='Courtyard studio';$('mode-label').textContent='MOCK WORKSPACE';$('answer-mode').textContent='ANSWER / MOCK';$('source-pill').textContent='Synthetic drawing · no live AI';$('source-note').textContent=scene.attribution;$('trace-note').textContent='Local fixture · No model calls · No measured accuracy or cost';$('canvas-label').textContent='DEMO DRAWING · Not for construction';document.querySelector('.demo-note').textContent='Sample questions only. Answers are mock fixtures.';$('contract-status').textContent='Synthetic mock mode.';}
$('page-select').onchange=()=>{setPage(Number($('page-select').value));render();};
svg.querySelector('image').addEventListener('error',()=>{if(shared)$('contract-status').textContent='Page image unavailable. Configure the /api proxy and dataset. Evidence coordinates cannot be visually verified until the image loads.';});
$('load-json').onclick=async()=>{
 let id;
 try{
  const sf=$('scene-file').files[0],af=$('answer-file').files[0];if(!sf||!af)throw new Error('Choose both a Scene JSON and its matching Answer JSON.');
  apiAbort?.abort();apiAbort=null;setBusy(false);id=++requestNumber;
  const [sceneText,answerText]=await Promise.all([sf.text(),af.text()]);
  if(id!==requestNumber)return;
  showShared(JSON.parse(sceneText),JSON.parse(answerText));recordHistory('imported',null,rawScene.path);
 }catch(error){if(id===undefined||id===requestNumber)$('contract-status').textContent=error.message;}
};
async function jsonResponse(response){if(!response.ok)throw new Error(`API unavailable (${response.status}). Scene builders may still return 501.`);return response.json();}


function setBusy(busy){
 $('ask-submit').disabled=busy;$('ask-api').disabled=busy;
 $('cancel-request').hidden=!busy;
 $('ask-form').setAttribute('aria-busy',String(busy));
 if(busy)$('retry-request').hidden=true;
}
function cancelRequest(){
 if(!apiAbort)return;
 apiAbort.abort();apiAbort=null;requestNumber++;setBusy(false);
 const retained=shared&&rawScene;
 if(retained)$('status').textContent='Previous result - request cancelled';
 else{result={answer:null,status:'Cancelled',scope:'Not determined',evidence:[],explanation:'Request cancelled. No answer was accepted.'};render();}
 $('contract-status').textContent='Cancelled locally. Your drawing and question are kept. Retry uses the text currently in the question box. Server work may still finish.';$('retry-request').hidden=!lastRequest;if(lastRequest&&!retained)recordHistory('live',lastRequest.question,lastRequest.path);
}
$('cancel-request').onclick=cancelRequest;
window.addEventListener('stoa:cancel-request',cancelRequest);
$('retry-request').onclick=()=>{if(lastRequest){$('api-path').value=lastRequest.path;askAPI();}};
async function askAPI(){
 window.dispatchEvent(new Event('stoa:close-local'));
 const path=$('api-path').value.trim(),question=$('question').value.trim();
 if(!isDrawingPath(path)||!question){$('contract-status').textContent='Enter a data/ or uploads/ drawing path and a question.';return;}
 lastRequest={path,question};apiAbort?.abort();apiAbort=new AbortController();const controller=apiAbort,id=++requestNumber;const timer=setTimeout(()=>controller.abort(),LIVE_REQUEST_TIMEOUT_MS);
 const keepScene=shared&&rawScene?.path===path;
 if(!keepScene)openPending(path,question);else{$('history-view-note').hidden=true;$('status').textContent='Previous result - answering...';}
 setBusy(true);$('contract-status').textContent='Requesting scene and answer from the team API…';
 try{
  const response=await requestDrawing(path,question,controller.signal,message=>{if(id===requestNumber)$('contract-status').textContent=message;});if(id!==requestNumber)return;
  if(keepScene&&!response.answer){$('status').textContent='Previous result - request failed';$('contract-status').textContent=recoveryMessage(response.errors.join(' - '))+' Previous answer and evidence remain displayed.';$('retry-request').hidden=false;return;}
  if(response.scene){const answer=response.answer??{answer:null,unit:null,confidence:0,tier:'none',evidence:[],extra_entities:[],trace:[],error:response.errors.join(' · '),cost_usd:0};showShared(response.scene,answer,'live');}
  else{shared=false;apiSelected=true;const a=response.answer;scene=emptyDisplayScene(a?adaptAnswerEvidence(a):[]);rawScene=null;rawAnswer=a??null;result=a?adaptAnswer(a,scene):{answer:null,status:'API unavailable',scope:'Not determined',evidence:[],explanation:response.errors.join(' · ')};result.explanation=[result.explanation,'No extracted scene available; displaying a source preview does not verify the reported answer.'].filter(Boolean).join(' · ');result.status=a?.answer===null?'Insufficient evidence':a?'Reported · scene unavailable':'API unavailable';render();$('answer-mode').textContent='ANSWER / TEAM API';$('mode-label').textContent='TEAM API';$('source-pill').textContent='Team API · extracted scene unavailable';}
  const problems=[...response.errors,response.answer?.error].filter(Boolean);
  $('retry-request').hidden=problems.length===0;
  $('contract-status').textContent=problems.length?recoveryMessage(problems.join(' · '))+' Your drawing and question are kept. Retry uses the current question.':response.answer?.answer===null?'The team returned no answer. Review the evidence or edit your question; no mock result was substituted.':'Scene and answer received from the team API.';recordHistory('live',question,path);
 }catch(error){if(id===requestNumber){const message=recoveryMessage(controller.signal.aborted?'Request timed out':error);if(keepScene){$('status').textContent='Previous result - request failed';}else{result={answer:null,status:'API unavailable',scope:'Not determined',evidence:[],explanation:message};render();recordHistory('live',question,path);}$('contract-status').textContent=message+(keepScene?' Previous answer and evidence remain displayed.':'');$('retry-request').hidden=false;}}
 finally{clearTimeout(timer);if(id===requestNumber){apiAbort=null;setBusy(false);}}
}
function openPending(path,question){overviewMode=false;clearSourcePreview();historyStore.setDrawing('drawing:'+path,drawingLabel(path));$('history-view-note').hidden=true;shared=false;apiSelected=true;rawScene=null;rawAnswer=null;scene=emptyDisplayScene();currentPage=0;$('page-select').hidden=true;result={answer:null,status:'Loading',scope:'Not determined',evidence:[],explanation:'Waiting for team API evidence.'};render();svg.querySelector('image').setAttribute('href','');$('drawing-title').textContent=drawingLabel(path);$('mode-label').textContent='TEAM API';$('answer-mode').textContent='ANSWER / PENDING';$('source-pill').textContent='Team API · waiting for source';$('canvas-label').textContent='No source image loaded';svg.setAttribute('aria-label','Source image unavailable. No visual evidence loaded.');document.querySelector('.demo-note').textContent='Questions use the team API. Sample buttons below return to mock mode.';$('source-note').textContent='Source attribution is not supplied by schema v1. Consult the source license before sharing.';$('question').value=question;$('trace-note').textContent='Reported API trace. No mock data or reference answers are sent.';if(sourcePreviewKind(path)){$('canvas-label').textContent='Loading source preview…';showSourcePreview(path);}}
$('check-api').onclick=async()=>{try{const state=await getConnectionStatus();$('connection-status').textContent=`${state.title} · ${state.origin} · ${state.message}`;}catch(error){$('connection-status').textContent=error.message;}};
$('ask-api').onclick=askAPI;
function openEvaluationCase(path,question,displayName){
 if(displayName)drawingNames.set(path,displayName);
 apiAbort?.abort();apiAbort=null;setBusy(false);$('retry-request').hidden=true;
 window.dispatchEvent(new Event('stoa:close-local'));requestNumber++;
 $('api-path').value=path;openPending(path,question);
 result={answer:null,status:'Awaiting question',scope:'Not determined',evidence:[],explanation:'Live drawing selected. Submit a question to load its evidence.'};
 render();$('contract-status').textContent='Live drawing selected. Loading what its reader found…';
 loadOverview(path,requestNumber);
}
/** Show the drawing and what its reader found as soon as it is selected, before any question. */
async function loadOverview(path,id){
 try{
  const s=await getDrawingScene(path);if(id!==requestNumber)return;
  const blank={answer:null,unit:null,confidence:0,tier:'none',evidence:[],extra_entities:[],trace:[],cost_usd:0};
  showShared(s,blank,'live');
  overviewMode=true;result={...result,...summarizeScene(s,currentPage),unit:null,status:`Overview - ${s.builder}`,scope:`Page ${currentPage+1} of ${s.pages.length}`,confidenceText:'Ask a question for an answer'};
  render();$('contract-status').textContent=`Loaded ${s.builder}. Ask a question to highlight its evidence.`;
 }catch(error){
  if(id!==requestNumber)return;
  $('contract-status').textContent=recoveryMessage(error)+' You can still ask a question; the vision reader may answer it.';
 }
}
const query=new URLSearchParams(location.search);if(query.has('path'))openEvaluationCase(query.get('path'),query.get('question')??'');
window.addEventListener('stoa:open-case',e=>openEvaluationCase(e.detail.path,e.detail.question,e.detail.displayName));
window.addEventListener('stoa:select-entity',e=>{if(scene.entities.some(entity=>entity.id===e.detail.id))select(e.detail.id);});

const connectionObserver=new MutationObserver(()=>{$('editor-connection-status').textContent=$('contract-status').textContent;});connectionObserver.observe($('contract-status'),{childList:true,subtree:true,characterData:true});

function recordHistory(origin,question,path=null){
 $('answer-mode').textContent=origin==='mock'?'ANSWER / MOCK':origin==='live'?'ANSWER / TEAM API':'ANSWER / IMPORTED';
 $('history-view-note').hidden=true;
 const key=origin==='mock'?'mock:courtyard':'drawing:'+path;
 historyStore.add(key,origin==='mock'?'Courtyard studio · mock':drawingLabel(path),{
  origin,question,path,payload:{scene,result,rawScene,rawAnswer,shared,apiSelected}
 });
}
window.addEventListener('stoa:restore-answer',event=>{
 const {key,id,reuse}=event.detail,entry=historyStore.get(key,id);if(!entry)return;
 apiAbort?.abort();apiAbort=null;requestNumber++;lastRequest=null;setBusy(false);$('retry-request').hidden=true;
 window.dispatchEvent(new Event('stoa:close-local'));
 if(!$('voice-stop').disabled)$('voice-stop').click();
 if($('voice-dictate').getAttribute('aria-pressed')==='true')$('voice-dictate').click();
 if(entry.path){$('api-path').value=entry.path;$('live-path').value=entry.path;}
 const saved=entry.payload;
 if(saved.shared&&saved.rawScene&&saved.rawAnswer)showShared(saved.rawScene,saved.rawAnswer,entry.origin);
 else if(entry.origin==='mock'){restoreMock();scene=saved.scene;result=saved.result;render();}
 else{
  openPending(entry.path,entry.question??'');scene={...saved.scene,width:sourcePreview?.width??saved.scene.width,height:sourcePreview?.height??saved.scene.height};result=saved.result;rawScene=null;rawAnswer=null;render();
  $('source-pill').textContent='Saved result · no source scene available';
 }
 $('question').value=entry.question??'';
 $('history-view-note').hidden=false;
 $('history-view-note').textContent='Saved '+entry.origin+' result from '+new Date(entry.createdAt).toLocaleString()+'. Original evidence snapshot; no new question was sent. Source images may be loaded again.'+(reuse?' Edit the question, then submit to run it again.':'');
 $('answer-mode').textContent='ANSWER / SAVED '+entry.origin.toUpperCase();
 $('contract-status').textContent='Viewing a saved result. No inference request was made.';
 historyStore.markViewed(key,id);
 if($('panel-editor').hidden)document.querySelector('[data-panel="editor"]').click();$('view-2d').click();
 if(reuse)$('question').focus();else $('history-view-note').focus();
});
if(!query.has('path')&&!location.hash.startsWith('#scenario='))recordHistory('mock',$('question').value);
