import test from 'node:test';import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';import {adaptScene,adaptAnswer,parseEvalJSONL,summarizeEval} from './contracts.js';import {sharedScene,sharedAnswers} from './mock-scene.js';
const fixture=name=>readFileSync(new URL(`../fixtures/${name}`,import.meta.url),'utf8').replace(/^\uFEFF/,'');

import {adaptAnswerEvidence} from './contracts.js';
test('inline vision evidence is retained without projecting onto an invented page',()=>{
 const entity={id:'vision-room',type:'room',label:'Office',page:1,ref:'vision:room:1',geom:[[.1,.2],[.4,.2],[.4,.5]],qty:{m2:0},conf:.7};
 const raw={answer:0,unit:'m2',confidence:.7,tier:'llm',evidence:['vision-room','missing'],extra_entities:[entity,{...entity}],trace:[{step:'tool:run_plan'}]};
 const entities=adaptAnswerEvidence(raw),result=adaptAnswer(raw,{entities});
 assert.equal(entities.length,1);assert.equal(entities[0].page,1);assert.equal(entities[0].sourceRef,'vision:room:1');assert.equal(entities[0].quantity,0);assert.equal(entities[0].geometryUnavailable,true);assert.deepEqual(entities[0].polygon,[]);assert.deepEqual(entity.geom,[[.1,.2],[.4,.2],[.4,.5]]);
 assert.deepEqual(result.missing,['missing']);assert.equal(result.trace[0].step,'tool:run_plan');
 assert.throws(()=>adaptAnswerEvidence({...raw,extra_entities:[{...entity,geom:[[4,5]]}]}),/normalized/);
 assert.throws(()=>adaptAnswerEvidence({...raw,extra_entities:[entity,{...entity,label:'Conflicting'}]}),/Conflicting/);
});
test('team Scene and Answer examples map normalized coordinates and resolve evidence',()=>{const raw=JSON.parse(fixture('scene.example.json')),a=JSON.parse(fixture('answer.example.json')),s=adaptScene(raw,a),v=adaptAnswer(a,s);assert.equal(v.missing.length,0);assert.equal(s.entities[0].polygon[0][0],.355*2480);assert.equal(v.answer,72.01);assert.equal(v.unit,'m²');});
test('D synthetic wire fixtures pass the same adapter',()=>{for(const a of Object.values(sharedAnswers)){const s=adaptScene(sharedScene,a);assert.equal(adaptAnswer(a,s).missing.length,0);}});
test('extra evidence, absent geometry and page indices remain explicit',()=>{const s={...sharedScene,pages:[...sharedScene.pages,{image:'pages/two.png',width:500,height:400}]};const a={...sharedAnswers.windows,evidence:['extra','unknown'],extra_entities:[{id:'extra',type:'room',page:1,geom:[],qty:{m2:0}}]};const scene=adaptScene(s,a);assert.equal(scene.entities.at(-1).page,1);assert.equal(scene.entities.at(-1).quantity,0);assert.deepEqual(adaptAnswer(a,scene).missing,['unknown']);assert.throws(()=>adaptScene(s,{extra_entities:[{id:'window-1',page:0}]}),/conflicts/);assert.throws(()=>adaptScene(s,{extra_entities:[{id:'bad',page:9}]}),/page/);assert.throws(()=>adaptScene({...s,schema_version:2}),/schema_version/);});
test('provided EvalRow logs retain scores; null correctness is excluded from accuracy',()=>{const rows=parseEvalJSONL(fixture('run.example.jsonl'));assert.equal(rows.length,12);assert.equal(summarizeEval(rows).correct,7);const text={...rows[0],correct:null,pred:'report'};const abstain={...rows[0],pred:null,correct:false};const s=summarizeEval([text,abstain,rows[1]]);assert.equal(s.unscored,1);assert.equal(s.accuracy,1);assert.equal(s.scoredAccuracy,.5);assert.equal(s.coverage,2/3);assert.equal(summarizeEval([]).accuracy,null);assert.throws(()=>parseEvalJSONL('{}'),/identity/);});
test('vision answers can repeat identical scene evidence without duplicate overlays',()=>{
 const original=sharedScene.entities[0],reordered=Object.fromEntries(Object.entries(original).reverse());
 const scene=adaptScene(sharedScene,{...sharedAnswers.windows,extra_entities:[reordered]});
 assert.equal(scene.entities.length,sharedScene.entities.length);
 assert.throws(()=>adaptScene(sharedScene,{extra_entities:[{...original,label:'Conflicting item'}]}),/conflicts/);
});

import {summarizeScene,describeEntity} from './contracts.js';

test('room names show the number once, also when the label already carries it (IFC)',()=>{assert.equal(describeEntity({type:'room',label:'Bagno',number:'5.06'}).label,'Bagno 5.06');assert.equal(describeEntity({type:'room',label:'Kitchen (A103)',number:'A103'}).label,'Kitchen (A103)');});

test('overview distinguishes unsupported room detection from a covered zero',()=>{
 const raw={covers:['window'],entities:[]};const unavailable=summarizeScene(raw);
 assert.equal(unavailable.answer,'Room detection unavailable');assert.match(unavailable.explanation,/0 detected windows/);assert.equal(unavailable.formula,null);
 const zero=summarizeScene({...raw,covers:['room']});assert.equal(zero.answer,'0 detected rooms');
});
test('overview reports partial coverage and partial areas without mixing pages or balconies',()=>{
 const raw={covers:['room:kitchen'],entities:[
  {id:'kitchen',type:'room',page:0,qty:{m2:20}},
  {id:'unmeasured',type:'room',page:0,qty:{}},
  {id:'balcony',type:'room',page:0,outdoor:true,qty:{m2:10}},
  {id:'upstairs',type:'room',page:1,qty:{m2:200}}
 ]};
 const result=summarizeScene(raw);assert.match(result.answer,/3 detected rooms.*partial coverage/);
 assert.match(result.formula,/Partial reported.*20.00.*1 of 2/);assert.doesNotMatch(result.formula,/230.00/);
 assert.deepEqual(result.evidence,['kitchen','unmeasured','balcony']);
 const pageTwo=summarizeScene(raw,1);assert.deepEqual(pageTwo.evidence,['upstairs']);assert.match(pageTwo.formula,/200.00/);
});
test('overview preserves reported zero areas and labels missing quantities',()=>{
 const room={id:'zero',type:'room',qty:{m2:0}};
 assert.match(summarizeScene({covers:['room'],entities:[room]}).formula,/^Reported.*0.00/);
 const missing=summarizeScene({covers:['room'],entities:[{...room,qty:{}}]});assert.equal(missing.formula,null);assert.match(missing.explanation,/areas are unavailable/);
 const partial=summarizeScene({covers:['room'],entities:[room,{id:'unknown',type:'room',qty:{}}]});assert.match(partial.formula,/^Partial reported/);
});
