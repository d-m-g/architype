import test from 'node:test';
import assert from 'node:assert/strict';
import {createHistoryStore} from './history-state.js';
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
