/** Live smoke check; never fabricates scenes or seeds backend caches.
 * node frontend-d/live-smoke.js http://127.0.0.1:8001 data/source/drawing.pdf "Question"
 * Exit 0: verified contracts and images; 2: live reader/answer not ready; 1: unexpected failure.
 */
import assert from 'node:assert/strict';
import {adaptScene,adaptAnswer,imageURL} from './contracts.js';
const [origin='http://127.0.0.1:8001',path,question='How many windows are on this floor?']=process.argv.slice(2);
if(!path?.startsWith('data/')&&!path?.startsWith('uploads/'))throw new Error('Supply an existing data/ drawing path.');
const url=p=>new URL(p,origin);
async function request(route,options={}){
 const response=await fetch(url(route),{...options,signal:AbortSignal.timeout(60000)});
 return {status:response.status,ok:response.ok,value:await response.json()};
}
const home=await fetch(url('/'));assert.equal(home.status,200);assert.match(await home.text(),/Drawing editor/);
const health=await request('/__frontend/api-status');assert.equal(health.value.connected,true);
for(const hidden of ['/.runtime/pyvenv.cfg','/serve.py','/voice.test.js','/.cache/test','/api/unknown']){
 assert.equal((await fetch(url(hidden))).status,404,hidden);
}
const scene=await request('/api/scene?'+new URLSearchParams({path}));
const answer=await request('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path,question})});
console.log('Single-origin UI/API and private-file exclusion: passed.');
console.log('Scene HTTP:',scene.status,'Answer HTTP:',answer.status);
if(!scene.ok||!answer.ok){
 console.log('Live integration blocked:',scene.value.detail??answer.value.detail??'API not ready');
 process.exitCode=2;
}else{
 const adapted=adaptScene(scene.value,answer.value),result=adaptAnswer(answer.value,adapted);
 assert.equal(result.missing.length,0,'Answer references missing evidence');
 for(const page of scene.value.pages){
  const image=await fetch(url(imageURL(page.image)));
  assert.equal(image.status,200,'Page image unavailable');
  assert.ok((await image.arrayBuffer()).byteLength>0,'Empty page image');
 }
 if(answer.value.answer===null){console.log('Explicit abstention:',answer.value.error??'No answer');process.exitCode=2;}
 else console.log('Scene, answer, evidence IDs and page images verified. Visually verify overlay alignment in the browser.');
}
