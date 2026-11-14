import test from 'node:test';
import assert from 'node:assert/strict';
import {createDraft,applyCorrection,undoCorrection,boxPolygon} from './correction-state.js';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';

function dragBoxHarness(){
 const nodes=new Map(),commits=[],captured=new Set();
 function element(tag='div',attrs={},...children){
  const classes=new Set((attrs.class??'').split(' ')),events=new Map();
  const node={tag,attrs,children:[],value:'0',dataset:{},classList:{add:name=>classes.add(name),remove:name=>classes.delete(name)},
   append(...added){for(const child of added.filter(Boolean)){if(typeof child==='object')child.parent=this;this.children.push(child);}},
   replaceChildren(...added){this.children=[];this.append(...added);},setAttribute(name,value){this.attrs[name]=value;},
   remove(){if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this);},closest:()=>null,
   querySelectorAll(selector){return this.children.filter(n=>typeof n==='object').flatMap(n=>[...(n.tag===selector||n.attrs.class?.split(' ').includes(selector.slice(1))?[n]:[]),...n.querySelectorAll(selector)]);},
   addEventListener(name,fn,capture=false){if(!events.has(name))events.set(name,[]);events.get(name).push({fn,capture});},
   dispatch(name,values={}){
    const event={type:name,pointerId:1,button:0,isPrimary:true,x:20,y:30,clientX:20,clientY:30,target:this,
     preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;},...values};
    const handlers=[...(events.get(name)??[])].sort((a,b)=>Number(b.capture)-Number(a.capture));
    for(const {fn} of handlers){fn(event);if(event.stopped)break;}return event;
   },click(){this.onclick?.();},setPointerCapture:id=>captured.add(id),hasPointerCapture:id=>captured.has(id),releasePointerCapture:id=>captured.delete(id)};
  node.append(...children);return node;
 }
 const $=id=>{if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);};
 const state={editing:true,selected:null,page:0,raw:{pages:[{width:100,height:100}],entities:[]},scene:{entities:[]}};
 const window=element();
 const context={$,el:element,state,window,Option:class{},subscribeEditor(){},onInspect(){},select:id=>state.selected=id,
  svgPoint:event=>({x:event.x,y:event.y})};
 runInNewContext(readFileSync(new URL('./edit.js',import.meta.url),'utf8').replace(/^import .*;\r?$/gm,''),context);
 context.change=(entities,message)=>commits.push({entities:structuredClone(entities),message});
 return {state,commits,captured,$,window,arm:()=>$('edit-draw').onclick(),event:(type,values)=>$('drawing').dispatch(type,values)};
}

test('Draw box previews during a drag and commits once on release using drawing coordinates',()=>{
 const h=dragBoxHarness();h.arm();
 const down=h.event('pointerdown');assert.equal(down.stopped,true);assert.equal(h.captured.has(1),true);assert.equal(h.commits.length,0);
 h.event('pointermove',{x:70,y:80,clientX:400,clientY:500});
 const preview=h.$('edit-layer').children[0];assert.match(preview.attrs.class,/draw-preview/);
 assert.equal(preview.children[0].attrs.points,'20,30 70,30 70,80 20,80');assert.equal(h.commits.length,0);
 h.event('pointerup',{x:70,y:80,clientX:400,clientY:500});
 assert.equal(h.commits.length,1);assert.deepEqual(h.commits[0].entities[0].geom,[[.2,.3],[.7,.3],[.7,.8],[.2,.8]]);
 assert.equal(h.$('edit-layer').children.length,0);assert.equal(h.captured.size,0);assert.equal(h.$('edit-draw').attrs['aria-pressed'],'false');
 assert.equal(h.event('click').stopped,true,'the release click must not select an underlying object');
 h.event('pointerup',{x:70,y:80});assert.equal(h.commits.length,1);
});

test('reverse drags clamp to the page and other pointers cannot move or complete a box',()=>{
 const h=dragBoxHarness();h.arm();h.event('pointerdown',{x:80,y:90,clientX:80,clientY:90});
 h.event('pointerdown',{pointerId:2,isPrimary:false});h.event('pointermove',{pointerId:2,x:50,y:50});h.event('pointerup',{pointerId:2});
 assert.equal(h.commits.length,0);assert.equal(h.captured.has(1),true);
 h.event('pointerup',{x:-20,y:-40,clientX:-20,clientY:-40});
 assert.deepEqual(h.commits[0].entities[0].geom,[[0,0],[.8,0],[.8,.9],[0,.9]]);
});

