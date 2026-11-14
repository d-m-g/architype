import test from 'node:test';
import assert from 'node:assert/strict';
import {createDraft,applyCorrection,undoCorrection,boxPolygon} from './correction-state.js';
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
