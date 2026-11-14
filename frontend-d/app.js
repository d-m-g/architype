import {historyStore} from './history-state.js';
import {requestDrawing,requestJSON,isDrawingPath} from './api-client.js';
import {publishEditor} from './editor-bridge.js';
import {scene as mockScene,scenarios,resolveMockQuestion,zoomView} from './mock-scene.js';
import {adaptScene,adaptAnswer,imageURL} from './contracts.js';
let apiAbort=null, lastRequest=null;
let requestNumber=0,apiSelected=false;
let scene=mockScene,currentPage=0,shared=false,rawScene=null,rawAnswer=null;
const evidenceFor=r=>[...new Set(r.evidence)].map(id=>scene.entities.find(e=>e.id===id)).filter(Boolean);
const $=id=>document.getElementById(id), NS='http://www.w3.org/2000/svg';
let result=scenarios.windows,selected=null,view={x:0,y:0,w:1000,h:760},drag=null;
const svg=$('drawing');
function el(tag,attrs={}){const node=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))node.setAttribute(k,v);return node;}
function syncEditor(){publishEditor({wireScene:shared?rawScene:null,answer:result.raw??result,entities:scene.entities.map(e=>({...e,page:e.page??0})),selectedId:selected,pageIndex:currentPage,viewport:{...view},mode:shared?'shared':apiSelected?'pending':'mock'});}
function updateView(){svg.setAttribute('viewBox',`${view.x} ${view.y} ${view.w} ${view.h}`);$('zoom-label').textContent=`${Math.round(scene.width*100/view.w)}%`;syncEditor();}
function select(id){selected=id;const entity=scene.entities.find(e=>e.id===id);if(shared&&entity.page!==currentPage){setPage(entity.page);render();}selected=id;document.querySelectorAll('[data-entity]').forEach(n=>{const active=n.dataset.entity===id;n.classList.toggle('selected',active);n.setAttribute('aria-pressed',String(active));});$('selection').textContent=`${entity.label} · ${entity.sourceRef} · Page ${shared?entity.page+1:scene.page} · ${entity.method}${entity.uncertain?' · Needs review':''}`;if(!entity.polygon.length){$('selection').textContent+=' · No drawable geometry';syncEditor();return;}const xs=entity.polygon.map(p=>p[0]),ys=entity.polygon.map(p=>p[1]);view.x=(Math.min(...xs)+Math.max(...xs))/2-view.w/2;view.y=(Math.min(...ys)+Math.max(...ys))/2-view.h/2;updateView();}
function render(){$('ask-submit').textContent=(shared||apiSelected)?'Ask live drawing':'Inspect mock evidence';selected=null;const entities=evidenceFor(result);$('answer').replaceChildren();$('answer').classList.toggle('empty',result.answer===null);$('answer').append(document.createTextNode(result.answer??'Not enough evidence'));if(result.unit){const unit=document.createElement('span');unit.textContent=` ${result.unit}`;$('answer').append(unit);}$('status').textContent=result.status;$('status').classList.toggle('warning',result.answer===null||entities.some(e=>e.uncertain));$('scope').textContent=result.scope;$('answer-confidence').textContent=result.confidenceText??'Not calibrated';$('explanation').textContent=result.explanation;$('formula').hidden=!result.formula;$('formula').textContent=result.formula??'';$('evidence-count').textContent=`${entities.length} linked`;$('evidence-list').replaceChildren();$('overlays').replaceChildren();$('selection').textContent='Select evidence to inspect its source reference.';
entities.forEach((entity,i)=>{if(entity.polygon.length&&(!shared||entity.page===currentPage)){const group=el('g',{class:`entity${entity.uncertain?' uncertain':''}`,role:'button',tabindex:'0','aria-label':`${i+1}. ${entity.label}${entity.uncertain?', needs review':''}`,'aria-pressed':'false','data-entity':entity.id});group.append(el('polygon',{points:entity.polygon.map(p=>p.join(',')).join(' ')}));const [x,y]=entity.polygon[0];group.append(el('circle',{cx:x+12,cy:y+10,r:15}));const text=el('text',{x:x+12,y:y+10});text.textContent=i+1;group.append(text);group.addEventListener('click',()=>select(entity.id));group.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();e.stopPropagation();select(entity.id);}});$('overlays').append(group);}
const row=document.createElement('button');row.className=`evidence-row${entity.uncertain?' uncertain':''}`;row.dataset.entity=entity.id;row.setAttribute('aria-pressed','false');const number=document.createElement('span');number.className='evidence-number';number.textContent=i+1;const label=document.createElement('span');label.className='evidence-text';label.textContent=entity.label;const detail=document.createElement('small');detail.textContent=entity.uncertain?'Needs review · uncertain candidate':entity.quantityText??(entity.quantity!==undefined?`${entity.quantity} ${entity.unit} · supplied mock quantity`:entity.sourceRef);label.append(detail);const arrow=document.createElement('span');arrow.className='row-arrow';arrow.setAttribute('aria-hidden','true');arrow.textContent='↗';row.append(number,label,arrow);if(shared)detail.textContent+=` · Page ${entity.page+1}${entity.polygon.length?'':' · No geometry'}`;row.onclick=()=>{if(!$('overlay-toggle').checked){$('overlay-toggle').checked=true;$('overlays').style.display='';}select(entity.id);};$('evidence-list').append(row);});
if(!entities.length){const empty=document.createElement('p');empty.className='muted small';empty.textContent='No supporting entities for this question.';$('evidence-list').append(empty);}
$('trace-list').replaceChildren();for(const line of ((shared||apiSelected)?traceLines():['Match the exact sample question to a local demonstration fixture.',`Scope: ${result.scope}.`,entities.length?`Display ${entities.length} unique fixture entities on page 1.`:'Abstain: no supporting evidence supplied.'])){const li=document.createElement('li');li.textContent=line;$('trace-list').append(li);}document.querySelectorAll('[data-scenario]').forEach(b=>{const active=result===scenarios[b.dataset.scenario];b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active));});view={x:0,y:0,w:scene.width,h:scene.height};updateView();}
$('ask-form').onsubmit=e=>{e.preventDefault();if(shared||apiSelected){askAPI();return;}result=resolveMockQuestion($('question').value);render();recordHistory('mock',$('question').value);};document.querySelectorAll('[data-scenario]').forEach(b=>b.onclick=()=>{restoreMock();result=scenarios[b.dataset.scenario];$('question').value=result.question;render();recordHistory('mock',result.question);});$('overlay-toggle').onchange=e=>{$('overlays').style.display=e.target.checked?'':'none';};function zoom(f){if(shared){const w=Math.max(scene.width*.18,Math.min(scene.width*1.5,view.w/f)),h=w*scene.height/scene.width;view={x:view.x+(view.w-w)/2,y:view.y+(view.h-h)/2,w,h};}else view=zoomView(view,f);updateView();}$('zoom-in').onclick=()=>zoom(1.25);$('zoom-out').onclick=()=>zoom(.8);$('fit').onclick=()=>{view={x:0,y:0,w:scene.width,h:scene.height};updateView();};function pan(direction){const step=view.w*.12;if(direction==='left')view.x-=step;if(direction==='right')view.x+=step;if(direction==='up')view.y-=step;if(direction==='down')view.y+=step;updateView();}document.querySelectorAll('[data-pan]').forEach(b=>b.onclick=()=>pan(b.dataset.pan));svg.addEventListener('keydown',e=>{if(e.target!==svg)return;const directions={ArrowLeft:'left',ArrowRight:'right',ArrowUp:'up',ArrowDown:'down'};if(directions[e.key]){e.preventDefault();pan(directions[e.key]);}if(e.key==='+'||e.key==='='){e.preventDefault();zoom(1.25);}if(e.key==='-'){e.preventDefault();zoom(.8);}});
function svgPoint(e){const p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;return p.matrixTransform(svg.getScreenCTM().inverse());}
svg.addEventListener('pointerdown',e=>{if(e.target.closest('.entity')||e.button!==0)return;drag={point:svgPoint(e),x:view.x,y:view.y,id:e.pointerId};svg.setPointerCapture(e.pointerId);});svg.addEventListener('pointermove',e=>{if(!drag)return;const p=svgPoint(e);view.x+=drag.point.x-p.x;view.y+=drag.point.y-p.y;updateView();});function endDrag(){drag=null;}svg.addEventListener('pointerup',endDrag);svg.addEventListener('pointercancel',endDrag);svg.addEventListener('lostpointercapture',endDrag);render();

