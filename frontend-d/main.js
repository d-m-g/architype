/** The Ask view: drawing viewer, question flow, answer card, evidence and trace. */
import {createSourcePreviewLoader,sourcePreviewKind,loadPDFPreview} from './local-file.js';
import {historyStore} from './history-state.js';
import {requestJSON,getDrawingScene,recoveryMessage,LIVE_REQUEST_TIMEOUT_MS} from './api-client.js';
import {publishEditor,subscribeEditor,selectEditorEntity} from './editor-bridge.js';
import {zoomAt} from './mock-scene.js';
import {adaptScene,adaptAnswer,adaptAnswerEvidence,imageURL} from './contracts.js';

export const $=id=>document.getElementById(id);
const SVG='http://www.w3.org/2000/svg';
/** Build an element: el('button',{class:'btn',onclick:fn},'Text', child). */
export function el(tag,attrs={},...children){
 const node=['g','polygon','circle','text','title'].includes(tag)?document.createElementNS(SVG,tag):document.createElement(tag);
 for(const [key,value] of Object.entries(attrs)){
  if(value===null||value===undefined||value===false)continue;
  if(key.startsWith('on'))node.addEventListener(key.slice(2),value);else node.setAttribute(key,value===true?'':value);
 }
 node.append(...children.filter(child=>child!==null&&child!==undefined&&child!==false));
 return node;
}

/** One drawing at a time. raw* are the API's objects; scene and result are their display forms. */
export const state={path:null,name:'',raw:null,scene:null,page:0,rawAnswer:null,result:null,question:'',ms:null,
 preview:null,layers:new Set(),selected:null,readError:null,editing:false,edited:false,corrected:null};

// ───────── progress: every request shows the top bar; long ones also get a clock ─────────
let busy=0;
export function track(promise){
 busy++;document.body.classList.add('is-busy');
 return promise.finally(()=>{if(!--busy)document.body.classList.remove('is-busy');});
}
function clock(node){
 const start=performance.now(),tick=()=>{node.textContent=((performance.now()-start)/1000).toFixed(1)+' s';};
 tick();const timer=setInterval(tick,100);
 return ()=>{clearInterval(timer);return performance.now()-start;};
}
let stopStageClock=null;
function stageStatus(text){
 stopStageClock?.();stopStageClock=null;
 $('stage-status').hidden=$('scan').hidden=!text;
 if(text){$('stage-status-text').textContent=text;stopStageClock=clock($('stage-status-time'));}
}

// ───────── words ─────────
const KINDS={room:['Room','Rooms'],door:['Door','Doors'],window:['Window','Windows'],fixture:['Fixture','Fixtures'],apartment:['Apartment','Apartments'],
 level:['Floor','Floors'],wall:['Wall','Walls'],slab:['Slab','Slabs'],column:['Column','Columns'],row:['Schedule row','Schedule rows'],other:['Other item','Other items']};
const UNIT={count:'',m2:'m²',ft2:'ft²',m:'m',m3:'m³'};
const money=usd=>usd?'$'+(usd<0.0001?usd.toFixed(6):usd.toFixed(4)):'$0';
const seconds=ms=>ms<1000?Math.round(ms)+' ms':(ms/1000).toFixed(1)+' s';
const plural=(n,[one,many])=>`${n} ${(n===1?one:many).toLowerCase()}`;
const clean=text=>{const s=String(text??'').replace(/^lane [A-D]: /i,'').trim();return s?s[0].toUpperCase()+s.slice(1):s;};
const notAPlan=raw=>raw?.builder.includes(':not-a-floor-plan:')?raw.builder.split(':not-a-floor-plan:')[1]:null;
function readerName(raw){
 if(raw.builder.startsWith('vision'))return 'Read by the vision model';
 return raw.tier==='parse'?'Read from the CAD geometry':'Read by the detector';
}
export function formatValue(value,unit){
 if(typeof value!=='number')return [String(value),''];
 const text=unit==='count'||Number.isInteger(value)?String(value):value.toLocaleString('en',{maximumFractionDigits:2});
 return [text,UNIT[unit]??''];
}

