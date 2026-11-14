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
