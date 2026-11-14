/** Correct what the reader found: remove a false detection (and the ones that look like it), confirm a doubtful
 * one, resize a box by its corners, draw a missed one.
 * The answer is recalculated by the server's executor on the corrected scene, with no model call. */
import {$,el,state,setScene,showAnswer,answerFromScene,track,onInspect,svgPoint,select} from './main.js';
import {requestJSON,recoveryMessage} from './api-client.js';
import {subscribeEditor} from './editor-bridge.js';

const ADD=[['door','door',null],['window','window',null],['toilet','fixture','toilet'],['sink','fixture','sink'],['bathtub','fixture','bathtub'],['shower','fixture','shower']];
const svg=$('drawing'),layer=$('edit-layer');
let undo=[],drawing=false,drawDrag=null,suppressDrawClick=false,resize=null;  // resize: the corner drag in progress
const hint=text=>{$('edit-hint').textContent=text;};
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

function paint(){
 layer.replaceChildren();
 if(!state.editing||!state.scene)return;
 const area=e=>{const [x0,y0,x1,y1]=bounds(e.polygon);return (x1-x0)*(y1-y0);};
 const items=state.scene.entities.filter(e=>e.page===state.page&&e.polygon.length>=3&&!['apartment','level'].includes(e.type)).sort((a,b)=>area(b)-area(a));
 for(const entity of items)layer.append(el('g',{class:`edit-mark${entity.id.startsWith('user-')?' added':''}`,role:'button',tabindex:'0','aria-label':entity.label,'data-entity':entity.id,
  onclick:()=>{if(!drawing)select(entity.id);},onkeydown:event=>{if(event.key==='Enter'){event.preventDefault();select(entity.id);}}},
  ...['halo-dark','halo-light','region-shape'].map(kind=>el('polygon',{class:kind,points:pointsAttr(entity.polygon)}))));
 paintSelection();
}
/** What follows the selection: corner handles on a box that can be resized; the last selection's look-alike marks go. */
let shown=null;
function paintSelection(){
 shown=state.selected;
 for(const node of layer.querySelectorAll('.edit-handle'))node.remove();
 for(const node of layer.querySelectorAll('.alike'))node.classList.remove('alike');
 const entity=state.editing&&!drawing?state.scene?.entities.find(e=>e.id===state.selected):null;
 if(!resizable(entity))return;
 layer.append(...corners(bounds(entity.polygon)).map(([x,y],i)=>el('circle',{class:'edit-handle',r:6,'data-corner':i,style:`--x:${x};--y:${y}`})));
}
subscribeEditor(()=>{if(state.selected!==shown&&!resize)paintSelection();});
function setEditing(on){
 state.editing=on;stopDrawing();
 $('edit-bar').hidden=!on;$('edit-toggle').setAttribute('aria-pressed',String(on));
 select(null);
 if(state.raw)setScene(state.raw,{keepView:true});  // main redraws its marks (hidden while editing)
 paint();
 if(on)hint('Click an outline to remove, confirm or resize it, or draw one the reader missed.');
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
/** What a correction says about each item it touched, as labels for training the detector: drawn boxes, resized
 * ones and confirmed ones are right, removed ones are what the detector got wrong. */
function labelsOf(before,after){
 const was=new Map(before.map(e=>[e.id,e])),now=new Set(after.map(e=>e.id)),labels=[];
 const label=(action,e,more={})=>{if(e.geom?.length>=3)labels.push({action,image:state.raw.pages[e.page??0].image,entity:e.id,type:e.type,
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
 // Saved in the background: a correction must work even when its label cannot be stored.
 const labels=labelsOf(state.raw.entities,entities),correction=labels.length?crypto.randomUUID():null;
 if(correction)post('/api/labels',{correction,labels}).catch(()=>{});
 undo.push({entities:state.raw.entities,answer:state.rawAnswer,corrected:state.corrected,ms:state.ms,edited:state.edited,forget,correction});
 return commit(entities,done,keep);
}
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
 if(last.correction)post('/api/labels',{correction:last.correction,undo:true}).catch(()=>{});
 paint();hint(last.forget?'Undone, and the symbol is forgotten.':'Undone.');
};
onInspect((entity,box)=>{
 if(!state.editing||!state.raw.entities.some(e=>e.id===entity.id))return;
 const actions=el('div',{class:'actions'},
  el('button',{class:'btn',type:'button',onclick:()=>change(state.raw.entities.filter(e=>e.id!==entity.id),`Removed ${entity.label}`)},'Remove'),
  entity.uncertain?el('button',{class:'btn',type:'button',onclick:()=>change(state.raw.entities.map(e=>e.id===entity.id?{...e,conf:1}:e),`Confirmed ${entity.label}`)},'It is right'):null);
 box.append(actions);
 if(entity.polygon.length<3)return;
 // One wrong detection is rarely alone: outline the others that show the same symbol and offer to remove them together.
 lookAlikes(entity).then(ids=>{
  if(!ids.length||!actions.isConnected)return;  // nothing alike, or the selection moved on
  for(const id of ids)markOf(id)?.classList.add('alike');
  const gone=new Set([entity.id,...ids]);
  actions.append(el('button',{class:'btn',type:'button',title:'The outlined items show the same symbol on the drawing. It is remembered and set aside on drawings you upload from now on.',
   onclick:()=>change(state.raw.entities.filter(e=>!gone.has(e.id)),`Removed ${entity.label} and ${ids.length} that look like it; the symbol is remembered for later drawings`,null,remember(entity))},`Remove this and ${ids.length} like it`));
 }).catch(()=>{});  // no look-alikes offered (a PDF page, the server unreachable); Remove still works
});

// Resize: drag a corner handle of the selected box; the opposite corner stays where it is.
svg.addEventListener('pointerdown',event=>{
 const handle=event.target.closest?.('.edit-handle'),entity=state.scene?.entities.find(e=>e.id===state.selected);
 if(!handle||!entity||event.button!==0)return;
 event.preventDefault();event.stopImmediatePropagation();  // not a pan
 handle.setPointerCapture(event.pointerId);
 const [x0,y0,x1,y1]=bounds(entity.polygon),corner=Number(handle.dataset.corner);
 resize={id:entity.id,label:entity.label,fixed:[corner===0||corner===3?x1:x0,corner<2?y1:y0],box:null};
},true);
svg.addEventListener('pointermove',event=>{
 if(!resize)return;
 const point=svgPoint(event),{width,height}=state.raw.pages[state.page],[fx,fy]=resize.fixed;
 const x=Math.min(Math.max(point.x,0),width),y=Math.min(Math.max(point.y,0),height);
 resize.box=[Math.min(fx,x),Math.min(fy,y),Math.max(fx,x),Math.max(fy,y)];
 // Move the outline and its handles in place: rebuilding them would drop the handle the pointer is holding.
 const points=corners(resize.box);
 for(const shape of markOf(resize.id)?.querySelectorAll('polygon')??[])shape.setAttribute('points',pointsAttr(points));
 layer.querySelectorAll('.edit-handle').forEach((node,i)=>node.setAttribute('style',`--x:${points[i][0]};--y:${points[i][1]}`));
});
function endResize(event){
 if(!resize)return;
 const {id,label,box}=resize,{width,height}=state.raw.pages[state.page];resize=null;
 if(!box){paintSelection();return;}  // a click on the handle, no drag
 const [x0,y0,x1,y1]=[box[0]/width,box[1]/height,box[2]/width,box[3]/height];
 if(event.type!=='pointerup'||x1-x0<.002||y1-y0<.002){paint();hint(event.type==='pointerup'?'That box is too small, so it was left as it was.':'Resize cancelled.');return;}
 change(state.raw.entities.map(e=>e.id===id?{...e,geom:corners([x0,y0,x1,y1])}:e),`Resized ${label}`,id);
}
svg.addEventListener('pointerup',endResize);svg.addEventListener('pointercancel',endResize);

$('edit-toggle').onclick=()=>setEditing(!state.editing);
$('edit-done').onclick=()=>setEditing(false);
$('edit-draw').onclick=()=>{
 if(drawing){stopDrawing();paint();hint('Drawing cancelled.');return;}
 $('view-2d').click();select(null);
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
window.addEventListener('keydown',event=>{if(event.key==='Escape'&&drawing){stopDrawing();paint();hint('Drawing cancelled.');}});
$('page-select').addEventListener('change',()=>{if(drawing)stopDrawing();paint();});
for(const id of ['view-3d','open-another'])$(id).addEventListener('click',()=>{if(state.editing)setEditing(false);});
window.addEventListener('stoa:drawing-reset',()=>{undo=[];state.edited=false;stopDrawing();$('edit-bar').hidden=true;$('edit-toggle').setAttribute('aria-pressed','false');$('edit-undo').disabled=true;layer.replaceChildren();});
