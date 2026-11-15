/** Start screen: drop or choose a drawing, or open one from the dataset. An upload opens as soon as it lands. */
import {$,el,openDrawing,track,savedResults,clearSavedResults,formatValue,answerTitle,prepared} from './main.js';
import {isDrawingPath,recoveryMessage} from './api-client.js';
import {prepareLocalDrawing,renderPDFPreview} from './local-file.js';
import {imageURL} from './contracts.js';

const SAMPLES=[
 {path:'data/swiss_dwellings/site1000_bldg2082_floor9385.pdf',thumb:'data/swiss_dwellings/site1000_bldg2082_floor9385.png',name:'Apartment block floor',kind:'CAD plan with four apartments'},
 {path:'data/cubicasa5k/colorful-10052.png',name:'Detached house',kind:'Brochure plan, image only'},
 {path:'data/cubicasa5k/colorful-10266.png',name:'Small flat',kind:'Brochure plan, image only'},
];
const status=text=>{$('start-status').textContent=text;};
const zone=$('dropzone'),idle=[...zone.children];
let uploading=false,objectURL=null;

/** POST the file as the raw body, like api-client's uploadDrawing, but with upload progress. */
function upload(blob,filename,onProgress){
 return new Promise((resolve,reject)=>{
  const xhr=new XMLHttpRequest();
  xhr.open('POST','/api/upload?'+new URLSearchParams({filename}));
  xhr.setRequestHeader('Content-Type',blob.type||'application/octet-stream');
  xhr.responseType='json';xhr.timeout=120000;
  xhr.upload.onprogress=event=>{if(event.lengthComputable)onProgress(event.loaded/event.total);};
  xhr.onload=()=>xhr.status===200&&String(xhr.response?.path).startsWith('uploads/')?resolve(xhr.response)
   :reject(new Error(`${xhr.status}: ${typeof xhr.response?.detail==='string'?xhr.response.detail:xhr.response?.error??xhr.statusText}`));
  xhr.onerror=()=>reject(new Error('Failed to fetch'));
  xhr.ontimeout=()=>reject(new Error('Upload timed out'));
  xhr.send(blob);
 });
}
function showProgress(label,fraction){
 const bar=el('i',{style:`width:${Math.round((fraction??0)*100)}%`});
 zone.classList.add('uploading');
 zone.replaceChildren(el('span',{class:'spinner','aria-hidden':'true'}),el('strong',{},label),
  fraction===null?'':el('span',{class:'upload-progress'},bar),$('drawing-file'));
}
async function take(file){
 if(uploading||!file||prepared.size)return;  // a guest cannot upload
 uploading=true;status('');
 const input=$('drawing-file');
 try{
  showProgress(`Checking ${file.name}`,null);
  const prepared=await prepareLocalDrawing(file);
  // Render the drawing locally so it is on screen the moment the upload ends.
  let preview=null;
  if(objectURL)URL.revokeObjectURL(objectURL);
  if(prepared.type==='application/pdf')preview=await renderPDFPreview(prepared.blob).catch(()=>null);
  else if(prepared.width)preview={displayURL:objectURL=URL.createObjectURL(prepared.blob),width:prepared.width,height:prepared.height};
  showProgress(`Uploading ${file.name}`,0);
  const uploaded=await track(upload(prepared.blob,prepared.name,fraction=>fraction<1?showProgress(`Uploading ${file.name}`,fraction):showProgress('Checking that it is a floor plan',null)));
  openDrawing(uploaded.path,{name:file.name,preview});
 }catch(error){
  // local-file.js already words its own validation errors; transport errors get the recovery copy.
  status(/^\d{3}:|fetch|timed out/i.test(error.message)?recoveryMessage(error):error.message);
 }finally{
  uploading=false;input.value='';zone.classList.remove('uploading','over');zone.replaceChildren(...idle);
 }
}
$('drawing-file').onchange=()=>take($('drawing-file').files[0]);
$('choose-file').onclick=()=>$('drawing-file').click();

