import test from 'node:test';
import assert from 'node:assert/strict';
import {createHistoryStore,createWorkspaceStorage} from './history-state.js';

function memoryDatabase({fail=false}={}){
 const records=new Map();
 return {open(){
  const request={result:{transaction(){
   const tx={objectStore:()=>({
    put(value,key){const result={};queueMicrotask(()=>{if(fail){tx.error=new Error('Quota exceeded');tx.onabort();}else{records.set(key,structuredClone(value));tx.oncomplete();}});return result;},
    get(key){const result={result:structuredClone(records.get(key))};queueMicrotask(()=>tx.oncomplete());return result;}
   })};return tx;
  }}};queueMicrotask(()=>request.onsuccess());return request;
 }};
}
test('a new page restores large scenes, corrected geometry, answer history and viewer state independently per drawing',async()=>{
 const database=memoryDatabase(),first=createWorkspaceStorage(database);
 const snapshot={path:'uploads/plan.pdf',raw:{pages:[{width:1000,height:800}],entities:[{id:'user-door',label:'x'.repeat(6000000)}]},edited:true,
  rawAnswer:{answer:0,evidence:['user-door']},entries:[{question:'Count doors',payload:{rawAnswer:{answer:0}}}],
  page:2,view:{x:20,y:40,w:300,h:200},generalView:false,selected:'user-door',draft:'How wide?'};
 const saving=first.save(snapshot);snapshot.raw.entities[0].id='mutated';await saving;
 await first.save({path:'data/other.pdf',rawAnswer:{answer:8}});
 const reloaded=await createWorkspaceStorage(database).load('uploads/plan.pdf');
 assert.equal(reloaded.raw.entities[0].id,'user-door');assert.equal(reloaded.raw.entities[0].label.length,6000000);
 assert.equal(reloaded.rawAnswer.answer,0);assert.equal(reloaded.edited,true);assert.equal(reloaded.draft,'How wide?');
 assert.deepEqual(reloaded.view,snapshot.view);assert.deepEqual(reloaded.entries,snapshot.entries);
 assert.equal(reloaded.selected,'user-door');assert.equal(reloaded.generalView,false);assert.equal(reloaded.page,2);
 assert.equal((await first.load('data/other.pdf')).rawAnswer.answer,8);
 assert.equal(await first.load('missing'),null);
});
test('blocked storage and quota failures are reported rather than treated as saved',async()=>{
 await assert.rejects(createWorkspaceStorage(null).load('a'),/unavailable/);
 await assert.rejects(createWorkspaceStorage(memoryDatabase({fail:true})).save({path:'a'}),/Quota/);
});
const entry=(answer=6)=>({origin:'live',question:'Count windows',path:'data/a.pdf',payload:{result:{answer,evidence:['w1'],confidence:.7,cost_usd:.01},scene:{entities:[{id:'w1',qty:{m2:4}}]}}});
test('history snapshots are isolated from input, viewer and correction mutations',()=>{
 const store=createHistoryStore(),input=entry();const id=store.add('a','A',input);
 input.payload.scene.entities[0].qty.m2=99;
 const restored=store.get('a',id);restored.payload.result.answer=99;
 const listed=store.list('a');listed[0].payload.scene.entities=[];
 assert.equal(store.get('a',id).payload.result.answer,6);
 assert.equal(store.get('a',id).payload.scene.entities[0].qty.m2,4);
});
test('drawing groups remain separate, ordered and bounded',()=>{
 const store=createHistoryStore(2);
 const first=store.add('a','A',entry(1));store.add('b','B',entry(9));store.add('a','A',entry(2));store.add('a','A',entry(3));
 assert.deepEqual(store.list('a').map(e=>e.payload.result.answer),[3,2]);
 assert.equal(store.get('a',first),null);
 assert.equal(store.list('b')[0].payload.result.answer,9);
 assert.equal(store.groups().length,2);
});
test('opening or browsing history never creates another result',()=>{
 const store=createHistoryStore();let updates=0;const stop=store.subscribe(()=>updates++);
 const id=store.add('a','A',entry());store.markViewed('a',id);store.setDrawing('b','B');store.markViewed('a',id);
 assert.equal(store.list('a').length,1);assert.equal(store.list('b').length,0);
 assert.deepEqual(store.viewed(),{key:'a',id});assert.ok(updates>1);stop();
});
test('abstentions, zero answers, imported unknown questions and errors remain explicit',()=>{
 const store=createHistoryStore();
 store.add('a','A',entry(0));store.add('a','A',{...entry(null),question:null,origin:'imported'});
 const error=entry(null);error.payload.result.error='Reader unavailable';store.add('a','A',error);
 assert.equal(store.list('a')[2].payload.result.answer,0);
 assert.equal(store.list('a')[1].question,null);
 assert.equal(store.list('a')[0].payload.result.error,'Reader unavailable');
});
test('a follow-up carries the conversation since the drawing was opened: small turns, capped, never the scene or trace',async()=>{
 const {conversationTurns}=await import('./history-state.js');
 const store=createHistoryStore();
 const add=(question,rawAnswer)=>store.add('drawing:a','A',{question,payload:{rawAnswer,ms:5,scene:{entities:[{id:'big'}]}}});
 add('From an earlier visit',{answer:1,evidence:[]});
 const after=Number(store.list('drawing:a')[0].id);
 add('How big are the bathrooms?',{answer:9.8,unit:'m2',interpretation:'Bathrooms',assumptions:{},evidence:Array.from({length:30},(_,i)=>'room-'+i),
  trace:[{step:'decide',detail:'direct'},{step:'execute',detail:"direct: a = SELECT sum(m2) AS value FROM entities WHERE subtype = 'bathroom'"}]});
 add('How high are the ceilings?',{answer:null,error:"Not in this drawing's data: the ceiling height.",evidence:[],assumptions:{ceiling_height_m:'needed: the ceiling height in metres'}});
 for(let i=0;i<4;i++)add('Question '+i+' '+'x'.repeat(500),{answer:i,unit:'count',evidence:[]});
 const turns=conversationTurns(store.list('drawing:a'),{after,path:'data/a.pdf'});
 assert.equal(turns.length,4);assert.deepEqual(turns.map(t=>t.answer),[0,1,2,3]);  // the last four, oldest first
 assert.ok(turns.every(t=>t.question.length<=300&&t.path==='data/a.pdf'&&!('scene' in t)&&!('trace' in t)));
 const all=conversationTurns(store.list('drawing:a'),{after,limit:9});
 assert.equal(all.length,6);assert.ok(!all.some(t=>t.question==='From an earlier visit'));
 assert.deepEqual({...all[0],evidence:all[0].evidence.length},{question:'How big are the bathrooms?',answer:9.8,unit:'m2',interpretation:'Bathrooms',
  query:"direct: a = SELECT sum(m2) AS value FROM entities WHERE subtype = 'bathroom'",evidence:12,evidence_count:30});
 assert.deepEqual(all[1],{question:'How high are the ceilings?',error:"Not in this drawing's data: the ceiling height.",assumptions:{ceiling_height_m:'needed: the ceiling height in metres'}});
 assert.deepEqual(conversationTurns(store.list('drawing:a'),{after:Number(store.list('drawing:a')[0].id)}),[]);  // a newly opened drawing: fresh
});
