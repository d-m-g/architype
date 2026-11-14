/** Correct what the reader found: remove a false detection (and the ones that look like it), confirm a doubtful
 * one, resize a box by its corners, move a room's outline corner by corner, draw a missed one.
 * The answer is recalculated by the server's executor on the corrected scene, with no model call. */
import {$,el,state,setScene,showAnswer,answerFromScene,track,onInspect,svgPoint,select,showOnDrawing} from './main.js';
import {requestJSON,getDrawingScene,recoveryMessage} from './api-client.js';
import {subscribeEditor} from './editor-bridge.js';
import {imageURL} from './contracts.js';
import {measure,reshaped,nearestEdge,withCorner,withoutCorner} from './correction-state.js';

const ADD=[['door','door',null],['window','window',null],['toilet','fixture','toilet'],['sink','fixture','sink'],['bathtub','fixture','bathtub'],['shower','fixture','shower']];
const svg=$('drawing'),layer=$('edit-layer');
let undo=[],drawing=false,drawDrag=null,suppressDrawClick=false,resize=null;  // resize: the corner drag in progress
let alike=null;  // the selected item's look-alikes: {ids, toggle(id)}; each can be kept or removed with it
/** The tools float over the top of the drawing; the info panel keeps clear of however tall their message makes them. */
const lift=()=>$('viewport-2d').style?.setProperty('--edit-bar',$('edit-bar').hidden?'0px':$('edit-bar').offsetHeight+10+'px');
const hint=text=>{$('edit-hint').textContent=text;lift();};
$('edit-kind').replaceChildren(...ADD.map(([name],i)=>new Option(name,String(i))));

/** [x0,y0,x1,y1] around a polygon, and back to the 4 corners clockwise from the top left. */
const bounds=points=>{const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);return [Math.min(...xs),Math.min(...ys),Math.max(...xs),Math.max(...ys)];};
const corners=([x0,y0,x1,y1])=>[[x0,y0],[x1,y0],[x1,y1],[x0,y1]];
const pointsAttr=points=>points.map(p=>p.join(',')).join(' ');
const markOf=id=>layer.querySelector(`.edit-mark[data-entity="${CSS.escape(id)}"]`);
/** What can be resized: an upright box the reader or the user drew. Measured outlines keep the shape their numbers came from. */
function resizable(entity){
 if(!entity||entity.polygon.length!==4||Object.keys(entity.qty??{}).length||!state.raw.entities.some(e=>e.id===entity.id))return false;
 const [x0,y0,x1,y1]=bounds(entity.polygon);
 return entity.polygon.every(([x,y])=>(x===x0||x===x1)&&(y===y0||y===y1));
}
/** A room is corrected corner by corner instead, box or not, and its quantities follow the outline (correction-state.js). */
const outlined=entity=>entity?.type==='room'&&entity.polygon.length>=3&&state.raw.entities.some(e=>e.id===entity.id);
const geomOf=id=>state.raw.entities.find(e=>e.id===id).geom;
function reshape(id,geom,done){
 const entity=state.scene.entities.find(e=>e.id===id),page=state.raw.pages[entity.page];
 if(!geom||measure(geom,page).area<1){paint();hint(geom?'That outline would enclose nothing, so it was left as it was.':'An outline needs at least 3 corners.');return;}
 // What the reader measured is kept on the room, so its card can show the numbers before and after.
 change(state.raw.entities.map(e=>e.id===id?{...reshaped(e,geom,page),attrs:{...e.attrs,corrected_from:e.attrs?.corrected_from??entity.quantityText??''}}:e),`${done} ${entity.label}`,id);
}
/** A click on the selected room's outline, within 8 screen pixels of an edge, puts a corner there. */
function addCorner(entity,event){
 const point=svgPoint(event),{width,height}=state.raw.pages[entity.page],edge=nearestEdge(entity.polygon,[point.x,point.y]);
 if(edge.distance>8*(Number(svg.style?.getPropertyValue('--u'))||1))return false;
 reshape(entity.id,withCorner(geomOf(entity.id),edge.index,[edge.point[0]/width,edge.point[1]/height]),'Added a corner to');
 return true;
}

