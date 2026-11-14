import test from 'node:test';
import assert from 'node:assert/strict';
import {validateLocalDrawing,MAX_LOCAL_BYTES,validateImageSize,fitImageSize,createSourcePreviewLoader,sourcePreviewKind,pdfPreviewSize,renderPDFPreview} from './local-file.js';
import {requestDrawing,getConnectionStatus,requestJSON,createSceneLoader} from './api-client.js';

test('connection check reads production OpenAPI and verifies route methods',async(t)=>{
 let route;
 t.mock.method(globalThis,'fetch',async url=>{route=url;return new Response(JSON.stringify({info:{title:'Team API'},paths:{'/api/ask':{post:{}},'/api/scene':{get:{}},'/api/image':{get:{}},'/api/upload':{post:{}}}}));});
 const state=await getConnectionStatus();
 assert.equal(route,'/openapi.json');assert.equal(state.connected,true);assert.equal(state.upload_available,true);assert.equal(state.recompute_available,false);
 t.mock.method(globalThis,'fetch',async()=>new Response(JSON.stringify({paths:{'/api/ask':{get:{}}}})));
 assert.equal((await getConnectionStatus()).connected,false);
});

test('expired sessions retain the backend error and give sign-in recovery',async(t)=>{
 t.mock.method(globalThis,'fetch',async()=>new Response(JSON.stringify({error:'sign in first'}),{status:401}));
 await assert.rejects(requestJSON('/api/scene'),/401: sign in first/);
 assert.match(recoveryMessage('401: sign in first'),/Sign in.*Keep this tab open/);
});
test('local preview checks file content, empty files and size before displaying',async()=>{
 assert.equal(await validateLocalDrawing(new Blob(['%PDF-1.7\n'])),'application/pdf');
 assert.equal(await validateLocalDrawing(new Blob([new Uint8Array([137,80,78,71,13,10,26,10])])),'image/png');
 assert.equal(await validateLocalDrawing(new Blob(['  0\r\nSECTION\r\n  2\r\nHEADER\r\n'])),'image/vnd.dxf');
 assert.equal(await validateLocalDrawing(new Blob(['999\nexported by CAD\n0\nSECTION\n'])),'image/vnd.dxf');
 await assert.rejects(validateLocalDrawing(new Blob(['0\nnot a dxf'])),/not a supported/);
 await assert.rejects(validateLocalDrawing(new Blob(['<script>fake.pdf</script>'])),/not a supported/);
 await assert.rejects(validateLocalDrawing(new Blob([])),/non-empty/);
 await assert.rejects(validateLocalDrawing({size:MAX_LOCAL_BYTES+1}),/25 MB/);
});

test('additional raster formats are identified from bytes rather than the filename',async()=>{
 for(const [bytes,type] of [['RIFFxxxxWEBP','image/webp'],['GIF89a','image/gif'],['GIF87a','image/gif'],['BMxxxx','image/bmp'],['\0\0\0\x20ftypavif\0\0\0\0','image/avif']])assert.equal(await validateLocalDrawing(new Blob([bytes],{type:'application/octet-stream'})),type);
 assert.equal(sourcePreviewKind('uploads/drawing.png'),'image');assert.equal(sourcePreviewKind('uploads/drawing.pdf'),'pdf');assert.equal(sourcePreviewKind('https://example.com/file.png'),null);assert.equal(sourcePreviewKind('uploads/file.svg'),null);
 validateImageSize(2480,1753);assert.throws(()=>validateImageSize(0,10),/too large/);assert.throws(()=>validateImageSize(10000,10000),/too large/);
 assert.deepEqual(fitImageSize(2480,1753),{width:2480,height:1753});
 for(const [w,h] of [[14000,9902],[40000,1000],[1000,40000],[20000,20000]]){const fit=fitImageSize(w,h);validateImageSize(fit.width,fit.height);assert.ok(Math.abs(fit.width/fit.height-w/h)<=w/h*0.02,w+'x'+h+' keeps its proportions');}
 assert.equal(fitImageSize(40000,100).width,16384);
});

test('source preview survives repeated requests and ignores a late previous file',async()=>{
 const pending=new Map(),seen=[];let reads=0;
 const loader=createSourcePreviewLoader(path=>{reads++;return new Promise(resolve=>pending.set(path,resolve));});
 const a=loader.select('uploads/old.png',p=>seen.push(p.path)),b=loader.select('uploads/new.png',p=>seen.push(p.path));
 pending.get('uploads/new.png')({width:640,height:480});await b;
 pending.get('uploads/old.png')({width:100,height:100});await a;
 assert.deepEqual(seen,['uploads/new.png']);
 await loader.select('uploads/new.png',p=>{assert.equal(p.width,640);seen.push(p.path);});assert.equal(reads,2);
 const cancelled=loader.select('uploads/cancelled.png',p=>seen.push(p.path));loader.cancel();pending.get('uploads/cancelled.png')({width:40,height:30});await cancelled;
 assert.deepEqual(seen,['uploads/new.png','uploads/new.png']);
});

