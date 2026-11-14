/** Correct what the reader found: remove a false detection (and the ones that look like it), confirm a doubtful
 * one, resize a box by its corners, draw a missed one.
 * The answer is recalculated by the server's executor on the corrected scene, with no model call. */
import {$,el,state,setScene,showAnswer,answerFromScene,track,onInspect,svgPoint,select} from './main.js';
import {requestJSON,recoveryMessage} from './api-client.js';
import {subscribeEditor} from './editor-bridge.js';

const ADD=[['door','door',null],['window','window',null],['toilet','fixture','toilet'],['sink','fixture','sink'],['bathtub','fixture','bathtub'],['shower','fixture','shower']];
const svg=$('drawing'),layer=$('edit-layer');
let undo=[],drawing=false,first=null,resize=null;  // resize: the corner drag in progress
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
  el('polygon',{points:pointsAttr(entity.polygon)})));
 if(first)layer.append(el('circle',{cx:first.x,cy:first.y,r:4,fill:'var(--accent)','vector-effect':'non-scaling-stroke'}));
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
function stopDrawing(){drawing=false;first=null;svg.classList.remove('drawing-box');$('edit-draw').setAttribute('aria-pressed','false');}

/** keep: the entity to leave selected afterwards, so a resized box can be adjusted again. */
async function commit(entities,done,keep=null){
 select(null);
 setScene({...state.raw,entities},{keepView:true});
 state.edited=undo.length>0;$('edit-undo').disabled=!undo.length;paint();select(keep);
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
function change(entities,done,keep=null){
 undo.push({entities:state.raw.entities,answer:state.rawAnswer,corrected:state.corrected,ms:state.ms});
 return commit(entities,done,keep);
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
 state.edited=undo.length>0;state.corrected=last.corrected;$('edit-undo').disabled=!undo.length;
 if(last.answer)showAnswer(last.answer,state.question,last.ms);
 paint();hint('Undone.');
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
  actions.append(el('button',{class:'btn',type:'button',title:'The outlined items show the same symbol on the drawing',
   onclick:()=>change(state.raw.entities.filter(e=>!gone.has(e.id)),`Removed ${entity.label} and ${ids.length} that look like it`)},`Remove this and ${ids.length} like it`));
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
 markOf(resize.id)?.firstChild.setAttribute('points',pointsAttr(points));
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
 hint('Click two opposite corners of the item. Esc cancels.');
};
svg.addEventListener('pointerdown',event=>{
 if(!drawing||event.button!==0)return;
 event.preventDefault();event.stopImmediatePropagation();
 const point=svgPoint(event),{width,height}=state.raw.pages[state.page];
 if(point.x<0||point.y<0||point.x>width||point.y>height){hint('That point is outside the page.');return;}
 if(!first){first=point;paint();hint('Now click the opposite corner.');return;}
 const [name,type,subtype]=ADD[Number($('edit-kind').value)];
 const x0=Math.min(first.x,point.x)/width,x1=Math.max(first.x,point.x)/width,y0=Math.min(first.y,point.y)/height,y1=Math.max(first.y,point.y)/height;
 stopDrawing();
 if(x1-x0<.002||y1-y0<.002){paint();hint('That box is too small. Try again with Draw box.');return;}
 const entity={id:'user-'+Date.now().toString(36),type,subtype,page:state.page,geom:[[x0,y0],[x1,y0],[x1,y1],[x0,y1]],conf:1,ref:'added by you'};
 change([...state.raw.entities,entity],`Added a ${name}`);
},true);
svg.addEventListener('click',event=>{if(drawing){event.preventDefault();event.stopImmediatePropagation();}},true);
window.addEventListener('keydown',event=>{if(event.key==='Escape'&&drawing){stopDrawing();paint();hint('Drawing cancelled.');}});
$('page-select').addEventListener('change',paint);
for(const id of ['view-3d','open-another'])$(id).addEventListener('click',()=>{if(state.editing)setEditing(false);});
window.addEventListener('stoa:drawing-reset',()=>{undo=[];state.edited=false;stopDrawing();$('edit-bar').hidden=true;$('edit-toggle').setAttribute('aria-pressed','false');$('edit-undo').disabled=true;layer.replaceChildren();});
