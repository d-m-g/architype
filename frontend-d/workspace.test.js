import test from 'node:test';import assert from 'node:assert/strict';import {readFileSync} from 'node:fs';import {publishEditor,subscribeEditor,getEditorSnapshot} from './editor-bridge.js';
import {runInNewContext} from 'node:vm';

function themeHarness({saved=null,dark=false,blocked=false}={}){
 const html=readFileSync(new URL('./index.html',import.meta.url),'utf8');
 const code=html.match(/<script id="theme-bootstrap">([\s\S]*?)<\/script>/)[1];
 const root={dataset:{},style:{}},events=new Map(),changes=new Map(),storage=new Map(saved===null?[]:[['stoa-theme',saved]]);
 const media={matches:dark,addEventListener:(name,fn)=>changes.set(name,fn)};
 const win={matchMedia:()=>media,addEventListener:(name,fn)=>events.set(name,fn),dispatchEvent:event=>events.get(event.type)?.(event)};
 const localStorage={getItem:key=>{if(blocked)throw new Error('Storage blocked');return storage.get(key)??null;},setItem:(key,value)=>{if(blocked)throw new Error('Storage blocked');storage.set(key,value);}};
 runInNewContext(code,{window:win,document:{documentElement:root},localStorage,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}}});
 return {root,storage,theme:win.stoaTheme,device(value){media.matches=value;changes.get('change')();},otherTab(value){events.get('storage')({key:'stoa-theme',newValue:value});}};
}

test('appearance resolves before paint and saved choices survive reload',()=>{
 const first=themeHarness({dark:true});assert.equal(first.root.dataset.theme,'dark');assert.equal(first.root.style.colorScheme,'dark');
 first.theme.setPreference('light');assert.equal(first.storage.get('stoa-theme'),'light');first.device(true);assert.equal(first.root.dataset.theme,'light');
 const reloaded=themeHarness({saved:first.storage.get('stoa-theme'),dark:true});assert.equal(reloaded.root.dataset.theme,'light');assert.equal(reloaded.root.dataset.themePreference,'light');
});

test('follow-device appearance tracks system changes and synchronizes other tabs',()=>{
 const app=themeHarness({saved:'dark'});app.theme.setPreference('system');app.device(false);assert.equal(app.root.dataset.theme,'light');app.device(true);assert.equal(app.root.dataset.theme,'dark');
 app.otherTab('light');assert.equal(app.root.dataset.theme,'light');assert.equal(app.theme.getPreference(),'light');app.otherTab(null);assert.equal(app.theme.getPreference(),'system');assert.equal(app.root.dataset.theme,'dark');
});

test('appearance remains usable when storage is blocked or a saved value is invalid',()=>{
 const blocked=themeHarness({blocked:true});blocked.theme.setPreference('dark');assert.equal(blocked.root.dataset.theme,'dark');assert.equal(blocked.theme.isPersistent(),false);
 const invalid=themeHarness({saved:'invalid',dark:true});assert.equal(invalid.theme.getPreference(),'dark');assert.equal(invalid.root.dataset.theme,'dark');
 assert.equal(themeHarness({dark:false}).root.dataset.theme,'dark');  // dark is the default until a choice is saved
});
test('workspace contains all persistent panels and preserves unique control IDs',()=>{const html=readFileSync(new URL('./index.html',import.meta.url),'utf8');const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);assert.equal(ids.length,new Set(ids).size);for(const id of ['panel-ask','panel-evaluation','drawing','drawing-file','question','ask-form','run-file','viewport-2d','viewport-3d'])assert.ok(ids.includes(id),id);assert.equal((html.match(/<main\b/g)||[]).length,1);assert.equal((html.match(/<\/main>/g)||[]).length,1);});
test('renderer subscription receives current selection and can unsubscribe',()=>{publishEditor({selectedId:'window-1',pageIndex:0});const seen=[];const stop=subscribeEditor(s=>seen.push(s.selectedId));publishEditor({selectedId:'room-1',pageIndex:0});stop();publishEditor({selectedId:'room-2',pageIndex:1});assert.deepEqual(seen,['window-1','room-1']);assert.equal(getEditorSnapshot().pageIndex,1);});

