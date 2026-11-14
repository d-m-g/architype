import {subscribeEditor} from './editor-bridge.js';
import {createDraft,applyCorrection,undoCorrection,boxPolygon,ENTITY_TYPES} from './correction-state.js';
const $=id=>document.getElementById(id), svg=$('drawing'), NS='http://www.w3.org/2000/svg';
const layer=document.createElementNS(NS,'g');layer.id='correction-overlays';svg.append(layer);
const sessions=new Map();
let snapshot=null,key=null,draft=null,selected='',drawing=false,first=null,lastSource='',imageReady=false;
const status=text=>{$('correction-status').textContent=text;};
const active=()=>$('correction-enable').checked&&Boolean(draft);
const page=()=>snapshot?.pageIndex??0;
const dimensions=()=>snapshot?.wireScene?.pages[page()]??{width:1000,height:760};
function cancelBox(){drawing=false;first=null;$('correction-draw').setAttribute('aria-pressed','false');svg.classList.remove('drawing-box');}
function controls(){
  $('correction-fields').disabled=!active();
  $('overlay-toggle').disabled=active();
  $('correction-undo').disabled=!draft?.history.length;
  $('correction-enable').disabled=!draft;
  $('correction-tools').hidden=!active();
  document.body.classList.toggle('correcting-scene',active());
}
function form(){
  const entity=draft?.entities.find(e=>e.id===selected);
  $('correction-type').value=entity?.type??'other';
  $('correction-area').value=entity?.qty?.m2??entity?.qty?.ft2??entity?.quantity??'';
  $('correction-area-unit').value=entity?.qty?.ft2!==undefined||entity?.unit==='ft²'?'ft2':'m2';
  for(const id of ['correction-delete','correction-apply-type','correction-apply-area','correction-confirm'])$(id).disabled=!entity;
  $('correction-confirm').disabled=!entity||Boolean(entity.reviewedByUser)||!entity.uncertain;
}
function select(id){selected=id;$('correction-item').value=id;form();paint();}
function paint(){
  layer.replaceChildren();layer.style.display=active()?'':'none';
  if(!active())return;
  for(const e of draft.entities.filter(e=>(e.page??0)===page()&&e.polygon?.length)){
    const group=document.createElementNS(NS,'g');
    group.setAttribute('class','correction-entity'+(e.id===selected?' selected':'')+(e.uncertain&&!e.reviewedByUser?' uncertain':''));
    group.setAttribute('role','button');group.setAttribute('tabindex','0');group.setAttribute('aria-label','Draft: '+e.label+(e.reviewedByUser?' · confirmed by user':''));
    group.setAttribute('aria-pressed',String(e.id===selected));
    const polygon=document.createElementNS(NS,'polygon');polygon.setAttribute('points',e.polygon.map(p=>p.join(',')).join(' '));group.append(polygon);
    group.addEventListener('click',event=>{event.stopPropagation();if(!drawing)select(e.id);});
    group.addEventListener('pointerdown',event=>{if(!drawing)event.stopPropagation();});
    group.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();select(e.id);}});
    layer.append(group);
  }
  if(first){const marker=document.createElementNS(NS,'circle');marker.setAttribute('cx',first[0]);marker.setAttribute('cy',first[1]);marker.setAttribute('r','6');marker.setAttribute('fill','#2459a6');layer.append(marker);}
}
function render(){
  controls();
  const picker=$('correction-item');picker.replaceChildren();
  for(const e of draft?.entities.filter(e=>(e.page??0)===page())??[]){
    const option=document.createElement('option');option.value=e.id;option.textContent=(e.label||e.id)+' · '+e.type+(e.reviewedByUser?' · user confirmed':'');picker.append(option);
  }
  if(![...picker.options].some(o=>o.value===selected))selected=picker.options[0]?.value??'';
  picker.value=selected;form();paint();
  $('correction-history').replaceChildren();
  for(const change of draft?.history??[]){const li=document.createElement('li');li.textContent=change.description;$('correction-history').append(li);}
  const count=draft?.history.length??0;
  $('correction-count').textContent=count+' local change'+(count===1?'':'s');
  $('correction-answer-note').hidden=!count;
  $('correction-answer-note').textContent=count?'User correction draft pending. Reported answer unchanged: '+(snapshot.answer?.answer??'No answer')+' '+(snapshot.answer?.unit??'')+'. New answer unavailable until recomputation.':'';
}
function change(action){
  try{draft=applyCorrection(draft,action);sessions.set(key,draft);cancelBox();render();status('Draft saved in this session. Reported answer and 3D remain unchanged.');}
  catch(error){status(error.message);}
}
function addBox(points){
  const {width,height}=dimensions();
  const polygon=boxPolygon(...points,width,height);
  const id='user-'+crypto.randomUUID();
  change({kind:'add',entity:{id,type:$('correction-new-type').value,label:'User box '+(draft.entities.filter(e=>e.id.startsWith('user-')).length+1),page:page(),polygon,qty:{},uncertain:true,sourceRef:'User-drawn local draft',method:'user draft'}});
  select(id);
}
for(const id of ['correction-type','correction-new-type'])for(const type of ENTITY_TYPES){const option=document.createElement('option');option.value=type;option.textContent=type;$(id).append(option);}
$('correction-new-type').value='window';
$('correction-enable').onchange=()=>{cancelBox();if(active())$('view-2d').click();render();status(active()?'Editing local draft overlays. The answer and 3D still show the original result.':'Original evidence restored. Your draft is retained in this session.');};
$('correction-item').onchange=()=>select($('correction-item').value);
$('correction-delete').onclick=()=>change({kind:'delete',id:selected});
$('correction-apply-type').onclick=()=>change({kind:'type',id:selected,value:$('correction-type').value});
$('correction-apply-area').onclick=()=>{
  if(!$('correction-area').value.trim()){status('Enter an area; blank does not mean zero.');return;}
  change({kind:'area',id:selected,value:Number($('correction-area').value),unit:$('correction-area-unit').value});
};
$('correction-confirm').onclick=()=>change({kind:'confirm',id:selected});
$('correction-undo').onclick=()=>{draft=undoCorrection(draft);sessions.set(key,draft);cancelBox();render();status('Last draft change undone. Reported answer unchanged.');};
$('correction-draw').onclick=()=>{
  if(drawing){cancelBox();paint();status('Box drawing cancelled.');return;}
  if(!imageReady){status('Wait for a source image before drawing a box. Numeric coordinates remain available.');return;}
  drawing=true;first=null;$('correction-draw').setAttribute('aria-pressed','true');svg.classList.add('drawing-box');svg.focus();status('Click two opposite corners on this page. Escape cancels. You can also enter percentage coordinates below.');
};
$('correction-add-box').onclick=()=>{
  try{const {width,height}=dimensions();const values=['x1','y1','x2','y2'].map(id=>{const input=$('correction-'+id);if(!input.value.trim())throw new Error('Enter all four box coordinates.');return Number(input.value);});
    if(values.some(n=>!Number.isFinite(n)||n<0||n>100))throw new Error('Box coordinates must be from 0 to 100 percent.');
    addBox([values[0]*width/100,values[1]*height/100,values[2]*width/100,values[3]*height/100]);
  }catch(error){status(error.message);}
};
svg.addEventListener('pointerdown',event=>{
  if(!drawing||!active()||event.button!==0)return;
  event.preventDefault();event.stopImmediatePropagation();
  try{const p=svg.createSVGPoint();p.x=event.clientX;p.y=event.clientY;const point=p.matrixTransform(svg.getScreenCTM().inverse());const {width,height}=dimensions();
    if(point.x<0||point.y<0||point.x>width||point.y>height)throw new Error('Choose a point inside the source page.');
    if(!first){first=[point.x,point.y];paint();status('First corner set. Click the opposite corner, or Escape to cancel.');}
    else addBox([...first,point.x,point.y]);
  }catch(error){status(error.message);}
},true);
svg.addEventListener('click',event=>{if(drawing){event.preventDefault();event.stopImmediatePropagation();}},true);
svg.addEventListener('keydown',event=>{if(event.key==='Escape'){cancelBox();paint();status('Box drawing cancelled.');}});
$('view-3d').addEventListener('click',()=>{if(active()){$('correction-enable').checked=false;cancelBox();render();status('3D shows the original scene. Draft corrections are retained for 2D.');}});
svg.querySelector('image').addEventListener('load',()=>{imageReady=true;});
svg.querySelector('image').addEventListener('error',()=>{imageReady=false;cancelBox();});
subscribeEditor(next=>{
  const source=next.mode==='mock'?'mock:courtyard':next.mode==='shared'?next.wireScene.path:null;
  const nextKey=source?source+'|'+JSON.stringify(next.entities):null;
  const oldPage=page(),oldKey=key;snapshot=next;
  const href=svg.querySelector('image').getAttribute('href');
  if(href!==lastSource){lastSource=href;imageReady=next.mode==='mock';}
  if(nextKey!==key){key=nextKey;draft=key?(sessions.get(key)??createDraft(next.entities)):null;if(key)sessions.set(key,draft);selected='';cancelBox();$('correction-enable').checked=false;render();}
  else if(oldPage!==page()){cancelBox();render();}
  else {
    if(next.selectedId&&draft?.entities.some(e=>e.id===next.selectedId)&&selected!==next.selectedId)select(next.selectedId);
    const count=draft?.history.length??0;
    if(count)$('correction-answer-note').textContent='User correction draft pending. Reported answer unchanged: '+(next.answer?.answer??'No answer')+' '+(next.answer?.unit??'')+'. New answer unavailable until recomputation.';
  }
  if(oldKey!==key)status(draft?'Enable correction mode to edit a local draft.':'Load a scene before correcting its entities. Local file preview alone has no extracted scene.');
});

function leaveCorrectionMode(){
 if(!active())return;
 $('correction-enable').checked=false;cancelBox();render();
 status('Original evidence restored. Draft corrections remain in this session.');
}
document.addEventListener('click',event=>{if(event.target.closest('#demo-start,[data-scenario]'))leaveCorrectionMode();},true);
window.addEventListener('hashchange',()=>{if($('viewport-2d').hidden)leaveCorrectionMode();});