function paint(){
 layer.replaceChildren();
 if(!state.editing||!state.scene)return;
 const area=e=>{const [x0,y0,x1,y1]=bounds(e.polygon);return (x1-x0)*(y1-y0);};
 const items=state.scene.entities.filter(e=>e.page===state.page&&e.polygon.length>=3&&!['apartment','level'].includes(e.type)).sort((a,b)=>area(b)-area(a));
 // The same type colours and low-score dashes as outside the mode, so the drawing does not change its meaning on entry.
 for(const entity of items)layer.append(el('g',{class:`edit-mark kind-${entity.type}${entity.id.startsWith('user-')?' added':''}${entity.uncertain?' review':''}`,
  role:'button',tabindex:'0','aria-label':entity.label,'data-entity':entity.id,
  // A look-alike of the selected item is ticked or unticked where it is; anything else is selected.
  onclick:event=>{if(drawing)return;if(alike?.ids.includes(entity.id))alike.toggle(entity.id);else if(entity.id!==state.selected||!outlined(entity)||!addCorner(entity,event))select(entity.id);},onkeydown:event=>{if(event.key==='Enter'){event.preventDefault();select(entity.id);}}},
  ...['halo-dark','halo-light','region-shape'].map(kind=>el('polygon',{class:kind,points:pointsAttr(entity.polygon)}))));
 paintSelection();
}
/** What follows the selection: a handle on every corner of a room's outline, or corner handles on a box that can be
 * resized; the last selection's look-alike marks go. */
let shown=null;
function paintSelection(){
 shown=state.selected;
 for(const node of layer.querySelectorAll('.edit-handle'))node.remove();
 alike=null;
 for(const node of layer.querySelectorAll('.alike,.hot'))node.classList.remove('alike','hot');
 const entity=state.editing&&!drawing?state.scene?.entities.find(e=>e.id===state.selected):null;
 if(outlined(entity)){layer.append(...entity.polygon.map(([x,y],i)=>el('circle',{class:'edit-handle',r:5,'data-vertex':i,style:`--x:${x};--y:${y}`})));return;}
 if(!resizable(entity))return;
 layer.append(...corners(bounds(entity.polygon)).map(([x,y],i)=>el('circle',{class:'edit-handle',r:6,'data-corner':i,style:`--x:${x};--y:${y}`})));
}
subscribeEditor(()=>{if(state.selected!==shown&&!resize)paintSelection();});
function setEditing(on){
 if(on&&!$('viewport-3d').hidden)$('view-2d').click();  // corrections are made on the plan
 state.editing=on;stopDrawing();
 $('edit-bar').hidden=!on;$('edit-toggle').setAttribute('aria-pressed',String(on));
 document.querySelector('.legend').classList.toggle('in-edit',on);lift();
 select(null);
 if(state.raw)setScene(state.raw,{keepView:true});  // main redraws its marks (hidden while editing)
 paint();
 if(on)hint('Click an item to check, fix or remove it.');
}
/** Keep the info panel off the item it describes: it sits bottom-left, and moves to the top when the item is under it. */
function place(id,box){
 box.classList.remove('up');
 const mark=document.querySelector(`#edit-layer [data-entity="${CSS.escape(id)}"],#overlays [data-entity="${CSS.escape(id)}"]`)?.getBoundingClientRect(),own=box.getBoundingClientRect();
 box.classList.toggle('up',Boolean(mark&&mark.left<own.right&&mark.right>own.left&&mark.top<own.bottom&&mark.bottom>own.top));
}
function stopDrawing(){
 const drag=drawDrag;drawDrag=null;drawing=false;
 drag?.preview.remove();
 if(drag&&svg.hasPointerCapture(drag.pointerId))svg.releasePointerCapture(drag.pointerId);
 svg.classList.remove('drawing-box');$('edit-draw').setAttribute('aria-pressed','false');
}