test('PDF source preview requires neither a model nor invented image dimensions',async()=>{
 const loader=createSourcePreviewLoader(()=>{throw new Error('Must not decode a PDF as an image');});let preview;
 await loader.select('uploads/source.pdf',p=>preview=p);
 assert.deepEqual(preview,{path:'uploads/source.pdf',image:'uploads/source.pdf',type:'pdf'});
});
test('live question preserves free-form text and sends only path/question after scene attempt',async(t)=>{
 const calls=[],progress=[];
 t.mock.method(globalThis,'fetch',async(url,options)=>{
  calls.push({url,options});
  return calls.length===1?new Response(JSON.stringify({detail:'reader pending'}),{status:501}):new Response(JSON.stringify({answer:null,error:'Cannot verify'}));
 });
 const response=await requestDrawing('data/test.pdf','Kuinka monta laattaa tarvitaan 10 % hukalla?',undefined,m=>progress.push(m));
 assert.equal(response.scene,null);assert.equal(response.answer.answer,null);
 assert.deepEqual(JSON.parse(calls[1].options.body),{path:'data/test.pdf',question:'Kuinka monta laattaa tarvitaan 10 % hukalla?'});
 assert.equal(progress.length,2);assert.match(response.errors[0],/reader pending/);
});
test('cancelling during scene load prevents any question submission',async(t)=>{
 const controller=new AbortController();let calls=0;
 t.mock.method(globalThis,'fetch',async()=>{calls++;controller.abort();return new Response('{}');});
 await assert.rejects(requestDrawing('data/test.pdf','Count',controller.signal),{name:'AbortError'});
 assert.equal(calls,1);
});
test('a cancelled late answer is rejected instead of accepted',async(t)=>{
 const controller=new AbortController();let calls=0;
 t.mock.method(globalThis,'fetch',async()=>{calls++;if(calls===2)controller.abort();return new Response('{}');});
 await assert.rejects(requestDrawing('data/test.pdf','Count',controller.signal),{name:'AbortError'});
 assert.equal(calls,2);
});
import {isDrawingPath,uploadDrawing} from './api-client.js';
test('uploaded identifiers and raw-body upload match the team contract',async(t)=>{
 assert.equal(isDrawingPath('uploads/abc.pdf'),true);assert.equal(isDrawingPath('data/a.png'),true);assert.equal(isDrawingPath('https://example.com/a'),false);
 const blob=new Blob(['%PDF-1.7'],{type:'application/pdf'});let sent;
 t.mock.method(globalThis,'fetch',async(url,options)=>{sent={url,options};return new Response(JSON.stringify({path:'uploads/abc.pdf',reader:'vision'}));});
 const result=await uploadDrawing(blob,'drawing.pdf');
 assert.equal(sent.url,'/api/upload?filename=drawing.pdf');assert.equal(sent.options.body,blob);assert.equal(sent.options.headers['Content-Type'],'application/pdf');assert.equal(result.path,'uploads/abc.pdf');
});

import {recoveryMessage} from './api-client.js';
test('recovery distinguishes model setup, reader, timeout, connection and file failures',()=>{
 assert.match(recoveryMessage('Scene: 501 parser not written; GEMINI_API_KEY is not set'),/Model setup needed/);
 assert.match(recoveryMessage('501: swiss parser not written yet'),/Drawing reader not ready/);
 assert.match(recoveryMessage(new Error('Failed to fetch')),/Cannot reach/);
 assert.match(recoveryMessage('504: timed out'),/took too long/);
 assert.match(recoveryMessage('404 not found'),/Drawing not found/);
 assert.match(recoveryMessage('413 too large'),/file size/);
 assert.match(recoveryMessage('415 unsupported format'),/not supported/);
 assert.match(recoveryMessage('Unexpected response'),/Review the details/);
});

