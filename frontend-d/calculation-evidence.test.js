import test from 'node:test';
import assert from 'node:assert/strict';
import {calculationAudit,calculationExclusion,adaptScene} from './contracts.js';

const query={type:'door',included:['d0','d1'],context:['room'],unresolved:['d2'],excluded:[{id:'d2',reason:'Unresolved room connection'},{id:'legend',reason:'Legend symbol outside floor plan'}]};
const answer={trace:[{step:'calculation_evidence',detail:JSON.stringify({threshold:.6,confidence:.7,queries:[query]})}]};
test('calculation audit distinguishes numbered objects from context and excluded or unresolved detections',()=>{
 const audit=calculationAudit(answer);
 assert.deepEqual(audit.included,['d0','d1']);assert.deepEqual(audit.context,['room']);assert.deepEqual(audit.unresolved,['d2']);
 assert.equal(audit.threshold,.6);assert.equal(audit.confidence,.7);assert.deepEqual(audit.excluded.map(e=>e.id),['d2','legend']);
});
test('multi-query evidence is deduplicated and an included item is not also reported as excluded',()=>{
 const raw={trace:[{step:'calculation_evidence',detail:JSON.stringify({threshold:.6,queries:[query,{...query,included:['d2'],excluded:[]}]})}]};
 assert.deepEqual(calculationAudit(raw).included,['d0','d1','d2']);
 assert.deepEqual(calculationAudit(raw).excluded.map(e=>e.id),['legend']);
});
test('malformed audits use the existing evidence route rather than hiding ordinary citations',()=>{
 assert.equal(calculationAudit({trace:[]}),null);
 for(const detail of ['{broken',JSON.stringify({threshold:.6,queries:[{included:'invented'}]}),JSON.stringify({threshold:2,queries:[]})]){
  assert.equal(calculationAudit({trace:[{step:'calculation_evidence',detail}]}),null);
 }
});
test('low scores are drawn dashed for review, not hidden; explicit exclusions remain inspectable in the adapter',()=>{
 const scene={schema_version:1,path:'uploads/x.png',builder:'test',tier:'detect',covers:['door'],pages:[{image:'uploads/x.png',width:100,height:100}],
  entities:[{id:'edge',type:'door',conf:.6,geom:[]},{id:'low',type:'door',conf:.59,geom:[]},
   {id:'legend',type:'door',conf:.9,geom:[],attrs:{calculation_excluded:true,calculation_exclusion_reason:'Legend symbol'}}]};
 const adapted=adaptScene(scene);
 assert.deepEqual(adapted.entities.map(e=>[e.id,e.calculationExcluded,e.uncertain]),[['edge',false,false],['low',false,true],['legend',true,false]]);
 assert.equal(adapted.entities[2].calculationExclusion,'Legend symbol');
 assert.equal(calculationExclusion(scene.entities[1],scene),null);
});