/** keep: the entity to leave selected afterwards, so a resized box can be adjusted again. */
async function commit(entities,done,keep=null){
 select(null);
 setScene({...state.raw,entities},{keepView:true});
 state.edited=true;$('edit-undo').disabled=!undo.length;paint();select(keep);
 if(!state.rawAnswer||!state.question){hint(`${done}. Your next question uses the corrected drawing.`);return;}
 const before=state.rawAnswer.answer;
 hint(`${done}. Recalculating the answer`);
 try{
  const started=performance.now(),answer=await track(answerFromScene(state.question));
  state.corrected=`Recalculated from your corrections, with no model call. It was ${before??'unanswered'} before.`;
  const selected=state.selected;
  showAnswer(answer,state.question,performance.now()-started);paint();select(selected);
  hint(`${done}. The answer is now ${answer.answer??'unavailable'}.`);
 }catch(error){
  hint(`${done}, but the answer could not be recalculated: ${/\b501\b/.test(error.message)?'this question needs the agent, which reads the original drawing':recoveryMessage(error)}`);
 }
}
const post=(url,body)=>requestJSON(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
/** How the corrected drawing differs from what the reader found, as labels for training the detector: drawn boxes,
 * resized ones and confirmed ones are right, removed ones are what the detector got wrong. */
function labelsOf(before,after){
 const was=new Map(before.map(e=>[e.id,e])),now=new Set(after.map(e=>e.id)),labels=[];
 // "other" items were already set aside by the server as rejected symbols: they teach nothing new.
 const label=(action,e,more={})=>{if(e.geom?.length>=3&&e.type!=='other')labels.push({action,image:state.raw.pages[e.page??0].image,entity:e.id,type:e.type,
  ...(e.subtype?{subtype:e.subtype}:{}),box:bounds(e.geom),conf:e.conf??null,...more});};
 for(const e of before)if(!now.has(e.id))label('removed',e);
 for(const e of after){
  const old=was.get(e.id);
  if(!old)label('added',e);
  else if(JSON.stringify(old.geom)!==JSON.stringify(e.geom))label('resized',e,{was:bounds(old.geom),conf:old.conf??null});
  else if(old.conf!==e.conf)label('confirmed',e,{conf:old.conf??null});
 }
 return labels;
}
/** forget: what Undo also has to take back on the server (a remembered symbol). */
function change(entities,done,keep=null,forget=null){
 undo.push({entities:state.raw.entities,answer:state.rawAnswer,corrected:state.corrected,ms:state.ms,edited:state.edited,forget});
 return commit(entities,done,keep);
}
// Corrections become training labels only when the user says so: nothing is recorded while they try things out.
$('edit-record').onclick=async()=>{
 const button=$('edit-record');
 button.disabled=true;hint('Recording your corrections');
 try{
  // The server holds the drawing as its reader left it; the corrections are in this browser.
  const labels=labelsOf((await track(getDrawingScene(state.path))).entities,state.raw.entities);
  await track(post('/api/labels',{drawing:state.path,labels}));
  const counts=['removed','added','resized','confirmed'].map(action=>[labels.filter(l=>l.action===action).length,action]).filter(([n])=>n);
  hint(labels.length?`Recorded for training: ${counts.map(([n,action])=>`${n} ${action}`).join(', ')}. Recording again replaces this.`
   :'Nothing differs from what the reader found, so this drawing has no training labels now.');
 }catch(error){hint(`Could not record the corrections: ${recoveryMessage(error)}`);}
 finally{button.disabled=false;}
};
/** Tell the server this symbol is not what the detector took it for, so it is set aside on drawings uploaded from
 * now on. Returns the function that makes the server forget it again. */
function remember(entity){
 const me=state.raw.entities.find(e=>e.id===entity.id),kind={type:me.type,...(me.subtype?{subtype:me.subtype}:{})};
 const stored=post('/api/reject',{image:state.raw.pages[me.page??0].image,box:bounds(me.geom),...kind}).then(reply=>reply.id,()=>null);
 return ()=>stored.then(id=>id&&requestJSON('/api/reject?'+new URLSearchParams({...kind,id}),{method:'DELETE'})).catch(()=>{});
}
/** The other items of the same kind on the page that show the same symbol as this one, by the server's pixel comparison. */
async function lookAlikes(entity){
 const all=state.raw.entities,me=all.find(e=>e.id===entity.id),page=me.page??0;
 const others=Object.fromEntries(all.filter(e=>e.id!==me.id&&e.type===me.type&&(e.subtype??null)===(me.subtype??null)&&(e.page??0)===page&&e.geom?.length>=3).map(e=>[e.id,bounds(e.geom)]));
 if(!Object.keys(others).length)return [];
 return (await requestJSON('/api/alike',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({image:state.raw.pages[page].image,box:bounds(me.geom),others})})).ids;
}
$('edit-undo').onclick=()=>{
 const last=undo.pop();if(!last)return;
 select(null);setScene({...state.raw,entities:last.entities},{keepView:true});
 state.edited=last.edited;state.corrected=last.corrected;$('edit-undo').disabled=!undo.length;
 if(last.answer)showAnswer(last.answer,state.question,last.ms);
 last.forget?.();
 paint();hint(last.forget?'Undone, and the symbol is forgotten.':'Undone.');
};
// In every mode: a room whose outline the user moved says so, with the reader's numbers beside the ones that followed.
onInspect((entity,box)=>{
 const was=entity.attrs?.corrected_from;
 if(was!==undefined)box.append(el('p',{class:'edit-note'},`Outline corrected by user${was?`: was ${was}, now ${entity.quantityText}`:''}.`));
});
onInspect((entity,box)=>{
 if(!state.editing||!state.raw.entities.some(e=>e.id===entity.id))return;
 if(outlined(entity))box.append(el('p',{class:'muted small'},'Drag a corner to move it. Click the outline to add a corner, Alt-click a corner to remove it.'));
 const actions=el('div',{class:'actions'},
  el('button',{class:'btn',type:'button',onclick:()=>change(state.raw.entities.filter(e=>e.id!==entity.id),`Removed ${entity.label}`)},'Remove'),
  entity.uncertain?el('button',{class:'btn',type:'button',onclick:()=>change(state.raw.entities.map(e=>e.id===entity.id?{...e,conf:1}:e),`Confirmed ${entity.label}`)},'It is right'):null);
 box.append(actions);
 // Look-alikes are for symbols drawn the same way all over a sheet: doors, windows, fixtures. Rooms are each their own shape.
 if(entity.polygon.length<3||!['door','window','fixture'].includes(entity.type))return;
 // One wrong detection is rarely alone: show the others that look like it, each as a small crop of the drawing, and
 // offer to remove them together. Some may be right after all, so each can be kept: click its crop or its outline.
 const wait=el('p',{class:'muted small alike-wait'},el('span',{class:'spinner','aria-hidden':'true'}),'Looking for others like it');
 box.append(wait);
 lookAlikes(entity).finally(()=>wait.remove()).then(ids=>{
  if(!ids.length||!actions.isConnected)return;  // nothing alike, or the selection moved on
  const chosen=new Set(ids);
  const note=el('p',{class:'muted small'}),crops=el('div',{class:'alike-list',role:'group','aria-label':'Items that look like it'},...ids.map(id=>crop(id,()=>alike.toggle(id))));
  const remove=el('button',{class:'btn',type:'button',onclick:()=>{
   const all=chosen.size===ids.length,gone=new Set([entity.id,...chosen]);
   // The symbol is remembered for later drawings only when every look-alike was wrong: if some were right, it is no proof.
   change(state.raw.entities.filter(e=>!gone.has(e.id)),`Removed ${entity.label} and ${chosen.size} that look like it; `+
    (all?'the symbol is remembered for later drawings':`${ids.length-chosen.size} kept`),null,all?remember(entity):null);
  }});
  const sync=()=>{
   for(const id of ids)markOf(id)?.classList.toggle('alike',chosen.has(id));
   for(const node of crops.children){const on=chosen.has(node.dataset.alike);node.classList.toggle('kept',!on);node.lastChild.checked=on;}
   remove.textContent=`Remove this and ${chosen.size} like it`;remove.disabled=!chosen.size;
   note.textContent=`${ids.length} look like it; ${chosen.size} will be removed with it. Click a crop to see it on the drawing; untick the ones to keep.`;
  };
  alike={ids,toggle:id=>{chosen.has(id)?chosen.delete(id):chosen.add(id);sync();}};
  actions.append(remove);box.append(note,crops);sync();place(entity.id,box);
 }).catch(()=>{});  // no look-alikes offered (a PDF page, the server unreachable); Remove still works
});
onInspect((entity,box)=>place(entity.id,box));  // in every mode, once the panel has its content
/** One look-alike: a small crop of the drawing that, clicked, moves the view to the item and lights up its outline
 * (the selection stays), and a tick box: ticked means it is removed with the selected item. */