// Dashboard deep links select an existing mock scenario; all viewer controls remain unchanged.
function openLinkedScenario(){const key=new URLSearchParams(location.hash.slice(1)).get('scenario');if(Object.hasOwn(scenarios,key)){restoreMock();result=scenarios[key];$('question').value=result.question;render();recordHistory('mock',result.question);}}
window.addEventListener('hashchange',openLinkedScenario);openLinkedScenario();

function traceLines(){return [...(result.trace??[]).map(t=>`${t.step} · ${t.model??'CPU'} · ${t.in_tokens??0}/${t.out_tokens??0} tokens · ${t.gpu_s??0} GPU s · $${t.usd??0} · ${t.ms??0} ms${t.cached?' · cached':''}`),`Query cost: $${result.cost_usd??0} (scene build excluded)`,rawScene?`Scene build: $${(rawScene.trace??[]).reduce((n,t)=>n+(t.usd??0),0)}`:'Scene build cost: unavailable', ...Object.entries(result.assumptions??{}).map(([k,v])=>`Assumption: ${k} = ${v}`)];}
function setPage(index){currentPage=index;const p=scene.pages[index];scene.width=p?.width??1000;scene.height=p?.height??760;const image=svg.querySelector('image');image.setAttribute('href',p?imageURL(p.image):'');image.setAttribute('width',scene.width);image.setAttribute('height',scene.height);$('page-select').value=String(index);}
function showShared(s,a){historyStore.setDrawing('drawing:'+s.path,s.path);$('history-view-note').hidden=true;const next=adaptScene(s,a),answer=adaptAnswer(a,next);scene=next;result=answer;rawScene=s;rawAnswer=a;shared=true;$('page-select').replaceChildren();scene.pages.forEach((p,i)=>{const option=document.createElement('option');option.value=i;option.textContent=`Page ${i+1}`;$('page-select').append(option);});$('page-select').hidden=false;setPage(0);$('drawing-title').textContent=s.path;$('mode-label').textContent='SHARED SCHEMA INPUT';$('answer-mode').textContent='ANSWER / REPORTED';$('source-pill').textContent='Shared schema v1 · reported evidence';$('source-note').textContent='Source attribution is not supplied by schema v1; consult the dataset license before sharing.';$('trace-note').textContent='Reported trace and assumptions. Assumptions are read-only: the API has no override field.';$('canvas-label').textContent='SHARED SCENE · image requires /api/image';svg.setAttribute('aria-label','Source page with evidence overlays; arrow keys pan, plus and minus zoom.');document.querySelector('.demo-note').textContent='Reported answer. Sample buttons below restore the synthetic demonstration.';$('api-path').value=s.path;$('contract-status').textContent=`Loaded ${s.builder}; searched for: ${s.covers.join(', ')}. ${answer.missing.length?'Unresolved evidence: '+answer.missing.join(', '):''}`;render();}
function restoreMock(){historyStore.setDrawing('mock:courtyard','Courtyard studio · mock');$('history-view-note').hidden=true;svg.setAttribute('aria-label','Synthetic ground floor. Use evidence list to select objects; arrow keys pan and plus or minus zoom.');apiAbort?.abort();apiAbort=null;setBusy(false);$('retry-request').hidden=true;rawScene=null;rawAnswer=null;apiSelected=false;requestNumber++;shared=false;scene=mockScene;currentPage=0;$('page-select').hidden=true;const img=svg.querySelector('image');img.setAttribute('href','plan.svg');img.setAttribute('width',1000);img.setAttribute('height',760);$('drawing-title').textContent='Courtyard studio';$('mode-label').textContent='MOCK WORKSPACE';$('answer-mode').textContent='ANSWER / MOCK';$('source-pill').textContent='Synthetic drawing · no live AI';$('source-note').textContent=scene.attribution;$('trace-note').textContent='Local fixture · No model calls · No measured accuracy or cost';$('canvas-label').textContent='DEMO DRAWING · Not for construction';document.querySelector('.demo-note').textContent='Sample questions only. Answers are mock fixtures.';$('contract-status').textContent='Synthetic mock mode.';}
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
 result={answer:null,status:'Cancelled',scope:'Not determined',evidence:[],explanation:'Request cancelled. No answer was accepted.'};
 render();$('contract-status').textContent='Request cancelled. You can retry or ask a different question.';$('retry-request').hidden=!lastRequest;if(lastRequest)recordHistory('live',lastRequest.question,lastRequest.path);
}
$('cancel-request').onclick=cancelRequest;
window.addEventListener('stoa:cancel-request',cancelRequest);
$('retry-request').onclick=()=>{if(lastRequest){$('api-path').value=lastRequest.path;$('question').value=lastRequest.question;askAPI();}};
async function askAPI(){
 window.dispatchEvent(new Event('stoa:close-local'));
 const path=$('api-path').value.trim(),question=$('question').value.trim();
 if(!isDrawingPath(path)||!question){$('contract-status').textContent='Enter a data/ or uploads/ drawing path and a question.';return;}
 lastRequest={path,question};apiAbort?.abort();apiAbort=new AbortController();const controller=apiAbort,id=++requestNumber;const timer=setTimeout(()=>controller.abort(),60000);
 openPending(path,question);setBusy(true);$('contract-status').textContent='Requesting scene and answer from the team API…';
 try{
  const response=await requestDrawing(path,question,controller.signal,message=>{if(id===requestNumber)$('contract-status').textContent=message;});if(id!==requestNumber)return;
  if(response.scene){const answer=response.answer??{answer:null,unit:null,confidence:0,tier:'none',evidence:[],extra_entities:[],trace:[],error:response.errors.join(' · '),cost_usd:0};showShared(response.scene,answer);}
  else{shared=false;apiSelected=true;scene={...mockScene,entities:[]};rawScene=null;rawAnswer=null;const a=response.answer;result=a?adaptAnswer(a,{entities:[]}):{answer:null,status:'API unavailable',scope:'Not determined',evidence:[],explanation:response.errors.join(' · ')};result.explanation=[result.explanation,'No source scene available; visual evidence cannot be verified.'].filter(Boolean).join(' · ');result.status=a?.answer===null?'Insufficient evidence':a?'Reported · source unavailable':'API unavailable';render();$('answer-mode').textContent='ANSWER / TEAM API';$('mode-label').textContent='TEAM API';$('source-pill').textContent='Team API · source scene unavailable';}
  $('retry-request').hidden=response.errors.length===0;
  $('contract-status').textContent=response.errors.join(' · ')||'Scene and answer received from the team API.';recordHistory('live',question,path);
 }catch(error){if(id===requestNumber){const message=controller.signal.aborted?'Request timed out. Retry when the team API is ready.':error.message;result={answer:null,status:'API unavailable',scope:'Not determined',evidence:[],explanation:message};render();$('contract-status').textContent=message;$('retry-request').hidden=false;recordHistory('live',question,path);}}
 finally{clearTimeout(timer);if(id===requestNumber){apiAbort=null;setBusy(false);}}
}
function openPending(path,question){historyStore.setDrawing('drawing:'+path,path);$('history-view-note').hidden=true;shared=false;apiSelected=true;rawScene=null;rawAnswer=null;scene={...mockScene,entities:[]};currentPage=0;$('page-select').hidden=true;result={answer:null,status:'Loading',scope:'Not determined',evidence:[],explanation:'Waiting for team API evidence.'};render();svg.querySelector('image').setAttribute('href','');$('drawing-title').textContent=path;$('mode-label').textContent='TEAM API';$('answer-mode').textContent='ANSWER / PENDING';$('source-pill').textContent='Team API · waiting for source';$('canvas-label').textContent='No source image loaded';svg.setAttribute('aria-label','Source image unavailable. No visual evidence loaded.');document.querySelector('.demo-note').textContent='Questions use the team API. Sample buttons below return to mock mode.';$('source-note').textContent='Source attribution is not supplied by schema v1. Consult the source license before sharing.';$('question').value=question;$('trace-note').textContent='Reported API trace. No mock data or reference answers are sent.';}
$('check-api').onclick=async()=>{try{const state=await requestJSON('/__frontend/api-status');$('connection-status').textContent=`${state.title} · ${state.origin} · ${state.message}`;}catch(error){$('connection-status').textContent=error.message;}};
$('ask-api').onclick=askAPI;
function openEvaluationCase(path,question){
 apiAbort?.abort();apiAbort=null;setBusy(false);$('retry-request').hidden=true;
 window.dispatchEvent(new Event('stoa:close-local'));requestNumber++;
 $('api-path').value=path;openPending(path,question);
 result={answer:null,status:'Awaiting question',scope:'Not determined',evidence:[],explanation:'Live drawing selected. Submit a question to load its evidence.'};
 render();$('contract-status').textContent='Live drawing selected. Ask a free-form question to retrieve current evidence; reference answers are never sent.';
}
const query=new URLSearchParams(location.search);if(query.has('path'))openEvaluationCase(query.get('path'),query.get('question')??'');
window.addEventListener('stoa:open-case',e=>openEvaluationCase(e.detail.path,e.detail.question));
window.addEventListener('stoa:select-entity',e=>{if(scene.entities.some(entity=>entity.id===e.detail.id))select(e.detail.id);});