function liveFollowupHarness(response){
 const text=readFileSync(new URL('./app.js',import.meta.url),'utf8');
 const ask=text.slice(text.indexOf('async function askAPI(){'),text.indexOf('function openPending('));
 const nodes=new Map(),calls={opened:0,rendered:0,recorded:0,shown:0,busy:[]};
 const node=id=>{if(!nodes.has(id))nodes.set(id,{value:id==='api-path'?'uploads/current.png':id==='question'?'New question':'',textContent:'',hidden:false});return nodes.get(id);};
 let finish;const pending=new Promise(resolve=>finish=resolve);
 const result={answer:'previous answer',evidence:['old-evidence']};
 const context={window:{dispatchEvent(){}},Event,AbortController,setTimeout:()=>1,clearTimeout(){},$:node,shared:true,rawScene:{path:'uploads/current.png'},result,requestNumber:0,apiAbort:null,lastRequest:null,LIVE_REQUEST_TIMEOUT_MS:300000,isDrawingPath:()=>true,openPending:()=>calls.opened++,setBusy:value=>calls.busy.push(value),requestDrawing:()=>pending,showShared:()=>calls.shown++,recoveryMessage:value=>String(value),render:()=>calls.rendered++,recordHistory:()=>calls.recorded++};
 const request=runInNewContext(ask+';askAPI()',context);
 return {node,calls,result,context,finish:()=>finish(response),request};
}
test('follow-up retains source and evidence until its answer arrives',async()=>{
 const h=liveFollowupHarness({scene:{},answer:{answer:2,evidence:[]},errors:[]});
 assert.equal(h.calls.opened,0);assert.equal(h.context.result,h.result);assert.match(h.node('status').textContent,/Previous result/);assert.equal(h.calls.rendered,0);
 h.finish();await h.request;assert.equal(h.calls.shown,1);assert.equal(h.calls.recorded,1);assert.deepEqual(h.calls.busy,[true,false]);
});
test('failed follow-up retains previous evidence and never saves it as the new question',async()=>{
 const h=liveFollowupHarness({scene:{},answer:null,errors:['Answer: 503 unavailable']});
 h.finish();await h.request;assert.equal(h.context.result,h.result);assert.equal(h.calls.shown,0);assert.equal(h.calls.rendered,0);assert.equal(h.calls.recorded,0);assert.equal(h.node('retry-request').hidden,false);assert.match(h.node('contract-status').textContent,/Previous answer and evidence remain/);
});

test('General view restores all detection layers and fits the page without discarding the answer',()=>{
 const text=readFileSync(new URL('./main.js',import.meta.url),'utf8'),start=text.indexOf('export function toggleGeneralView(){'),end=text.indexOf("$('general-view').onclick",start);
 const answer={answer:4,evidence:['window']},state={generalView:false,editing:false,scene:{entities:[{type:'room'},{type:'window'}]},rawAnswer:answer,result:answer,selected:'window',layers:new Set()};
 const calls={fit:0,frame:0,paint:0},status={};
 const context={state,kindOf:e=>e.type,select:id=>state.selected=id,renderDrawingSide(){},drawMarks:()=>calls.paint++,fit:()=>calls.fit++,frameEvidence:()=>calls.frame++,publish(){},$:()=>status};
 runInNewContext(text.slice(start,end).replace('export ',''),context);
 context.toggleGeneralView();assert.equal(state.generalView,true);assert.deepEqual([...state.layers],['room','window']);assert.equal(state.rawAnswer,answer);assert.equal(state.result,answer);assert.equal(state.selected,null);assert.equal(calls.fit,1);
 context.toggleGeneralView();assert.equal(state.generalView,false);assert.equal(state.layers.size,0);assert.equal(calls.frame,1);assert.equal(state.rawAnswer,answer);
 state.editing=true;context.toggleGeneralView();assert.equal(calls.paint,2);assert.equal(state.generalView,false);
});
test('General view paints detections without answer badges while retaining accurate evidence labels',()=>{
 const text=readFileSync(new URL('./main.js',import.meta.url),'utf8'),start=text.indexOf('function drawMarks(){'),end=text.indexOf('function markSelection()',start);
 const make=(id,type,n)=>({id,type,page:0,label:id,conf:.9,polygon:[[0,0],[n,0],[n,n],[0,n]]});
 const state={generalView:true,editing:false,page:0,layers:new Set(['room','window']),scene:{entities:[make('room','room',20),make('window','window',5)]}};
 const layer={children:[],replaceChildren(){this.children=[];},append(...nodes){this.children.push(...nodes);}};
 const context={state,$:()=>layer,evidenceIds:()=>['window'],area:e=>e.polygon[1][0],kindOf:e=>e.type,mark:(entity,className,label)=>({id:entity.id,className,label}),el:(tag,attrs,...children)=>({tag,attrs,children}),markSelection(){},updateView(){}};
 runInNewContext(text.slice(start,end),context);context.drawMarks();assert.equal(layer.children.length,2);assert.ok(layer.children.every(n=>n.className.startsWith('mark other')));assert.match(layer.children.find(n=>n.id==='window').label,/counted in the answer/);
 state.generalView=false;state.layers.clear();context.drawMarks();assert.equal(layer.children.filter(n=>n.className==='mark evidence').length,1);assert.equal(layer.children.filter(n=>n.attrs?.class?.startsWith('badge')).length,1);
});