function crop(id,toggle){
 const entity=state.scene.entities.find(e=>e.id===id),page=state.raw.pages[entity.page],[x0,y0,x1,y1]=bounds(entity.polygon),pad=Math.max(x1-x0,y1-y0)*.25;
 const go=el('button',{class:'alike-go',type:'button','aria-label':`Show this ${entity.label} on the drawing`,title:'Show on the drawing',onclick:()=>{
  for(const node of layer.querySelectorAll('.hot'))node.classList.remove('hot');
  showOnDrawing(id);markOf(id)?.classList.add('hot');
 }});
 // Numbers and an encoded URL only, so this markup is safe to build as text; el() makes no <svg> or <image>.
 go.innerHTML=`<svg viewBox="${x0-pad} ${y0-pad} ${x1-x0+2*pad} ${y1-y0+2*pad}" aria-hidden="true"><image href="${imageURL(page.image)}" width="${page.width}" height="${page.height}"/></svg>`;
 return el('span',{class:'alike-crop','data-alike':id},go,
  el('input',{type:'checkbox',checked:true,'aria-label':`Remove this ${entity.label} too`,title:'Ticked: removed with the selected one',onchange:toggle}));
}

// Resize: drag a corner handle of the selected box; the opposite corner stays where it is.
// A room's outline has a handle on every corner instead: dragging one moves that corner alone, Alt-click removes it.
svg.addEventListener('pointerdown',event=>{
 const handle=event.target.closest?.('.edit-handle'),entity=state.scene?.entities.find(e=>e.id===state.selected);
 if(!handle||!entity||event.button!==0)return;
 event.preventDefault();event.stopImmediatePropagation();  // not a pan
 if(handle.dataset.vertex!==undefined){
  const vertex=Number(handle.dataset.vertex);
  suppressDrawClick=true;  // the click that ends this press must not land on the outline as "add a corner"
  if(event.altKey){reshape(entity.id,withoutCorner(geomOf(entity.id),vertex),'Removed a corner of');return;}
  handle.setPointerCapture(event.pointerId);
  resize={id:entity.id,vertex,from:entity.polygon,points:null};
  return;
 }
 handle.setPointerCapture(event.pointerId);
 const [x0,y0,x1,y1]=bounds(entity.polygon),corner=Number(handle.dataset.corner);
 resize={id:entity.id,label:entity.label,fixed:[corner===0||corner===3?x1:x0,corner<2?y1:y0],box:null};
},true);
svg.addEventListener('pointermove',event=>{
 if(!resize)return;
 const point=svgPoint(event),{width,height}=state.raw.pages[state.page];
 const x=Math.min(Math.max(point.x,0),width),y=Math.min(Math.max(point.y,0),height);
 let points;
 if(resize.from)points=resize.points=resize.from.map((p,i)=>i===resize.vertex?[x,y]:p);
 else{
  const [fx,fy]=resize.fixed;
  resize.box=[Math.min(fx,x),Math.min(fy,y),Math.max(fx,x),Math.max(fy,y)];
  points=corners(resize.box);
 }
 // Move the outline and its handles in place: rebuilding them would drop the handle the pointer is holding.
 for(const shape of markOf(resize.id)?.querySelectorAll('polygon')??[])shape.setAttribute('points',pointsAttr(points));
 layer.querySelectorAll('.edit-handle').forEach((node,i)=>node.setAttribute('style',`--x:${points[i][0]};--y:${points[i][1]}`));
});
function endResize(event){
 if(!resize)return;
 const {id,label,box,points,vertex}=resize,{width,height}=state.raw.pages[state.page];resize=null;
 if(!box&&!points){paintSelection();return;}  // a click on the handle, no drag
 if(points){  // a room's corner: the others keep the exact numbers the reader gave them
  if(event.type!=='pointerup'){paint();hint('Move cancelled.');return;}
  reshape(id,geomOf(id).map((p,i)=>i===vertex?[points[vertex][0]/width,points[vertex][1]/height]:p),'Moved a corner of');
  return;
 }
 const [x0,y0,x1,y1]=[box[0]/width,box[1]/height,box[2]/width,box[3]/height];
 if(event.type!=='pointerup'||x1-x0<.002||y1-y0<.002){paint();hint(event.type==='pointerup'?'That box is too small, so it was left as it was.':'Resize cancelled.');return;}
 change(state.raw.entities.map(e=>e.id===id?{...e,geom:corners([x0,y0,x1,y1])}:e),`Resized ${label}`,id);
}
svg.addEventListener('pointerup',endResize);svg.addEventListener('pointercancel',endResize);

