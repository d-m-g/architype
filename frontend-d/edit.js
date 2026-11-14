/** Correct what the reader found: remove a false detection, confirm a doubtful one, draw a missed one.
 * The answer is recalculated by the server's executor on the corrected scene, with no model call. */
import {$,el,state,setScene,showAnswer,answerFromScene,track,onInspect,svgPoint,select} from './main.js';
import {recoveryMessage} from './api-client.js';

const ADD=[['door','door',null],['window','window',null],['toilet','fixture','toilet'],['sink','fixture','sink'],['bathtub','fixture','bathtub'],['shower','fixture','shower']];
const svg=$('drawing'),layer=$('edit-layer');
let undo=[],drawing=false,first=null;
const hint=text=>{$('edit-hint').textContent=text;};
$('edit-kind').replaceChildren(...ADD.map(([name],i)=>new Option(name,String(i))));

function paint(){
 layer.replaceChildren();
 if(!state.editing||!state.scene)return;
 const area=e=>{const xs=e.polygon.map(p=>p[0]),ys=e.polygon.map(p=>p[1]);return (Math.max(...xs)-Math.min(...xs))*(Math.max(...ys)-Math.min(...ys));};
 const items=state.scene.entities.filter(e=>e.page===state.page&&e.polygon.length>=3&&!['apartment','level'].includes(e.type)).sort((a,b)=>area(b)-area(a));
 for(const entity of items)layer.append(el('g',{class:`edit-mark${entity.id.startsWith('user-')?' added':''}`,role:'button',tabindex:'0','aria-label':entity.label,'data-entity':entity.id,
  onclick:()=>{if(!drawing)select(entity.id);},onkeydown:event=>{if(event.key==='Enter'){event.preventDefault();select(entity.id);}}},
  ...['halo-dark','halo-light','region-shape'].map(kind=>el('polygon',{class:kind,points:entity.polygon.map(p=>p.join(',')).join(' ')}))));
 if(first)layer.append(el('circle',{cx:first.x,cy:first.y,r:4,fill:'var(--accent)','vector-effect':'non-scaling-stroke'}));
}
function setEditing(on){
 state.editing=on;stopDrawing();
 $('edit-bar').hidden=!on;$('edit-toggle').setAttribute('aria-pressed',String(on));
 select(null);
 if(state.raw)setScene(state.raw,{keepView:true});  // main redraws its marks (hidden while editing)
 paint();
 if(on)hint('Click an outline to remove or confirm it, or draw one the reader missed.');
}
function stopDrawing(){drawing=false;first=null;svg.classList.remove('drawing-box');$('edit-draw').setAttribute('aria-pressed','false');}

async function commit(entities,done){
 select(null);
 setScene({...state.raw,entities},{keepView:true});
 state.edited=true;$('edit-undo').disabled=!undo.length;paint();
 if(!state.rawAnswer||!state.question){hint(`${done}. Your next question uses the corrected drawing.`);return;}
 const before=state.rawAnswer.answer;
 hint(`${done}. Recalculating the answer`);
 try{
  const started=performance.now(),answer=await track(answerFromScene(state.question));
  state.corrected=`Recalculated from your corrections, with no model call. It was ${before??'unanswered'} before.`;
  showAnswer(answer,state.question,performance.now()-started);paint();
  hint(`${done}. The answer is now ${answer.answer??'unavailable'}.`);
 }catch(error){
  hint(`${done}, but the answer could not be recalculated: ${/\b501\b/.test(error.message)?'this question needs the agent, which reads the original drawing':recoveryMessage(error)}`);
 }
}
function change(entities,done){
 undo.push({entities:state.raw.entities,answer:state.rawAnswer,corrected:state.corrected,ms:state.ms,edited:state.edited});
 return commit(entities,done);
}
$('edit-undo').onclick=()=>{
 const last=undo.pop();if(!last)return;
 select(null);setScene({...state.raw,entities:last.entities},{keepView:true});
 state.edited=last.edited;state.corrected=last.corrected;$('edit-undo').disabled=!undo.length;
 if(last.answer)showAnswer(last.answer,state.question,last.ms);
 paint();hint('Undone.');
};
onInspect((entity,box)=>{
 if(!state.editing||!state.raw.entities.some(e=>e.id===entity.id))return;
 box.append(el('div',{class:'actions'},
  el('button',{class:'btn',type:'button',onclick:()=>change(state.raw.entities.filter(e=>e.id!==entity.id),`Removed ${entity.label}`)},'Remove'),
  entity.uncertain?el('button',{class:'btn',type:'button',onclick:()=>change(state.raw.entities.map(e=>e.id===entity.id?{...e,conf:1}:e),`Confirmed ${entity.label}`)},'It is right'):null));
});

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
