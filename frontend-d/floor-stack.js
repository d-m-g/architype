import {subscribeEditor,selectEditorEntity} from './editor-bridge.js';
import {imageURL} from './contracts.js';
import {floorPlates,visiblePlates} from './floor-stack-state.js';
const $=id=>document.getElementById(id),NS='http://www.w3.org/2000/svg';
const root=document.createElement('section');root.className='floor-stack';root.hidden=true;root.setAttribute('aria-label','Page image floor stack');
root.innerHTML='<div class="stack-heading"><strong>Floor plates · visual alignment only</strong><span>Page images are stacked in page order. Spacing and alignment are illustrative, not measured elevations.</span></div><div class="stack-controls"><label>View <select id="stack-mode"><option value="stack">All pages</option><option value="single">One page</option></select></label><label>Explode <input id="stack-explode" type="range" min="0" max="100" value="45" aria-label="Separate page plates"></label><label>Active page <select id="stack-page" aria-label="Active floor page"></select></label></div><div id="stack-stage" class="stack-stage"></div><p id="stack-status" role="status"></p>';
$('viewport-3d').append(root);
const stage=root.querySelector('#stack-stage'),mode=root.querySelector('#stack-mode'),explode=root.querySelector('#stack-explode'),picker=root.querySelector('#stack-page'),status=root.querySelector('#stack-status');
let snapshot=null,source='',built='',plateNodes=[];
function activatePage(index){const control=$('page-select');if(control.value!==String(index)){control.value=String(index);control.dispatchEvent(new Event('change',{bubbles:true}));}else draw();}
function makePlate(plate){
 const button=document.createElement('div');button.className='stack-plate';button.dataset.page=plate.index;
 const image=document.createElement('img');image.src=imageURL(plate.image);image.alt='';image.loading='lazy';image.onerror=()=>{button.classList.add('stack-image-error');status.textContent='Page '+(plate.index+1)+' image unavailable. Evidence overlays may still be inspected.';};
 const svg=document.createElementNS(NS,'svg');svg.setAttribute('viewBox',`0 0 ${plate.width} ${plate.height}`);svg.setAttribute('aria-label',`Evidence on page ${plate.index+1}`);svg.setAttribute('role','group');
 for(const e of plate.entities){const polygon=document.createElementNS(NS,'polygon');polygon.setAttribute('points',e.polygon.map(p=>p.join(',')).join(' '));polygon.dataset.entity=e.id;polygon.setAttribute('tabindex','0');polygon.setAttribute('role','button');polygon.setAttribute('aria-label',`${e.label} · page ${plate.index+1}${e.uncertain?' · needs review':''}`);polygon.addEventListener('click',event=>{event.stopPropagation();selectEditorEntity(e.id);});polygon.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();selectEditorEntity(e.id);}});svg.append(polygon);}
 const select=document.createElement('button');select.type='button';select.textContent='Page '+(plate.index+1);select.setAttribute('aria-label','Select page '+(plate.index+1));select.onclick=()=>activatePage(plate.index);
 button.append(image,svg,select);stage.append(button);return button;
}
function draw(){
 if(!snapshot)return;const plates=floorPlates(snapshot),available=plates.length>0;
 root.hidden=!available;$('three-mount').hidden=available;
 if(!available)return;
 if(source!==snapshot.wireScene.path||picker.options.length!==plates.length){source=snapshot.wireScene.path;picker.replaceChildren(...plates.map(p=>new Option('Page '+(p.index+1),String(p.index))));}
 picker.value=String(snapshot.pageIndex);
 const signature=JSON.stringify(plates.map(p=>[p.image,p.width,p.height,p.entities.map(e=>[e.id,e.polygon]) ]));
 if(signature!==built){built=signature;stage.replaceChildren();plateNodes=plates.map(makePlate);}
 const visible=new Set(visiblePlates(plates,mode.value,snapshot.pageIndex).map(p=>p.index));
 for(const node of plateNodes){const index=Number(node.dataset.page),on=visible.has(index);node.hidden=!on;node.style.setProperty('--stack-offset',String((plates.length-1-index)*(Number(explode.value)/100)*72)+'px');node.classList.toggle('active',index===snapshot.pageIndex);for(const shape of node.querySelectorAll('[data-entity]')){const e=plates[index].entities.find(x=>x.id===shape.dataset.entity);shape.classList.toggle('selected',shape.dataset.entity===snapshot.selectedId);shape.classList.toggle('evidence',snapshot.answer?.evidence?.includes(shape.dataset.entity));shape.classList.toggle('uncertain',Boolean(e?.uncertain));}}
 status.textContent=`${mode.value==='single'?'Page '+(snapshot.pageIndex+1):plates.length+' pages'} · page ${snapshot.pageIndex+1} active · ${plates.reduce((n,p)=>n+p.entities.length,0)} drawable objects. Green: answer evidence; gold: selected. Select a plate or object to sync the 2D page.`;
}
subscribeEditor(next=>{snapshot=next;draw();});
mode.onchange=draw;explode.oninput=draw;picker.onchange=()=>activatePage(Number(picker.value));