$('edit-toggle').onclick=()=>setEditing(!state.editing);
$('edit-done').onclick=()=>setEditing(false);
$('edit-draw').onclick=()=>{
 if(drawing){stopDrawing();paint();hint('Drawing cancelled.');return;}
 select(null);  // Correct mode is always on the plan, so there is no view to switch
 drawing=true;svg.classList.add('drawing-box');$('edit-draw').setAttribute('aria-pressed','true');
 hint('Press and drag around the item, then release to add it. Esc cancels.');
};
// Capture on the stable SVG, so dragging outside the page or across existing marks still finishes reliably.
svg.addEventListener('pointerdown',event=>{
 if(!drawing){suppressDrawClick=false;return;}
 if(event.button!==0)return;
 event.preventDefault();event.stopImmediatePropagation();
 if(drawDrag||event.isPrimary===false)return;
 const point=svgPoint(event),{width,height}=state.raw.pages[state.page];
 if(point.x<0||point.y<0||point.x>width||point.y>height){hint('Start the box inside the page.');return;}
 const preview=el('g',{class:'edit-mark draw-preview','aria-hidden':'true'},
  ...['halo-dark','halo-light','region-shape'].map(kind=>el('polygon',{class:kind,points:pointsAttr(corners([point.x,point.y,point.x,point.y]))})));
 drawDrag={pointerId:event.pointerId,start:point,clientX:event.clientX,clientY:event.clientY,page:state.page,raw:state.raw,
  kind:Number($('edit-kind').value),preview};
 suppressDrawClick=true;layer.append(preview);svg.setPointerCapture(event.pointerId);
 hint('Release to add the box. Esc cancels.');
},true);
function drawPoint(event){
 const point=svgPoint(event),{width,height}=state.raw.pages[state.page];
 return {x:Math.min(Math.max(point.x,0),width),y:Math.min(Math.max(point.y,0),height)};
}
svg.addEventListener('pointermove',event=>{
 if(!drawDrag||event.pointerId!==drawDrag.pointerId)return;
 event.preventDefault();event.stopImmediatePropagation();
 const point=drawPoint(event),start=drawDrag.start;
 const points=pointsAttr(corners([Math.min(start.x,point.x),Math.min(start.y,point.y),Math.max(start.x,point.x),Math.max(start.y,point.y)]));
 for(const shape of drawDrag.preview.querySelectorAll('polygon'))shape.setAttribute('points',points);
},true);
function endDraw(event){
 if(!drawDrag||event.pointerId!==drawDrag.pointerId)return;
 event.preventDefault();event.stopImmediatePropagation();
 const drag=drawDrag;
 if(event.type!=='pointerup'||state.page!==drag.page||state.raw!==drag.raw){stopDrawing();paint();hint('Drawing cancelled.');return;}
 const point=drawPoint(event),{width,height}=state.raw.pages[state.page],start=drag.start;
 const [name,type,subtype]=ADD[drag.kind];
 const x0=Math.min(start.x,point.x)/width,x1=Math.max(start.x,point.x)/width,y0=Math.min(start.y,point.y)/height,y1=Math.max(start.y,point.y)/height;
 stopDrawing();
 if(Math.hypot(event.clientX-drag.clientX,event.clientY-drag.clientY)<4||x1-x0<.002||y1-y0<.002){paint();hint('That box is too small. Press Draw box and drag around the item.');return;}
 const entity={id:'user-'+Date.now().toString(36),type,subtype,page:drag.page,geom:[[x0,y0],[x1,y0],[x1,y1],[x0,y1]],conf:1,ref:'added by you'};
 change([...state.raw.entities,entity],`Added a ${name}`);
}
svg.addEventListener('pointerup',endDraw,true);
svg.addEventListener('pointercancel',endDraw,true);
svg.addEventListener('lostpointercapture',endDraw,true);
svg.addEventListener('click',event=>{if(drawing||suppressDrawClick){event.preventDefault();event.stopImmediatePropagation();suppressDrawClick=false;}},true);
// Esc steps back one level at a time: out of drawing a box, then off the selected item, then out of the mode.
window.addEventListener('keydown',event=>{
 if(event.key!=='Escape'||!state.editing||event.defaultPrevented||event.target?.closest?.('input,textarea,select'))return;
 if(drawing){stopDrawing();paint();hint('Drawing cancelled.');}
 else if(state.selected)select(null);
 else{setEditing(false);$('edit-toggle').focus();}
});
$('page-select').addEventListener('change',()=>{if(drawing)stopDrawing();paint();});
for(const id of ['view-3d','open-another'])$(id).addEventListener('click',()=>{if(state.editing)setEditing(false);});
window.addEventListener('stoa:drawing-reset',()=>{undo=[];state.edited=false;stopDrawing();$('edit-bar').hidden=true;document.querySelector('.legend').classList.remove('in-edit');$('edit-toggle').setAttribute('aria-pressed','false');$('edit-undo').disabled=true;layer.replaceChildren();});
