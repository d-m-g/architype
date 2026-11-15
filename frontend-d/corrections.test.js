import test from 'node:test';
import assert from 'node:assert/strict';
import {createDraft,applyCorrection,undoCorrection,boxPolygon,measure,reshaped,nearestEdge,withCorner,withoutCorner} from './correction-state.js';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {adaptScene} from './contracts.js';

/** edit.js on a stand-in page. With a scene (`raw`) its own change, commit and Undo run; without one, commits are only collected. */
function dragBoxHarness(raw=null){
 const nodes=new Map(),commits=[],captured=new Set();
 function element(tag='div',attrs={},...children){
  const classes=new Set((attrs.class??'').split(' ')),events=new Map();
  const node={tag,attrs,children:[],value:'0',dataset:Object.fromEntries(Object.entries(attrs).filter(([key])=>key.startsWith('data-')).map(([key,value])=>[key.slice(5),String(value)])),
   classList:{add:name=>classes.add(name),remove:name=>classes.delete(name)},
   append(...added){for(const child of added.filter(Boolean)){if(typeof child==='object')child.parent=this;this.children.push(child);}},
   replaceChildren(...added){this.children=[];this.append(...added);},setAttribute(name,value){this.attrs[name]=value;},
   remove(){if(this.parent)this.parent.children=this.parent.children.filter(n=>n!==this);},closest:selector=>classes.has(selector.slice(1))?node:null,
   querySelector(selector){const [,name,id]=selector.match(/^\.([\w-]+)\[data-entity="(.*)"\]$/);return this.querySelectorAll('.'+name).find(n=>n.attrs['data-entity']===id)??null;},
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
 const state={editing:true,edited:false,selected:null,page:0,raw:raw??{pages:[{width:100,height:100}],entities:[]},scene:raw?adaptScene(raw):{entities:[]}};
 const window=element(),inspectors=[],asked=[];
 let selectionChanged=()=>{};
 const context={$,el:element,state,window,Option:class{},subscribeEditor(fn){selectionChanged=fn;},onInspect:fn=>inspectors.push(fn),
  select:id=>{state.selected=id;selectionChanged();},svgPoint:event=>({x:event.x,y:event.y}),CSS:{escape:String},performance,measure,reshaped,nearestEdge,withCorner,withoutCorner,
  // Copied, so the scene is made of this realm's arrays and deepEqual can compare it.
  setScene:next=>{state.raw=structuredClone(next);state.scene=adaptScene(state.raw);},track:promise=>promise,showAnswer:answer=>{state.rawAnswer=answer;},
  // The server's executor, standing in: the floor area of every room in the scene it is sent.
  answerFromScene:async()=>{asked.push(structuredClone(state.raw));return {answer:state.raw.entities.reduce((sum,e)=>sum+(e.qty?.m2??0),0)};}};
 runInNewContext(readFileSync(new URL('./edit.js',import.meta.url),'utf8').replace(/^import .*;\r?$/gm,''),context);
 if(!raw)context.change=(entities,message)=>commits.push({entities:structuredClone(entities),message});
 const layer=$('edit-layer'),entity=id=>state.scene.entities.find(e=>e.id===id);
 return {state,commits,captured,$,window,asked,layer,entity,arm:()=>$('edit-draw').onclick(),event:(type,values)=>$('drawing').dispatch(type,values),
  /** Select an item in Correct mode, as a click on its mark does: returns its mark and handles. */
  pick(id){context.paint();context.select(id);return {mark:layer.querySelector(`.edit-mark[data-entity="${id}"]`),handles:layer.querySelectorAll('.edit-handle')};},
  /** The paragraphs the inspectors add to an item's card; the last inspector only positions the panel on the page. */
  card(id){const box=element();for(const fn of inspectors.slice(0,-1))fn(entity(id),box);return box.querySelectorAll('p').map(p=>p.children.join(''));}};
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

// A page twice as wide as it is high: the kitchen is 100 by 60 pixels, so 6000 px² and 320 px around.
const outlineScene=()=>({schema_version:1,path:'uploads/plan.png',builder:'test@1',tier:'detect',covers:['room','door'],pages:[{image:'pages/plan.png',width:200,height:100}],entities:[
 {id:'kitchen',type:'room',label:'Kitchen',geom:[[.1,.2],[.6,.2],[.6,.8],[.1,.8]],qty:{m:16,wall_m2:40,m2:15}},
 {id:'hall',type:'room',label:'Hall',geom:[[.7,.2],[.9,.2],[.9,.8],[.7,.8]]},
 {id:'door',type:'door',geom:[[.3,.1],[.4,.1],[.4,.2],[.3,.2]]}]});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('dragging a room corner scales its quantities, recalculates the answer and Undo takes all of it back',async()=>{
 const h=dragBoxHarness(outlineScene()),original=structuredClone(h.state.raw);
 h.state.rawAnswer={answer:15};h.state.question='Total floor area?';
 const {mark,handles}=h.pick('kitchen');
 assert.deepEqual(handles.map(n=>n.dataset.vertex),['0','1','2','3'],'a 4-corner room has a handle on each corner');
 assert.equal(h.event('pointerdown',{target:handles[1]}).stopped,true,'a press on a handle is not a pan');
 h.event('pointermove',{x:160,y:20});
 assert.equal(mark.children[0].attrs.points,'20,20 160,20 120,80 20,80','the outline follows the pointer before anything is committed');
 assert.deepEqual(h.state.raw,original);
 h.event('pointerup',{x:160,y:20});await tick();
 const kitchen=h.state.raw.entities[0],around=140+Math.hypot(40,60)+100+60;
 assert.deepEqual(kitchen.geom,[[.1,.2],[.8,.2],[.6,.8],[.1,.8]]);
 assert.deepEqual(kitchen.qty,{m:Math.round(16*around/320*100)/100,wall_m2:Math.round(40*around/320*100)/100,m2:18});
 assert.equal(kitchen.qty.m,18.61,'perimeter is measured in pixels: in 0..1 coordinates this page would give 17.69');
 assert.equal(h.entity('kitchen').quantity,18,'floor area stays the primary quantity');
 assert.deepEqual(h.card('kitchen').slice(0,1),['Outline corrected by user: was 15 m² · 16 m · 40 m² wall, now 18 m² · 18.61 m · 46.51 m² wall.']);
 assert.deepEqual(h.asked.at(-1).entities[0].qty,kitchen.qty,'the executor is sent the corrected scene');
 assert.equal(h.state.rawAnswer.answer,18);assert.match(h.state.corrected,/It was 15 before/);assert.equal(h.state.edited,true);
 assert.equal(h.state.selected,'kitchen');assert.equal(h.layer.querySelectorAll('.edit-handle').length,4,'the room stays selected for the next corner');
 // A second edit keeps what the reader measured, not the first edit's numbers.
 h.event('pointerdown',{target:h.layer.querySelectorAll('.edit-handle')[2]});h.event('pointermove',{x:160,y:80});h.event('pointerup',{x:160,y:80});await tick();
 assert.equal(h.state.raw.entities[0].qty.m2,21);assert.match(h.card('kitchen')[0],/was 15 m² · 16 m · 40 m² wall, now 21 m²/);
 h.$('edit-undo').onclick();assert.equal(h.state.raw.entities[0].qty.m2,18);
 h.$('edit-undo').onclick();
 assert.deepEqual(h.state.raw,original);assert.equal(h.state.rawAnswer.answer,15);assert.equal(h.state.edited,false);assert.equal(h.$('edit-undo').disabled,true);
 assert.doesNotMatch(h.card('kitchen').join(' '),/corrected by user/);
});
test('a click on the outline adds a corner, Alt-click removes one, and an outline keeps at least three',async()=>{
 const h=dragBoxHarness(outlineScene());
 let {mark}=h.pick('kitchen');
 mark.attrs.onclick({x:70,y:50});assert.equal(h.state.raw.entities[0].geom.length,4,'a click inside the room, away from its edges, adds nothing');
 mark.attrs.onclick({x:123,y:50});await tick();
 assert.deepEqual(h.state.raw.entities[0].geom,[[.1,.2],[.6,.2],[.6,.5],[.6,.8],[.1,.8]]);
 assert.deepEqual(h.state.raw.entities[0].qty,{m:16,wall_m2:40,m2:15},'a corner on a straight edge changes no quantity');
 assert.equal(h.layer.querySelectorAll('.edit-handle').length,5);
 for(const left of [4,3]){
  const down=h.event('pointerdown',{target:h.layer.querySelectorAll('.edit-handle')[0],altKey:true});await tick();
  assert.equal(h.state.raw.entities[0].geom.length,left);assert.equal(down.stopped,true);
 }
 assert.deepEqual(h.state.raw.entities[0].geom,[[.6,.5],[.6,.8],[.1,.8]]);assert.equal(h.state.raw.entities[0].qty.m2,3.75);
 h.event('pointerdown',{target:h.layer.querySelectorAll('.edit-handle')[0],altKey:true});
 assert.equal(h.state.raw.entities[0].geom.length,3);assert.match(h.$('edit-hint').textContent,/at least 3 corners/);
 assert.equal(h.event('click').stopped,true,'the click that ends a press on a corner does not add another');
});
test('a room without quantities stays without them, and other boxes keep their four resize corners',async()=>{
 const h=dragBoxHarness(outlineScene());
 h.event('pointerdown',{target:h.pick('hall').handles[0]});h.event('pointermove',{x:130,y:10});h.event('pointerup',{x:130,y:10});await tick();
 const hall=h.state.raw.entities[1];
 assert.deepEqual(hall.geom[0],[.65,.1]);assert.equal('qty' in hall,false);assert.deepEqual(h.card('hall').slice(0,1),['Outline corrected by user.']);
 h.event('pointerdown',{target:h.pick('hall').handles[0]});h.event('pointermove',{x:150,y:50});h.event('pointercancel');
 assert.deepEqual(h.state.raw.entities[1],hall,'a cancelled drag changes nothing');
 assert.deepEqual(h.pick('door').handles.map(n=>n.attrs['data-corner']),[0,1,2,3]);
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

const page={width:200,height:100};  // twice as wide as high: 0.1 across is 20 pixels, 0.1 down is 10
const box=[[.1,.2],[.6,.2],[.6,.8],[.1,.8]];  // 100 by 60 pixels
const close=(actual,expected)=>assert.ok(Math.abs(actual-expected)<1e-9,`${actual} is not ${expected}`);
test('area and perimeter are measured in page pixels, where the two axes differ',()=>{
 assert.deepEqual(measure([[0,0],[1,0],[1,1],[0,1]],page),{area:20000,perimeter:600});
 const {area,perimeter}=measure(box,page);close(area,6000);close(perimeter,320);
 close(measure([...box].reverse(),page).area,6000);  // drawn the other way round
 const triangle=measure([[0,0],[.3,0],[0,.4]],page);close(triangle.area,1200);close(triangle.perimeter,60+40+Math.hypot(60,40));
 // The same shape on a square page measures differently, so 0..1 coordinates alone cannot give the perimeter ratio.
 close(measure(box,{width:100,height:100}).perimeter,220);
});
test('floor area follows the area ratio; perimeter and wall area follow the perimeter ratio',()=>{
 const room={id:'r',type:'room',geom:box,qty:{m2:15,m:16,wall_m2:40,ft2:161.46,m3:37.5},attrs:{height_m:2.5}};
 const wider=[[.1,.2],[.8,.2],[.8,.8],[.1,.8]],next=reshaped(room,wider,page);  // 140 by 60 pixels: area x1.4, perimeter x1.25
 assert.deepEqual(next.qty,{m2:21,m:20,wall_m2:50,ft2:226.04,m3:52.5});
 assert.deepEqual(next.geom,wider);assert.deepEqual(next.attrs,{height_m:2.5});
 assert.deepEqual(room.qty,{m2:15,m:16,wall_m2:40,ft2:161.46,m3:37.5},'the entity it was given is not changed');
 // A sheared outline: the area ratio is the same in any axis scale, the perimeter ratio is not.
 const sheared=reshaped(room,[[.1,.2],[.8,.2],[.6,.8],[.1,.8]],page);
 assert.equal(sheared.qty.m2,18);assert.equal(sheared.qty.m,18.61);assert.equal(sheared.qty.wall_m2,46.51);
});
test('a room without quantities stays without them and unchanged measures keep the reader\'s digits',()=>{
 assert.equal('qty' in reshaped({id:'r',type:'room',geom:box},[[.1,.2],[.8,.2],[.8,.8],[.1,.8]],page),false);
 assert.deepEqual(reshaped({id:'r',type:'room',geom:box,qty:{}},[[.1,.2],[.8,.2],[.8,.8]],page).qty,{});
 const exact={id:'r',type:'room',geom:box,qty:{m2:12.3456,m:14.777}};
 assert.deepEqual(reshaped(exact,withCorner(box,1,[.6,.5]),page).qty,exact.qty);
 // An outline that measured nothing gives no ratio to scale by.
 assert.deepEqual(reshaped({...exact,geom:[[.1,.2],[.1,.2],[.1,.2]]},box,page).qty,exact.qty);
});
test('a corner is added on the nearest edge and removed again, down to three and no further',()=>{
 const pixels=box.map(([x,y])=>[x*page.width,y*page.height]);
 const edge=nearestEdge(pixels,[123,50]);
 assert.equal(edge.index,1);close(edge.point[0],120);close(edge.point[1],50);close(edge.distance,3);
 assert.equal(nearestEdge(pixels,[70,10]).index,0);close(nearestEdge(pixels,[10,5]).distance,Math.hypot(10,15));  // beyond a corner, the corner itself
 const five=withCorner(box,edge.index,[.6,.5]);
 assert.deepEqual(five,[[.1,.2],[.6,.2],[.6,.5],[.6,.8],[.1,.8]]);assert.equal(box.length,4);
 assert.deepEqual(withCorner(box,3,[.1,.5]).at(-1),[.1,.5],'the closing edge takes a corner at the end');
 assert.deepEqual(withoutCorner(five,2),box);
 const three=withoutCorner(box,0);assert.equal(three.length,3);assert.equal(withoutCorner(three,0),null);
});
test('an outline with a few dozen corners is measured, edited and scaled like a box',()=>{
 const ring=Array.from({length:36},(_,i)=>[.5+.3*Math.cos(i*Math.PI/18),.5+.3*Math.sin(i*Math.PI/18)]);
 const {area,perimeter}=measure(ring,page);
 // An ellipse of 60 by 30 pixels, drawn as 36 chords: a little inside the true curve.
 assert.ok(area>.99*Math.PI*60*30&&area<Math.PI*60*30);assert.ok(perimeter>280&&perimeter<300);
 const room={id:'r',type:'room',geom:ring,qty:{m2:50,m:26,wall_m2:60}};
 const next=reshaped(room,ring.map((p,i)=>i===0?[.9,.5]:p),page);
 assert.equal(next.geom.length,36);assert.ok(next.qty.m2>50&&next.qty.m>26&&next.qty.wall_m2>60);
 assert.ok(Math.abs(next.qty.wall_m2/60-next.qty.m/26)<1e-3,'wall area and perimeter grow by the same ratio, to the rounding');
 assert.equal(withCorner(ring,35,[.8,.49]).length,37);assert.equal(withoutCorner(ring,17).length,35);
});
