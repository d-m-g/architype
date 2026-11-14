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
 const invalid=themeHarness({saved:'invalid',dark:true});assert.equal(invalid.theme.getPreference(),'system');assert.equal(invalid.root.dataset.theme,'dark');
});
test('workspace contains all persistent panels and preserves unique control IDs',()=>{const html=readFileSync(new URL('./index.html',import.meta.url),'utf8');const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(m=>m[1]);assert.equal(ids.length,new Set(ids).size);for(const id of ['panel-editor','panel-dashboard','panel-sources','panel-settings','drawing','question','threshold','source','run-file','scene-file','answer-file','viewport-2d','viewport-3d'])assert.ok(ids.includes(id),id);assert.equal((html.match(/<main\b/g)||[]).length,1);assert.equal((html.match(/<\/main>/g)||[]).length,1);});
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