function evidenceNavigationCode(){
 const source=readFileSync(new URL('./main.js',import.meta.url),'utf8'),start=source.indexOf('export function nextEvidenceId('),end=source.indexOf("document.addEventListener('keydown',browseEvidence,true)",start);
 return source.slice(start,end).replace('export ','');
}
test('arrow evidence navigation follows displayed order, clamps at ends and supports Home and End',()=>{
 const context={};runInNewContext(evidenceNavigationCode(),context);const ids=['window-z','door-a','room-m'],next=context.nextEvidenceId;
 assert.equal(next(ids,null,'ArrowRight'),'window-z');assert.equal(next(ids,'window-z','ArrowDown'),'door-a');assert.equal(next(ids,'door-a','ArrowRight'),'room-m');assert.equal(next(ids,'room-m','ArrowRight'),'room-m');
 assert.equal(next(ids,'room-m','ArrowUp'),'door-a');assert.equal(next(ids,'window-z','ArrowLeft'),'window-z');assert.equal(next(ids,null,'ArrowLeft'),'room-m');assert.equal(next(ids,'room-m','Home'),'window-z');assert.equal(next(ids,'window-z','End'),'room-m');assert.equal(next([],null,'ArrowDown'),null);assert.equal(next(ids,null,'Enter'),null);
 assert.deepEqual(ids,['window-z','door-a','room-m']);
});
test('evidence shortcuts select and focus the next region while leaving text, modifiers and correction mode alone',()=>{
 const state={result:{evidence:['a','b']},selected:'a',generalView:true,layers:new Set(['room']),editing:false},answer=state.result,calls=[];
 const nodes={workspace:{hidden:false},'panel-ask':{hidden:false}},body={},target={isContentEditable:false,closest:selector=>selector==='#workspace'?{}:null};
 const context={state,asking:false,document:{body},$:id=>nodes[id],evidenceIds:()=>['a','missing','b'],entityById:id=>id==='missing'?null:{id},select:(id,options)=>{calls.push({id,options});state.selected=id;},renderDrawingSide(){},drawMarks(){}};
 runInNewContext(evidenceNavigationCode(),context);
 const event=extra=>({key:'ArrowRight',target,preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;},...extra});
 const e=event();context.browseEvidence(e);assert.equal(calls[0].id,'b');assert.equal(calls[0].options.zoomTo,true);assert.equal(calls[0].options.focusEvidence,true);assert.equal(e.prevented,true);assert.equal(state.generalView,false);assert.equal(state.result,answer);
 for(const extra of [{altKey:true},{ctrlKey:true},{metaKey:true},{shiftKey:true},{isComposing:true},{defaultPrevented:true},{target:{isContentEditable:true,closest(){}}},{target:{closest:()=>({})}}]){const skipped=event(extra);context.browseEvidence(skipped);assert.equal(skipped.prevented,undefined);}
 state.editing=true;context.browseEvidence(event());state.editing=false;context.asking=true;context.browseEvidence(event());context.asking=false;nodes.workspace.hidden=true;context.browseEvidence(event());assert.equal(calls.length,1);
});
test('selecting evidence on another page redraws its overlays and focuses the matching evidence row',()=>{
 const source=readFileSync(new URL('./main.js',import.meta.url),'utf8'),start=source.indexOf('export function select('),end=source.indexOf('/** Walk the numbered',start),code=source.slice(start,end).replace('export ','');
 const entity={id:'page-two',label:'Kitchen',type:'room',page:1,polygon:[],conf:1},state={page:0,selected:null},calls={draw:0,render:0,focus:0,scroll:0},row={dataset:{entity:entity.id},scrollIntoView(){calls.scroll++;},focus(){calls.focus++;}},nodes={'inspector':{replaceChildren(){},append(){}},'evidence-list':{children:[row]},'evidence-navigation':{}};
 const context={state,entityById:()=>entity,setPage:p=>state.page=p,renderDrawingSide:()=>calls.render++,drawMarks:()=>calls.draw++,markSelection(){},$:id=>nodes[id],evidenceIds:()=>['first',entity.id],el:()=>({}),KINDS:{room:['Room']},inspectors:[],matchMedia:()=>({matches:true}),publish(){}};
 runInNewContext(code,context);context.select(entity.id,{focusEvidence:true});assert.equal(state.page,1);assert.equal(calls.draw,1);assert.equal(calls.render,1);assert.equal(calls.focus,1);assert.equal(calls.scroll,1);assert.match(nodes['evidence-navigation'].textContent,/2 of 2/);
});