test('clicks, off-page starts, Escape, lost capture and pointer cancellation never add a box',()=>{
 for(const cancel of ['click','pointercancel','lostpointercapture','Escape','page change','scene change']){
  const h=dragBoxHarness();h.arm();h.event('pointerdown');
  if(cancel==='Escape')h.window.dispatch('keydown',{key:'Escape'});
  else if(cancel==='page change')h.$('page-select').dispatch('change');
  else if(cancel==='scene change'){h.state.raw={...h.state.raw};h.event('pointerup',{x:80,y:80,clientX:80,clientY:80});}
  else h.event(cancel==='click'?'pointerup':cancel);
  assert.equal(h.commits.length,0,cancel);assert.equal(h.captured.size,0,cancel);
  assert.equal(h.$('edit-layer').children.length,0,cancel);
 }
 const h=dragBoxHarness();h.arm();h.event('pointerdown',{x:-1});h.event('pointerup',{x:80,y:80});assert.equal(h.commits.length,0);assert.equal(h.captured.size,0);
});
const entities=[{id:'a',type:'room',subtype:'kitchen',page:0,label:'Kitchen',polygon:[[0,0],[10,0],[10,10],[0,10]],qty:{m2:10,m3:30},conf:.55,uncertain:true},{id:'b',type:'window',page:1,label:'Upstairs window',polygon:[]}];
test('all corrections undo completely without changing source entities or confidence',()=>{
 const original=structuredClone(entities);let draft=createDraft(entities);
 draft=applyCorrection(draft,{kind:'confirm',id:'a'});assert.equal(draft.entities[0].reviewedByUser,true);assert.equal(draft.entities[0].conf,.55);
 draft=applyCorrection(draft,{kind:'type',id:'a',value:'door'});assert.equal(draft.entities[0].subtype,null);
 draft=applyCorrection(draft,{kind:'area',id:'a',value:24,unit:'ft2'});assert.deepEqual(draft.entities[0].qty,{m3:30,ft2:24});
 draft=applyCorrection(draft,{kind:'add',entity:{id:'new',label:'Missed',type:'window',page:1,polygon:boxPolygon(50,50,20,20,100,100)}});
 draft=applyCorrection(draft,{kind:'delete',id:'b'});assert.equal(draft.entities.some(e=>e.id==='b'),false);
 assert.deepEqual(entities,original);assert.equal(draft.history.length,5);
 for(let i=0;i<5;i++)draft=undoCorrection(draft);
 assert.deepEqual(draft.entities,original);assert.equal(draft.history.length,0);
});
test('invalid areas/types, missing targets and duplicate IDs cannot corrupt a draft',()=>{
 const draft=createDraft(entities);
 for(const value of [-1,NaN,Infinity])assert.throws(()=>applyCorrection(draft,{kind:'area',id:'a',value,unit:'m2'}));
 assert.throws(()=>applyCorrection(draft,{kind:'type',id:'a',value:'invented'}));
 assert.throws(()=>applyCorrection(draft,{kind:'delete',id:'missing'}));
 assert.throws(()=>applyCorrection(draft,{kind:'add',entity:entities[0]}));
 assert.equal(draft.history.length,0);assert.deepEqual(draft.entities,entities);
 assert.equal(applyCorrection(draft,{kind:'type',id:'a',value:'room'}),draft);
});
test('boxes normalize reverse corners and reject off-page or degenerate geometry',()=>{
 assert.deepEqual(boxPolygon(80,90,20,30,100,100),[[20,30],[80,30],[80,90],[20,90]]);
 for(const points of [[-1,0,10,10],[0,0,101,10],[5,5,5,10],[0,0,NaN,10]])assert.throws(()=>boxPolygon(...points,100,100));
});
test('independent drawing drafts and undo snapshots do not share mutable state',()=>{
 let first=createDraft(entities),second=createDraft(entities);
 first=applyCorrection(first,{kind:'delete',id:'a'});
 assert.equal(second.entities.length,2);
 first.entities[0].label='Changed outside history';
 assert.equal(undoCorrection(first).entities[1].label,'Upstairs window');
});