test('PDF source uses a rendered image with actual dimensions and page count',async()=>{
 let loads=0;const loader=createSourcePreviewLoader(()=>{throw new Error('Raster loader must not receive a PDF');},async()=>{loads++;return {displayURL:'data:image/png;base64,preview',width:2400,height:1600,pageCount:8,previewPage:1};});
 let preview;await loader.select('uploads/source.pdf',value=>preview=value);
 assert.equal(preview.displayURL,'data:image/png;base64,preview');assert.equal(preview.width,2400);assert.equal(preview.pageCount,8);
 await loader.select('uploads/source.pdf',()=>{});assert.equal(loads,1);
});
test('PDF preview bounds canvas memory while preserving portrait and rotated page proportions',()=>{
 for(const [w,h] of [[595,842],[842,595],[20000,30000]]){
  const size=pdfPreviewSize(w,h);assert.ok(size.width<=4096&&size.height<=4096);assert.ok(size.width*size.height<8010000);
  assert.ok(Math.abs(size.width/size.height-w/h)<.002);
 }
 assert.throws(()=>pdfPreviewSize(0,10),/invalid dimensions/);
});
test('PDF renders only page one, reports full page count and cleans up on success or failure',async()=>{
 const calls=[];let destroyed=0,taskDestroyed=0,canvas;
 const page={getViewport:({scale})=>({width:600*scale,height:800*scale}),render:options=>{calls.push(options);return {promise:Promise.resolve()};}};
 const pdf={numPages:9,getPage:async n=>{assert.equal(n,1);return page;},destroy:async()=>{destroyed++;}};
 const lib={getDocument:options=>{assert.equal(options.isEvalSupported,false);assert.ok(options.data instanceof Uint8Array);return {promise:Promise.resolve(pdf),destroy:async()=>taskDestroyed++};}};
 const createCanvas=()=>canvas={getContext:()=>({}),toDataURL:type=>{assert.equal(type,'image/png');return 'data:image/png;base64,page';}};
 const result=await renderPDFPreview(new Blob(['%PDF-test']),{loadLibrary:async()=>lib,createCanvas});
 assert.equal(result.pageCount,9);assert.equal(result.previewPage,1);assert.equal(result.width,1800);assert.equal(result.height,2400);assert.equal(destroyed,1);assert.equal(canvas.width,1);assert.equal(calls.length,1);
 page.render=()=>({promise:Promise.reject(new Error('broken PDF'))});
 await assert.rejects(renderPDFPreview(new Blob(['%PDF-test']),{loadLibrary:async()=>lib,createCanvas}),/broken PDF/);assert.equal(destroyed,2);
 const failed={getDocument:()=>({promise:Promise.reject({name:'PasswordException'}),destroy:async()=>taskDestroyed++})};
 await assert.rejects(renderPDFPreview(new Blob(['%PDF-test']),{loadLibrary:async()=>failed,createCanvas}),/requires a password/);assert.equal(taskDestroyed,1);
});

test('overview and follow-up share extraction; one cancelled caller cannot cancel the other',async()=>{
 let loads=0,finish;const loader=createSceneLoader(()=>{loads++;return new Promise(resolve=>finish=resolve);});
 const controller=new AbortController(),path='uploads/shared.png';
 const overview=loader(path),cancelled=loader(path,controller.signal);await Promise.resolve();
 const cancelledResult=assert.rejects(cancelled,{name:'AbortError'});controller.abort();await cancelledResult;
 finish({schema_version:1,path,pages:[],entities:[]});const scene=await overview;
 assert.equal(loads,1);scene.entities.push({id:'local-change'});
 const next=await loader(path);assert.deepEqual(next.entities,[]);assert.equal(loads,1);
});
test('scene failures can be retried and a cancelled retry rejoins ongoing extraction',async()=>{
 let calls=0,finish;const path='uploads/retry.png';
 const loader=createSceneLoader(()=>{calls++;if(calls===1)throw new Error('reader offline');return new Promise(resolve=>finish=resolve);});
 await assert.rejects(loader(path),/reader offline/);
 const controller=new AbortController(),first=loader(path,controller.signal);await Promise.resolve();
 const cancelled=assert.rejects(first,{name:'AbortError'});controller.abort();await cancelled;
 const retry=loader(path);finish({schema_version:1,path,pages:[],entities:[]});await retry;assert.equal(calls,2);
});
test('different drawings never reuse each other and failed-shaped scenes are not cached',async()=>{
 let calls=0;const loader=createSceneLoader(async path=>{calls++;return {schema_version:1,path,pages:[],entities:[]};});
 await loader('uploads/a.png');await loader('uploads/b.png');await loader('uploads/a.png');assert.equal(calls,2);
 let invalid=0;const bad=createSceneLoader(async()=>{invalid++;return {detail:'not a scene'};});await bad('uploads/a.png');await bad('uploads/a.png');assert.equal(invalid,2);
 const controller=new AbortController();controller.abort();await assert.rejects(loader('uploads/a.png',controller.signal),{name:'AbortError'});assert.equal(calls,2);
});