const connectionObserver=new MutationObserver(()=>{$('editor-connection-status').textContent=$('contract-status').textContent;});connectionObserver.observe($('contract-status'),{childList:true,subtree:true,characterData:true});

function recordHistory(origin,question,path=null){
 $('answer-mode').textContent=origin==='mock'?'ANSWER / MOCK':origin==='live'?'ANSWER / TEAM API':'ANSWER / IMPORTED';
 $('history-view-note').hidden=true;
 const key=origin==='mock'?'mock:courtyard':'drawing:'+path;
 historyStore.add(key,origin==='mock'?'Courtyard studio · mock':path,{
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
 if(saved.shared&&saved.rawScene&&saved.rawAnswer)showShared(saved.rawScene,saved.rawAnswer);
 else if(entry.origin==='mock'){restoreMock();scene=saved.scene;result=saved.result;render();}
 else{
  openPending(entry.path,entry.question??'');scene=saved.scene;result=saved.result;rawScene=null;rawAnswer=null;render();
  $('source-pill').textContent='Saved result · no source scene available';
 }
 $('question').value=entry.question??'';
 $('history-view-note').hidden=false;
 $('history-view-note').textContent='Saved '+entry.origin+' result from '+new Date(entry.createdAt).toLocaleString()+'. Original evidence snapshot; no new question was sent. Source images may be loaded again.'+(reuse?' Edit the question, then submit to run it again.':'');
 $('answer-mode').textContent='ANSWER / SAVED '+entry.origin.toUpperCase();
 $('contract-status').textContent='Viewing a saved result. No inference request was made.';
 historyStore.markViewed(key,id);
 document.querySelector('[data-panel="editor"]').click();$('view-2d').click();
 if(reuse)$('question').focus();else $('history-view-note').focus();
});
if(!query.has('path')&&!location.hash.startsWith('#scenario='))recordHistory('mock',$('question').value);
