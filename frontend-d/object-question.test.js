import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import {scopedQuestion,questionLabel} from './object-question.js';

test('selected questions preserve identity while conversation labels omit technical context',()=>{
 const entity={id:'room-2',type:'room',label:'Korridor',number:'0.05',page:1};
 const question=scopedQuestion('How many doors connect to this room?',entity);
 assert.match(question,/id="room-2"/);assert.match(question,/type=room/);assert.match(question,/page=2/);
 assert.match(question,/connected_room_id/);
 assert.equal(questionLabel(question),'How many doors connect to this room? — Korridor 0.05');
 assert.equal(scopedQuestion(question,entity),question);
 assert.equal(scopedQuestion('Count doors',null),'Count doors');
 assert.equal(questionLabel('Count doors'),'Count doors');
 const quoted={...entity,id:'door-"2"',label:'Door "B"',number:null,type:'door'};
 assert.equal(questionLabel(scopedQuestion('How wide?',quoted)),'How wide? — Door "B"');
});

test('Ask about this action sets a removable scope without submitting and clears on missing entities',()=>{
 const text=readFileSync(new URL('./main.js',import.meta.url),'utf8');
 const code=text.slice(text.indexOf('export function setQuestionScope'),text.indexOf('function autosize()')).replace('export ','');
 const box={replaceChildren(){this.children=[];},append(...children){this.children.push(...children);}},input={focus(){this.focused=true;}};
 const state={raw:{entities:[{id:'d1',type:'door',label:'Door'}]}},nodes={'question-scope':box,question:input};
 const context={state,$:id=>nodes[id],scheduleSave(){},el:(tag,attrs,...children)=>({tag,attrs,children})};
 runInNewContext(code,context);context.setQuestionScope('d1');
 assert.equal(state.askAbout,'d1');assert.equal(box.hidden,false);assert.equal(input.focused,true);
 assert.equal(input.placeholder,'Ask about this door');assert.equal(box.children[0].children[0],'Asking about Door');
 box.children[1].attrs.onclick();assert.equal(state.askAbout,null);assert.equal(box.hidden,true);
 context.setQuestionScope('deleted');assert.equal(state.askAbout,null);
 assert.match(text,/onclick:\(\)=>setQuestionScope\(entity.id\)/);
});

test('Ask sends the selected identity to the existing API and keeps it in history for correction replays',async()=>{
 const text=readFileSync(new URL('./main.js',import.meta.url),'utf8');
 const code=text.slice(text.indexOf('export async function ask(text)'),text.indexOf('export function setQuestionScope')).replace('export ','');
 const entity={id:'door-2',type:'door',label:'Door',page:0},state={path:'uploads/plan.png',askAbout:entity.id,raw:{entities:[entity]},edited:false};
 const nodes=new Map(),requests=[],entries=[];
 const context={state,scopedQuestion,AbortController,setTimeout,clearTimeout,performance,LIVE_REQUEST_TIMEOUT_MS:30000,
  abortAsk(){},askAbort:null,autosize(){},select(){},setAsking(){},renderAnswer(){},reading:null,
  $:id=>{if(!nodes.has(id))nodes.set(id,{value:'',scrollIntoView(){},scrollTo(){},offsetTop:0});return nodes.get(id);},
  track:p=>p,requestJSON:async(url,options)=>{requests.push(JSON.parse(options.body));return {answer:'.8',trace:[]};},calculationAudit:()=>null,
  historyStore:{add:(...args)=>entries.push(args)},saveResult(){},showAnswer(){},frameEvidence(){},matchMedia:()=>({matches:true})};
 await runInNewContext(code+';ask("How wide is this?")',context);
 assert.match(requests[0].question,/id="door-2"/);assert.equal(requests[0].path,state.path);
 assert.equal(entries[0][2].question,requests[0].question);
});

test('corrected rankings use the browser scene and ordinary questions fall through to the existing planner',async()=>{
 const text=readFileSync(new URL('./main.js',import.meta.url),'utf8');
 const start=text.indexOf('export async function answerFromScene'),end=text.indexOf('//',start);
 const code=text.slice(start,end).replace('export ','');
 const scene={entities:[{id:'corrected'}]},calls=[],answer={answer:'Corridor'};
 const context={state:{raw:scene},requestJSON:async(url,options)=>{calls.push({url,body:JSON.parse(options.body)});return answer;},
  planFor:async()=>{calls.push('plan');return 'plan';},runPlan:async()=>{calls.push('execute');return 'normal';}};
 runInNewContext(code,context);
 assert.equal(await context.answerFromScene('Which room has the most doors?'),answer);
 assert.equal(calls.length,1);assert.deepEqual(calls[0].body.scene,scene);
 context.requestJSON=async()=>{throw Error('422: Not a ranking');};
 assert.equal(await context.answerFromScene('How wide?'),'normal');assert.deepEqual(calls.slice(1),['plan','execute']);
 context.requestJSON=async()=>{throw Error('503: unavailable');};
 await assert.rejects(context.answerFromScene('How wide?'),/503/);
});