// ───────── viewer ─────────
const svg=$('drawing'),image=$('page-image');
let view={x:0,y:0,w:1000,h:760};
const pageSize=()=>state.scene?{w:state.scene.width,h:state.scene.height}:{w:state.preview?.width??1000,h:state.preview?.height??760};
function publish(){
 publishEditor({wireScene:state.raw,answer:state.rawAnswer,entities:state.scene?.entities??[],selectedId:state.selected,pageIndex:state.page,
  viewport:{...view},sourcePreview:null,mode:state.raw?'shared':'pending'});
}
function updateView(sync=true){
 svg.setAttribute('viewBox',`${view.x} ${view.y} ${view.w} ${view.h}`);
 const box=svg.getBoundingClientRect();
 // --u is drawing units per screen pixel, so number badges keep one size at every zoom.
 if(box.width)svg.style.setProperty('--u',Math.max(view.w/box.width,view.h/box.height));
 svg.classList.toggle('dense',view.w>pageSize().w*.6&&$('overlays').querySelectorAll('.badge').length>12);
 $('zoom-label').textContent=Math.round(pageSize().w*MARGIN*100/view.w)+'%';
 if(sync)publish();
}
const MARGIN=1.06;  // "Fit" leaves a little desk around the paper
export function fit(){const {w,h}=pageSize(),m=(MARGIN-1)/2;view={x:-w*m,y:-h*m,w:w*MARGIN,h:h*MARGIN};updateView();}
/** Point the camera at what the answer counted, with room around it; a whole-page answer just fits the page. */
function frameEvidence(){
 const points=evidenceIds().map(entityById).filter(e=>e?.page===state.page).flatMap(e=>e.polygon);
 if(!points.length)return;
 const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]),{w,h}=pageSize();
 const width=Math.max((Math.max(...xs)-Math.min(...xs))*1.5,(Math.max(...ys)-Math.min(...ys))*1.5*w/h,w*.25);
 if(width>=w){fit();return;}
 view={w:width,h:width*h/w,x:(Math.min(...xs)+Math.max(...xs))/2-width/2,y:(Math.min(...ys)+Math.max(...ys))/2-width*h/w/2};updateView();
}
function zoom(factor,anchor={x:view.x+view.w/2,y:view.y+view.h/2}){view=zoomAt(view,factor,anchor,pageSize().w);updateView();}
function pan(dx,dy){view.x+=dx*view.w*.12;view.y+=dy*view.h*.12;updateView();}
export function svgPoint(event){const p=svg.createSVGPoint();p.x=event.clientX;p.y=event.clientY;return p.matrixTransform(svg.getScreenCTM().inverse());}
const locked=()=>svg.classList.contains('drawing-box');

$('zoom-in').onclick=()=>zoom(1.25);$('zoom-out').onclick=()=>zoom(.8);$('fit').onclick=fit;
svg.addEventListener('keydown',event=>{
 if(event.target!==svg)return;
 const move={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[event.key];
 if(move){event.preventDefault();pan(...move);}
 else if(event.key==='+'||event.key==='='){event.preventDefault();zoom(1.25);}
 else if(event.key==='-'){event.preventDefault();zoom(.8);}
 else if(event.key==='0'){event.preventDefault();fit();}
});
// Paint once per frame; pointer movement must not rebuild the page.
let frame=0,syncTimer;
function scheduleView(){if(!frame)frame=requestAnimationFrame(()=>{frame=0;updateView(false);});clearTimeout(syncTimer);syncTimer=setTimeout(publish,150);}
const pointers=new Map();let gesture=null,dragged=false;
function startGesture(){
 const points=[...pointers.values()],a=points[0],b=points[1]??a;
 if(!a){gesture=null;svg.classList.remove('is-panning');return;}
 const matrix=svg.getScreenCTM()?.inverse();if(!matrix)return;
 const mid=svg.createSVGPoint();mid.x=(a.x+b.x)/2;mid.y=(a.y+b.y)/2;
 gesture={view:{...view},matrix,anchor:mid.matrixTransform(matrix),distance:Math.hypot(a.x-b.x,a.y-b.y),x:mid.x,y:mid.y};
}
svg.addEventListener('pointerdown',event=>{
 if(event.button!==0||locked())return;
 if(!pointers.size)dragged=false;
 pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});event.target.setPointerCapture(event.pointerId);startGesture();
});
svg.addEventListener('pointermove',event=>{
 if(!pointers.has(event.pointerId)||!gesture)return;
 pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});
 const points=[...pointers.values()],a=points[0],b=points[1]??a,mid=svg.createSVGPoint();mid.x=(a.x+b.x)/2;mid.y=(a.y+b.y)/2;
 if(Math.hypot(mid.x-gesture.x,mid.y-gesture.y)>4||points.length>1){dragged=true;svg.classList.add('is-panning');}
 if(!dragged)return;
 const now=mid.matrixTransform(gesture.matrix),distance=Math.hypot(a.x-b.x,a.y-b.y);
 view=zoomAt(gesture.view,gesture.distance>0&&distance>0?distance/gesture.distance:1,gesture.anchor,pageSize().w);
 const ratio=view.w/gesture.view.w;view.x+=(gesture.anchor.x-now.x)*ratio;view.y+=(gesture.anchor.y-now.y)*ratio;scheduleView();
});
function endPointer(event){pointers.delete(event.pointerId);startGesture();if(!pointers.size)publish();}
svg.addEventListener('pointerup',endPointer);svg.addEventListener('pointercancel',endPointer);svg.addEventListener('lostpointercapture',endPointer);
svg.addEventListener('click',event=>{
 if(dragged){event.preventDefault();event.stopImmediatePropagation();dragged=false;}
 else if(event.target===svg||event.target===image)select(null);
},true);
svg.addEventListener('wheel',event=>{
 if(locked())return;event.preventDefault();
 const amount=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?svg.clientHeight:1);
 view=zoomAt(view,Math.exp(-Math.max(-150,Math.min(150,amount))*.004),svgPoint(event),pageSize().w);scheduleView();
},{passive:false});
svg.addEventListener('dblclick',event=>{if(locked())return;event.preventDefault();zoom(event.shiftKey?.5:2,svgPoint(event));});
new ResizeObserver(()=>updateView(false)).observe(svg);

