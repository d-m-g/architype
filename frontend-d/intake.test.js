import test from 'node:test';
import assert from 'node:assert/strict';
import {validateLocalDrawing,MAX_LOCAL_BYTES} from './local-file.js';
import {requestDrawing} from './api-client.js';
test('local preview checks file content, empty files and size before displaying',async()=>{
 assert.equal(await validateLocalDrawing(new Blob(['%PDF-1.7\n'])),'application/pdf');
 assert.equal(await validateLocalDrawing(new Blob([new Uint8Array([137,80,78,71,13,10,26,10])])),'image/png');
 await assert.rejects(validateLocalDrawing(new Blob(['<script>fake.pdf</script>'])),/not a supported/);
 await assert.rejects(validateLocalDrawing(new Blob([])),/non-empty/);
 await assert.rejects(validateLocalDrawing({size:MAX_LOCAL_BYTES+1}),/25 MB/);
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