// A file dropped anywhere on the Ask view opens it, also while another drawing is open.
const veil=el('div',{class:'drop-veil',hidden:true},'Drop to open this drawing');document.body.append(veil);
const dragsFile=event=>[...(event.dataTransfer?.types??[])].includes('Files')&&!$('panel-ask').hidden;
let depth=0;
window.addEventListener('dragenter',event=>{if(!dragsFile(event))return;depth++;zone.classList.add('over');veil.hidden=!$('start').hidden;});
window.addEventListener('dragleave',()=>{if(depth&&!--depth){zone.classList.remove('over');veil.hidden=true;}});
window.addEventListener('dragover',event=>{if(dragsFile(event))event.preventDefault();});
window.addEventListener('drop',event=>{
 if(!dragsFile(event))return;
 event.preventDefault();depth=0;veil.hidden=true;zone.classList.remove('over');
 if(!$('start').hidden)take(event.dataTransfer.files[0]);
 else{$('open-another').click();take(event.dataTransfer.files[0]);}
});

for(const sample of SAMPLES){
 const card=el('button',{type:'button',class:'sample',onclick:()=>openDrawing(sample.path,{name:sample.name})},
  el('img',{src:imageURL(sample.thumb??sample.path),alt:'',loading:'lazy',onerror:()=>card.remove()}),  // no dataset on this server: no card
  el('span',{},sample.name,el('small',{},sample.kind)));
 $('samples').append(card);
}
/** Drawings answered in this browser before: reopen one with its last answer and earlier questions, no model call. */
function renderRecent(){
 const saved=savedResults();
 $('recent').hidden=!saved.length;
 $('recent-list').replaceChildren(...saved.map(drawing=>{
  const last=drawing.entries.at(-1),[value,unit]=answerTitle(last.rawAnswer);
  return el('li',{},el('button',{type:'button',onclick:()=>openDrawing(drawing.path,{name:drawing.name,restore:true})},
   el('span',{},el('strong',{},drawing.name),el('small',{},`${drawing.entries.length===1?'1 question':drawing.entries.length+' questions'}, last: ${last.question}`)),
   el('b',{},`${value} ${unit}`.trim())));
 }));
}
$('recent-clear').onclick=()=>{clearSavedResults();renderRecent();};
window.addEventListener('stoa:drawing-reset',renderRecent);
renderRecent();
/** Without the password: prepared drawings and their questions only, answered from the server's caches.
 *  Signed in: a switch to turn cached answers off, so plans and decisions come from the model again. */
fetch('/api/access').then(r=>r.ok?r.json():null).then(access=>{
 if(!access)return;
 if(access.guest){
  for(const c of access.cases)prepared.set(c.path,c.questions);
  $('sign-in').hidden=false;$('dropzone').hidden=true;document.querySelector('.by-path').hidden=true;
  status(access.cases.length?'Demo mode: pick a prepared drawing and question; answers replay without a model call.':'Demo mode: no prepared drawings on this server yet.');
  const pick=el('select',{'aria-label':'Prepared drawing'},...access.cases.map(c=>el('option',{value:c.path},`${c.path.replace(/^data\//,'')} (${c.questions.length})`)));
  $('samples').replaceChildren(pick,el('button',{type:'button',class:'btn',onclick:()=>pick.value&&openDrawing(pick.value)},'Open'));
 }else if(access.login){
  $('cache-toggle').hidden=false;$('use-cache').checked=!access.fresh;
  $('use-cache').onchange=()=>{document.cookie=`blueprints_fresh=${$('use-cache').checked?0:1}; path=/; max-age=31536000; samesite=lax`;};
 }
}).catch(()=>{});
$('path-form').onsubmit=event=>{
 event.preventDefault();
 const path=$('live-path').value.trim();
 if(isDrawingPath(path)){status('');openDrawing(path);}
 else status('A dataset path starts with data/ or uploads/.');
};