function showImage(url,width,height){
 image.setAttribute('href',url??'');image.setAttribute('width',width);image.setAttribute('height',height);
}
const previewLoader=createSourcePreviewLoader(path=>new Promise((resolve,reject)=>{
 const probe=new Image();probe.onload=()=>resolve({width:probe.naturalWidth,height:probe.naturalHeight});
 probe.onerror=()=>reject(new Error('The drawing image could not be loaded.'));probe.src=imageURL(path);
}),loadPDFPreview);

// ───────── marks on the drawing ─────────
const kindOf=entity=>entity.type;
/** Every box's colour follows its own confidence: hue 0 (red) at 0%, 60 (amber) at 50%, 120 (green) at 100%. */
const hue=entity=>Math.round((entity.conf??1)*120);
const area=e=>Math.abs(e.polygon.reduce((sum,p,i,points)=>{const q=points[(i+1)%points.length];return sum+p[0]*q[1]-q[0]*p[1];},0));
const evidenceIds=()=>state.result?[...new Set(state.result.evidence)]:[];
export function entityById(id){return state.scene?.entities.find(e=>e.id===id);}
function hover(id,on){for(const node of document.querySelectorAll('[data-entity]'))if(node.dataset.entity===id)node.classList.toggle('hot',on);}
function mark(entity,className,label){
 const group=el('g',{class:className+(entity.uncertain?' review':''),style:`--h:${hue(entity)}`,role:'button',tabindex:'0','aria-label':label,'data-entity':entity.id,
  onclick:()=>select(entity.id),onmouseenter:()=>hover(entity.id,true),onmouseleave:()=>hover(entity.id,false),
  onkeydown:event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();select(entity.id);}}},
  el('polygon',{points:entity.polygon.map(p=>p.join(',')).join(' ')}));
 group.append(el('title',{},label));
 return group;
}
function drawMarks(){
 const layer=$('overlays');layer.replaceChildren();
 if(!state.scene||state.editing)return;
 const cited=evidenceIds(),onPage=state.scene.entities.filter(e=>e.page===state.page&&e.polygon.length>=3).sort((a,b)=>area(b)-area(a));
 for(const entity of onPage)if(!cited.includes(entity.id)&&state.layers.has(kindOf(entity)))
  layer.append(mark(entity,`mark other kind-${entity.type}`,`${entity.label}, ${Math.round(entity.conf*100)}% sure, not part of the answer`));
 const badges=[];
 for(const entity of onPage){
  const n=cited.indexOf(entity.id);if(n<0)continue;
  layer.append(mark(entity,'mark evidence',`${n+1}. ${entity.label}, ${Math.round(entity.conf*100)}% sure`));
  const xs=entity.polygon.map(p=>p[0]),ys=entity.polygon.map(p=>p[1]);
  badges.push(el('g',{class:`badge${entity.uncertain?' review':''}`,'data-entity':entity.id,style:`--x:${Math.min(...xs)};--y:${Math.min(...ys)}`},
   el('circle',{r:9,cx:-5,cy:-5}),el('text',{x:-5,y:-5,dy:'.35em'},String(n+1))));
 }
 layer.append(...badges);
 markSelection();updateView(false);
}
function markSelection(){
 for(const node of document.querySelectorAll('[data-entity]')){
  const on=node.dataset.entity===state.selected;node.classList.toggle('selected',on);
  if(node.hasAttribute('aria-pressed'))node.setAttribute('aria-pressed',String(on));
 }
}
const inspectors=[];
/** Other modules add controls to the inspector: fn(entity, node). */
export function onInspect(fn){inspectors.push(fn);}
export function select(id,{zoomTo=false}={}){
 const entity=id?entityById(id):null;
 state.selected=entity?.id??null;
 if(entity&&entity.page!==state.page)setPage(entity.page);
 markSelection();
 const box=$('inspector');box.hidden=!entity;box.replaceChildren();
 if(!entity){publish();return;}
 const n=evidenceIds().indexOf(entity.id);
 box.append(
  el('button',{class:'btn icon close',type:'button','aria-label':'Close',onclick:()=>select(null)},'×'),
  el('strong',{},entity.label),
  el('p',{},[KINDS[entity.type]?.[0]??entity.type,entity.quantityText,entity.context].filter(Boolean).join(', ')),
  el('p',{class:'muted small'},[n>=0?`Counted in the answer as number ${n+1}`:'Not part of this answer',
   entity.conf<1?`${Math.round(entity.conf*100)}% sure it is real`:null,entity.ref?`source ${entity.ref}`:null].filter(Boolean).join(' · ')));
 for(const fn of inspectors)fn(entity,box);
 const row=[...$('evidence-list').children].find(node=>node.dataset.entity===entity.id);
 row?.scrollIntoView({block:'nearest',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
 if(zoomTo&&entity.polygon.length){
  const xs=entity.polygon.map(p=>p[0]),ys=entity.polygon.map(p=>p[1]),{w,h}=pageSize();
  const width=Math.min(w,Math.max((Math.max(...xs)-Math.min(...xs))*4,(Math.max(...ys)-Math.min(...ys))*4*w/h,w*.3));
  view={w:width,h:width*h/w,x:(Math.min(...xs)+Math.max(...xs))/2-width/2,y:(Math.min(...ys)+Math.max(...ys))/2-width*h/w/2};
  updateView(false);
 }
 publish();
}
window.addEventListener('stoa:select-entity',event=>select(event.detail.id));

// ───────── pages ─────────
function setPage(index){
 state.page=index;const page=state.scene.pages[index];
 state.scene.width=page.width;state.scene.height=page.height;
 // A scene made only from answer evidence has no page image of its own: keep the preview on screen.
 showImage(state.raw?imageURL(page.image):state.preview?.displayURL??imageURL(state.preview.image),page.width,page.height);
 $('page-select').value=String(index);
 for(const button of $('page-rail').children)button.setAttribute('aria-pressed',String(Number(button.dataset.page)===index));
}
$('page-select').onchange=()=>{setPage(Number($('page-select').value));fit();renderDrawingSide();drawMarks();};
function buildPages(){
 const pages=state.scene.pages;
 $('page-select').replaceChildren(...pages.map((_,i)=>new Option(`Page ${i+1}`,String(i))));
 $('page-rail').hidden=pages.length<2;
 $('page-rail').replaceChildren(...pages.map((page,i)=>el('button',{type:'button','data-page':i,'aria-label':`Page ${i+1}`,'aria-pressed':'false',
  onclick:()=>{$('page-select').value=String(i);$('page-select').onchange();}},el('img',{src:imageURL(page.image),alt:'',loading:'lazy'}),el('span',{},String(i+1)))));
}

// ───────── right side: what the reader found ─────────
function steps(list){
 $('reading').hidden=!list;
 if(list)$('reading-steps').replaceChildren(...list.map(step=>el('li',{class:step.state},
  step.state==='active'?el('span',{class:'spinner','aria-hidden':'true'}):el('i',{'aria-hidden':'true'}),el('span',{},step.text))));
}
function counts(){
 const tally=new Map();
 for(const entity of state.scene.entities)if(entity.page===state.page)tally.set(kindOf(entity),(tally.get(kindOf(entity))??0)+1);
 return [...tally].sort((a,b)=>Object.keys(KINDS).indexOf(a[0])-Object.keys(KINDS).indexOf(b[0]));
}
function renderDrawingSide(){
 $('drawing-title').textContent=state.name;
 const raw=state.raw,kind=notAPlan(raw);
 $('layers').replaceChildren();
 $('edit-toggle').disabled=!raw||Boolean(kind);
 if(!raw){$('drawing-meta').textContent=state.readError??'';return;}
 if(kind){$('drawing-meta').textContent=`This looks like a ${kind}, not a floor plan, so there is nothing to count on it.`;return;}
 const cost=(raw.trace??[]).reduce((sum,step)=>sum+(step.usd??0),0);
 $('drawing-meta').textContent=`${readerName(raw)}${cost?`, ${money(cost)} once`:', no model cost'}${raw.pages.length>1?`. Page ${state.page+1} of ${raw.pages.length}`:''}.`;
 $('layers').append(...counts().map(([kind,n])=>el('button',{type:'button',class:'chip','aria-pressed':String(state.layers.has(kind)),title:'Show or hide on the drawing',
  onclick:()=>{state.layers.has(kind)?state.layers.delete(kind):state.layers.add(kind);renderDrawingSide();drawMarks();}},
  el('span',{},KINDS[kind]?.[1]??kind),el('b',{},String(n)))));
}
function suggest(){
 const raw=state.raw,list=[];
 if(raw&&!notAPlan(raw)){
  const has=key=>raw.covers.some(c=>c===key||c.startsWith(key+':')),rooms=raw.entities.filter(e=>e.type==='room');
  const measured=rooms.some(e=>e.qty?.m2>0);
  if(has('door'))list.push('How many doors are there?');
  if(measured)list.push('What is the total indoor floor area?');
  if(measured&&rooms.some(e=>e.subtype==='bathroom'))list.push('How many 60 × 60 cm tiles do I need for the bathroom floors, with 10% waste?');
  else if(has('fixture'))list.push('How many toilets and sinks are there?');
  if(measured)list.push('Which room is the largest?');
  else if(has('window'))list.push('How many windows are there?');
 }
 $('suggestions').hidden=!list.length||Boolean(state.result)||asking;
 $('suggestion-list').replaceChildren(...list.map(text=>el('button',{type:'button',class:'suggestion',onclick:()=>ask(text)},text)));
}

// ───────── right side: the answer ─────────
const TIER={parse:'Read from the CAD geometry',detect:'Counted from detections',llm:'Vision model and agent',none:'No route answered'};
const STEP={scene:'Read the drawing',room_types:'Named the room types',plan:'Understood the question',execute:'Counted and measured',escalate:'Agent reasoning',
 look:'Vision model looked at the drawing','tool:list_entities':'Agent listed what is on the drawing','tool:run_plan':'Agent ran a calculation','tool:look':'Agent asked for a closer look'};
function renderAnswer(){
 const result=state.result,raw=state.rawAnswer;
 $('answer-card').hidden=$('working').hidden===false||!result;
 $('evidence').hidden=$('trace').hidden=$('answer-card').hidden;
 suggest();renderHistory();
 if($('answer-card').hidden)return;
 $('asked').textContent=state.question;
 const failed=raw.answer===null;
 $('answer-card').classList.toggle('failed',failed);
 const [value,unit]=failed?['No answer','']:formatValue(raw.answer,raw.unit);
 $('answer').replaceChildren(value,unit?el('span',{class:'unit'},unit):'');
 // Errors from the executor name entities by id; show the names a person can find on the drawing.
 $('explanation').textContent=failed?clean(raw.error).replace(/[\w:.-]+/g,word=>entityById(word)?.label??word)||'The drawing does not hold enough evidence for this question.':raw.interpretation??'';
 $('explanation').hidden=!$('explanation').textContent;
 $('formula').hidden=!raw.formula;$('formula').textContent=raw.formula??'';
 $('edit-note').hidden=!state.corrected;$('edit-note').textContent=state.corrected??'';
 $('retry-request').hidden=!failed;

 const confidence=Math.round(raw.confidence*100),level=raw.confidence>=.8?'high':raw.confidence>=.5?'medium':'low';
 const models=[...new Set((raw.trace??[]).filter(step=>step.model&&!step.cached&&step.usd).map(step=>step.model))];
 $('facts').replaceChildren(...[
  failed?null:['Confidence',el('span',{class:'meter'},el('i',{style:`width:${confidence}%;--c:${confidence}`})),`${confidence}%, ${level}`],
  ['Route',null,TIER[raw.tier]],
  ['Models',null,models.join(', ')||'none needed'],
  ['Cost',null,money(raw.cost_usd)],
  state.ms===null?null:['Time',null,seconds(state.ms)],
 ].filter(Boolean).map(([term,graphic,text])=>el('div',{},el('dt',{},term),el('dd',{},graphic,text))));

 const assumptions=Object.entries(raw.assumptions??{});
 $('assumptions').hidden=!assumptions.length;
 $('assumptions').replaceChildren(el('h3',{},'Assumptions'),...assumptions.map(([key,value])=>el('span',{},`${key.replace(/_/g,' ')}: `,el('b',{},String(value)))));

 const cited=evidenceIds().map(id=>entityById(id)).filter(Boolean),unsure=cited.filter(e=>e.uncertain).length;
 $('evidence').hidden=!cited.length;
 $('evidence-count').textContent=`${cited.length}${unsure?`, ${unsure} to check`:''}`;
 $('evidence-list').replaceChildren(...cited.map((entity,i)=>el('button',{type:'button',class:`evidence-row${entity.uncertain?' review':''}`,style:`--h:${hue(entity)}`,'data-entity':entity.id,'aria-pressed':'false',
  onclick:()=>select(entity.id,{zoomTo:true}),onmouseenter:()=>hover(entity.id,true),onmouseleave:()=>hover(entity.id,false)},
  el('span',{class:'n'},String(i+1)),
  el('span',{class:'what'},entity.label,el('small',{},[entity.quantityText,entity.context,entity.conf<1?`${Math.round(entity.conf*100)}% sure${entity.uncertain?', check it':''}`:null,
   state.scene.pages.length>1?`page ${entity.page+1}`:null,entity.polygon.length?null:'not drawn'].filter(Boolean).join(' · '))))));

 const trace=raw.trace??[];
 $('trace').hidden=!trace.length;$('trace').open=raw.tier==='llm';
 $('trace-summary').textContent=`${plural(trace.length,['step','steps'])}, ${money(raw.cost_usd)}`;
 $('trace-list').replaceChildren(...trace.map(step=>{
  const skipped=step.step.startsWith('escalate:unavailable');
  const tokens=step.in_tokens||step.out_tokens?`${step.in_tokens.toLocaleString('en')} in, ${step.out_tokens.toLocaleString('en')} out`:null;
  return el('li',{class:skipped?'skipped':step.model?'model':''},
   el('span',{class:'step-name'},skipped?`${step.step.split(' ')[1]} did not respond, skipped`:STEP[step.step]??step.step),
   el('span',{class:'step-meta'},[step.model,tokens,step.gpu_s?`${step.gpu_s.toFixed(1)} GPU s`:null,step.cached?'cached':null,step.ms?seconds(step.ms):null].filter(Boolean).join(' · ')),
   el('span',{class:'step-cost'},step.usd?money(step.usd):'free'));
 }));
 const reading=(state.raw?.trace??[]).reduce((sum,step)=>sum+(step.usd??0),0);
 $('trace-note').textContent=reading?`Reading this drawing cost ${money(reading)} once; every later question reuses it.`:'';
}
function renderHistory(){
 const entries=state.path?historyStore.list('drawing:'+state.path).filter(entry=>entry.id!==shownHistory):[];
 $('history').hidden=!entries.length;
 $('history-list').replaceChildren(...entries.map(entry=>{
  const answer=entry.payload.rawAnswer,[value,unit]=answer.answer===null?['No answer','']:formatValue(answer.answer,answer.unit);
  return el('li',{},el('button',{type:'button',onclick:()=>{shownHistory=entry.id;state.corrected=null;showAnswer(answer,entry.question,entry.payload.ms);}},
   el('span',{},entry.question),el('b',{},`${value} ${unit}`.trim())));
 }));
}
let shownHistory=null;
/** Put an Answer on screen. Marks on the drawing switch to its evidence. */
export function showAnswer(raw,question,ms=null){
 try{
  if(state.raw)state.scene={...adaptScene(state.raw,raw),width:state.scene.width,height:state.scene.height};
  else if(state.preview?.width){
   // No reader for this drawing: draw the answer's own evidence over the preview, whose size we know.
   const page={image:state.preview.image,width:state.preview.width,height:state.preview.height};
   state.scene=adaptScene({schema_version:1,path:state.path,builder:'answer',tier:'detect',covers:[],pages:[page],entities:[]},raw);
  }else state.scene={pages:[],entities:adaptAnswerEvidence(raw),...pageSize()};
  state.result=adaptAnswer(raw,state.scene);state.rawAnswer=raw;
 }catch(error){
  state.rawAnswer={answer:null,unit:null,confidence:0,tier:'none',evidence:[],trace:[],error:error.message,cost_usd:0};
  state.result={evidence:[]};
 }
 state.question=question;state.ms=ms;state.selected=null;$('inspector').hidden=true;
 if(!state.editing)state.layers.clear();
 renderDrawingSide();renderAnswer();drawMarks();publish();
}

// ───────── saved results: the last drawings and their answers survive a reload ─────────
const SAVED='blueprints-results';
export function savedResults(){try{return JSON.parse(localStorage.getItem(SAVED))??[];}catch{return [];}}
export function clearSavedResults(){try{localStorage.removeItem(SAVED);}catch{}}
function saveResult(question,rawAnswer,ms){
 const all=savedResults(),mine=all.find(d=>d.path===state.path)?.entries??[];
 const list=[{path:state.path,name:state.name,at:Date.now(),entries:[...mine,{question,rawAnswer,ms}].slice(-20)},...all.filter(d=>d.path!==state.path)].slice(0,8);
 // ponytail: capped by count (8 drawings, 20 answers each), not bytes; when storage is full the oldest drawing goes.
 for(;;){try{localStorage.setItem(SAVED,JSON.stringify(list));return;}catch{if(list.length<=1)return;list.pop();}}
}

// ───────── opening a drawing ─────────
let generation=0,reading=null;
function resetDrawing(){
 abortAsk();generation++;previewLoader.cancel();
 Object.assign(state,{raw:null,scene:null,page:0,rawAnswer:null,result:null,question:'',ms:null,preview:null,selected:null,readError:null,editing:false,corrected:null});
 state.layers.clear();shownHistory=null;
 $('overlays').replaceChildren();$('inspector').hidden=true;$('page-rail').hidden=true;
 $('working').hidden=true;
 window.dispatchEvent(new Event('stoa:drawing-reset'));
}
export function showStart(){
 resetDrawing();state.path=null;stageStatus(null);
 $('workspace').hidden=true;$('start').hidden=false;$('open-another').hidden=true;
 history.replaceState(null,'',location.pathname+'#ask');showPanel('ask');
}
$('open-another').onclick=showStart;
/** Open a dataset or uploaded drawing: show it at once, then outline what its reader finds. */
export async function openDrawing(path,{name,question='',preview=null,restore=false}={}){
 resetDrawing();const id=generation,key='drawing:'+path,saved=savedResults().find(d=>d.path===path);
 name??=saved?.name??path.split('/').pop();
 Object.assign(state,{path,name,preview});
 historyStore.setDrawing(key,name);
 // Answers saved in an earlier visit come back as this drawing's earlier questions.
 if(saved&&!historyStore.list(key).length)for(const entry of saved.entries)historyStore.add(key,name,{question:entry.question,payload:{rawAnswer:entry.rawAnswer,ms:entry.ms}});
 $('start').hidden=true;$('workspace').hidden=false;$('open-another').hidden=false;
 showPanel('ask');setView('2d');
 $('question').value=question;autosize();
 showImage(preview?.displayURL??null,preview?.width??1000,preview?.height??760);fit();
 renderDrawingSide();renderAnswer();
 if(!preview&&sourcePreviewKind(path))previewLoader.select(path,found=>{
  if(id!==generation||state.raw)return;
  state.preview=found;showImage(found.displayURL??imageURL(found.image),found.width,found.height);fit();
 });
 $('reading-retry').hidden=true;
 steps([{state:'done',text:'Drawing opened'},{state:'active',text:'Finding rooms, doors, windows and fixtures'}]);
 stageStatus('Reading the drawing');
 try{
  const raw=await (reading=track(getDrawingScene(path)));
  if(id!==generation)return;
  setScene(raw);steps(null);
 }catch(error){
  if(id!==generation)return;
  const text=String(error?.message??error),noReader=/\b501\b/.test(text);
  // No fast reader is not a failure: the question goes to the vision model and the agent instead.
  state.readError=noReader?'No fast reader for this kind of drawing. Ask a question and the vision model will read it.':recoveryMessage(error);
  steps(noReader?null:/\b404\b/.test(text)?[{state:'failed',text:'This drawing is not on the server'}]
   :[{state:'done',text:'Drawing opened'},{state:'failed',text:'Could not read what is on it'}]);
  $('reading-retry').hidden=noReader;
  renderDrawingSide();
 }finally{if(id===generation)stageStatus(null);}
 if(id!==generation)return;
 const last=restore?historyStore.list(key)[0]:null;
 if(last){shownHistory=last.id;showAnswer(last.payload.rawAnswer,last.question,last.payload.ms);frameEvidence();}
 $('question').focus({preventScroll:true});
}
$('reading-retry').onclick=()=>openDrawing(state.path,{name:state.name,question:$('question').value,preview:state.preview});
/** Take a (new or corrected) scene from the reader. keepView is for corrections on the same page. */
export function setScene(raw,{keepView=false}={}){
 state.raw=raw;state.readError=null;
 const page=keepView?state.page:0;
 state.scene=adaptScene(raw,state.rawAnswer??{});
 if(!keepView){
  state.layers=new Set(['room','door','window','fixture']);
  buildPages();setPage(page);fit();
 }else{state.scene.width=raw.pages[page].width;state.scene.height=raw.pages[page].height;}
 if(state.rawAnswer)state.result=adaptAnswer(state.rawAnswer,state.scene);
 renderDrawingSide();renderAnswer();drawMarks();publish();
}

// ───────── asking ─────────
let askAbort=null,asking=false,stopWorkClock=null,hintTimers=[];
function setAsking(on,question=''){
 asking=on;
 $('working').hidden=!on;$('ask-submit').hidden=on;$('cancel-request').hidden=!on;
 $('ask-form').setAttribute('aria-busy',String(on));$('overlays').classList.toggle('stale',on);
 stopWorkClock?.();stopWorkClock=null;hintTimers.forEach(clearTimeout);hintTimers=[];
 if(on){
  $('working-question').textContent=question;stopWorkClock=clock($('working-time'));
  const say=text=>{$('working-text').textContent=text;};
  say('Understanding the question');
  // One request covers every step, so these lines follow the pipeline's usual timing rather than live events.
  hintTimers=[setTimeout(say,1500,'Finding the evidence on the drawing'),
   setTimeout(say,7000,'A hard one: the reasoning agent is working through it'),
   setTimeout(say,30000,'Still reasoning. The agent may take up to two minutes')];
 }
}
function abortAsk(){if(askAbort){askAbort.abort();askAbort=null;}if(asking)setAsking(false);}
$('cancel-request').onclick=()=>{abortAsk();renderAnswer();$('question').value=state.lastAsked??'';autosize();$('question').focus();};
$('retry-request').onclick=()=>ask(state.question);
const plans=new Map();
/** Answer on the scene as it is in this browser (after corrections): the question's plan, run by the server's executor. */
export async function answerFromScene(question,signal){
 if(!plans.has(question))plans.set(question,await requestJSON('/api/plan?'+new URLSearchParams({question}),{signal}));
 return requestJSON('/api/recompute',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scene:state.raw,plan:plans.get(question)}),signal});
}
export async function ask(text){
 const question=text.trim(),path=state.path;
 if(!question||!path)return;
 abortAsk();
 const controller=askAbort=new AbortController(),timer=setTimeout(()=>controller.abort(new Error('timeout')),LIVE_REQUEST_TIMEOUT_MS);
 state.lastAsked=question;$('question').value='';autosize();select(null);
 setAsking(true,question);renderAnswer();
 $('working').scrollIntoView({block:'nearest'});
 const started=performance.now();
 let answer,note=null,reached=true;
 try{
  // A question typed while the drawing is still being read waits for the reader, so the drawing is read once.
  await reading?.catch(()=>{});
  if(askAbort!==controller)return;
  if(state.edited){
   // The server only knows the drawing as it read it, so a corrected drawing is answered from this browser's copy.
   answer=await track(answerFromScene(question,controller.signal)).catch(()=>null);
   note=answer?'Answered on your corrected drawing.':'This question needs the agent, which reads the original drawing, so your corrections are not in this answer.';
  }
  answer??=await track(requestJSON('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path,question}),signal:controller.signal}));
  // The first question on an unread drawing may have produced its scene; pick it up for the outlines.
  if(!state.raw&&!controller.signal.aborted)try{setScene(await getDrawingScene(path,controller.signal));}catch{}
 }catch(error){
  if(askAbort!==controller)return;  // stopped by the user or replaced by a newer question
  reached=false;
  answer={answer:null,unit:null,confidence:0,tier:'none',evidence:[],extra_entities:[],trace:[],cost_usd:0,
   error:recoveryMessage(controller.signal.aborted?'Request timed out':error)};
 }finally{clearTimeout(timer);}
 if(askAbort!==controller)return;
 askAbort=null;setAsking(false);state.corrected=note;
 shownHistory=historyStore.add('drawing:'+path,state.name,{question,payload:{rawAnswer:answer,ms:performance.now()-started}});
 if(reached)saveResult(question,answer,performance.now()-started);  // a failed connection is not a result worth keeping
 showAnswer(answer,question,performance.now()-started);
 frameEvidence();
 $('thread').scrollTo({top:$('answer-card').offsetTop-12,behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
}
function autosize(){const box=$('question');box.style.height='auto';box.style.overflowY=box.scrollHeight>158?'auto':'hidden';box.style.height=Math.min(box.scrollHeight+2,160)+'px';}
autosize();
$('question').addEventListener('input',autosize);
$('question').addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('ask-form').requestSubmit();}});
$('ask-form').addEventListener('submit',event=>{event.preventDefault();if(!asking)ask($('question').value);});

// ───────── shell: tabs, theme, 3D ─────────
let panel='ask',mode='2d',renderer=null,loading3D=false;
export function showPanel(name){
 panel=name==='evaluation'?'evaluation':'ask';
 for(const section of document.querySelectorAll('[data-workspace-panel]'))section.hidden=section.dataset.workspacePanel!==panel;
 for(const link of document.querySelectorAll('.tabs [data-panel]'))link.toggleAttribute('aria-current',link.dataset.panel===panel);
 document.title=panel==='ask'?'Blueprints Talk':'Evaluation · Blueprints Talk';
 renderer?.setVisible?.(mode==='3d'&&panel==='ask');
 if(panel==='ask')updateView(false);
}
function setView(next){
 mode=next;
 $('viewport-2d').hidden=mode!=='2d';$('viewport-3d').hidden=mode!=='3d';
 $('view-2d').setAttribute('aria-pressed',String(mode==='2d'));$('view-3d').setAttribute('aria-pressed',String(mode==='3d'));
 document.querySelector('.zoom').hidden=mode!=='2d';
 $('view-status').textContent=mode==='2d'?'Drag to pan, scroll to zoom':'Drag to orbit. Heights are illustrative, not measured';
 if(mode==='3d'&&!renderer&&!loading3D){
  loading3D=true;
  track(import('./room-renderer.js')).then(({RoomRenderer})=>{
   renderer=new RoomRenderer();renderer.mount($('three-mount'),{selectEntity:selectEditorEntity});
   subscribeEditor(snapshot=>renderer.update(snapshot));$('three-placeholder').hidden=true;renderer.setVisible(mode==='3d'&&panel==='ask');
  }).catch(()=>{
   renderer=null;$('three-mount').replaceChildren();
   $('three-placeholder').querySelector('.spinner').hidden=true;
   $('three-placeholder').querySelector('h3').textContent='3D is not available in this browser';
   $('three-placeholder').querySelector('p').textContent='The plan view shows the same evidence.';
  }).finally(()=>{loading3D=false;});
 }
 renderer?.setVisible?.(mode==='3d'&&panel==='ask');
 if(mode==='2d')updateView(false);
}
$('view-2d').onclick=()=>setView('2d');$('view-3d').onclick=()=>setView('3d');$('back-to-2d').onclick=()=>setView('2d');
window.addEventListener('hashchange',()=>showPanel(location.hash.slice(1)));
function syncTheme(){
 const dark=document.documentElement.dataset.theme==='dark';
 $('theme-toggle').setAttribute('aria-pressed',String(dark));$('theme-toggle').title=dark?'Switch to light':'Switch to dark';
}
$('theme-toggle').onclick=()=>window.stoaTheme.setPreference(document.documentElement.dataset.theme==='dark'?'light':'dark');
window.addEventListener('stoa:theme-change',syncTheme);syncTheme();

// Evaluation rows and shared links open a drawing with its question filled in, ready to ask.
window.addEventListener('stoa:open-case',event=>openDrawing(event.detail.path,event.detail));
const query=new URLSearchParams(location.search);
showPanel(location.hash.slice(1));
if(query.has('path'))openDrawing(query.get('path'),{question:query.get('question')??''});
