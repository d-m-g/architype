import test from 'node:test';
import assert from 'node:assert/strict';
import {spokenAnswer, mountVoice, mountGoogleVoice} from './voice.js';
import {publishEditor} from './editor-bridge.js';
import {finishVoiceInput} from './voice-input.js';
test('speech distinguishes mock results, zero answers, uncertainty and abstention', () => {
 assert.match(spokenAnswer({mode:'mock',answer:{answer:0,evidence:['a']},entities:[{id:'a',uncertain:true}]}), /Mock demonstration. 0.*1 evidence items/);
 assert.match(spokenAnswer({mode:'shared',answer:{answer:null,error:'Parser unavailable'}}), /No verified answer. Parser unavailable/);
 assert.match(spokenAnswer({mode:'shared',answer:{answer:5,confidence:.8,evidence:[]}}), /80 percent, not necessarily calibrated/);
});
test('dictation requires review, ignores cancelled results, and preserves concurrent typing', () => {
 const elements = new Map();
 const get = id => { if (!elements.has(id)) elements.set(id,{value:'',textContent:'',setAttribute(){},addEventListener(){},dispatchEvent(){}}); return elements.get(id); };
 let session;
 class Recognition { constructor(){ session=this; } start(){} abort(){} }
 const root={getElementById:get,addEventListener(){}};
 const host={SpeechRecognition:Recognition,addEventListener(){}};
 const unsubscribe=mountVoice(root,host);
 get('question').value='Original';
 get('voice-dictate').onclick();
 const cancelled=session;
 get('voice-dictate').onclick();
 const event={results:[Object.assign([{transcript:'How many windows?'}],{isFinal:true})]};
 cancelled.onresult(event);
 assert.equal(get('question').value,'Original');
 get('voice-dictate').onclick();session.onresult(event);
 assert.equal(get('question').value,'How many windows?');
 assert.match(get('voice-status').textContent,/Review/);
 get('voice-dictate').onclick();get('question').value='Typed edit';session.onresult(event);
 assert.equal(get('question').value,'Typed edit');
 assert.equal(get('voice-read').disabled,true);
 unsubscribe();
});
test('cancelled playback callbacks cannot overwrite stop status or a newer playback', () => {
 const elements = new Map();
 const get = id => { if (!elements.has(id)) elements.set(id,{value:'',textContent:'',setAttribute(){},addEventListener(){}}); return elements.get(id); };
 const utterances=[];
 const host={addEventListener(){},SpeechSynthesisUtterance:class{constructor(text){this.text=text;}},speechSynthesis:{speak(u){utterances.push(u);},cancel(){utterances.at(-1)?.onend();}}};
 const unsubscribe=mountVoice({getElementById:get,addEventListener(){}},host);
 get('voice-read').onclick();
 get('voice-stop').onclick();
 assert.equal(get('voice-status').textContent,'Reading stopped.');
 utterances[0].onerror();
 assert.equal(get('voice-status').textContent,'Reading stopped.');
 get('voice-read').onclick();
 utterances[0].onend();
 assert.equal(get('voice-stop').disabled,false);
 assert.equal(get('voice-status').textContent,'Reading the current answer…');
 utterances[1].onend();
 assert.equal(get('voice-stop').disabled,true);
 assert.equal(get('voice-status').textContent,'Finished reading.');
 unsubscribe();
});

const tick=()=>new Promise(resolve=>setImmediate(resolve));
function googleHarness(fetcher,overrides={}){
 const elements=new Map(),recorders=[],audios=[],revoked=[];
 const get=id=>{
  if(!elements.has(id))elements.set(id,{value:id==='voice-language'?'fi-FI':'',textContent:'',
   setAttribute(){},addEventListener(){},removeEventListener(){},dispatchEvent(){}});
  return elements.get(id);
 };
 const track={stopped:false,stop(){this.stopped=true;}};
 class Recorder{
  static isTypeSupported(type){return type.startsWith('audio/webm');}
  constructor(stream,options){this.mimeType=options.mimeType;this.state='inactive';recorders.push(this);}
  start(){this.state='recording';}
  stop(){this.state='inactive';this.ondataavailable({data:new Blob(['recording'])});this.done=this.onstop();}
 }
 class Audio{
  constructor(url){this.url=url;audios.push(this);}
  play(){return Promise.resolve();}
  pause(){this.paused=true;}
 }
 const root={getElementById:get,addEventListener(){},removeEventListener(){}};
 const host={fetch:fetcher,MediaRecorder:Recorder,Audio,Blob,setTimeout,clearTimeout,
  navigator:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[track]})}},
  URL:{createObjectURL:()=>`blob:${audios.length}`,revokeObjectURL:url=>revoked.push(url)},
  addEventListener(){},removeEventListener(){}};
 Object.assign(host,overrides);
 const stop=mountGoogleVoice(root,host);
 return {get,recorders,audios,revoked,track,stop,host};
}
const configResponse=()=>new Response(JSON.stringify({configured:true,max_record_seconds:30,max_audio_bytes:8388608}));
test('Google dictation sends recorded audio and preserves edits made during transcription',async()=>{
 let resolveTranscript,request;
 const h=googleHarness(async(url,options)=>{
  if(url.endsWith('config'))return configResponse();
  request={url,options};return new Promise(resolve=>{resolveTranscript=resolve;});
 });
 try{
  await tick();h.get('question').value='Original';await h.get('voice-dictate').onclick();
  await h.get('voice-dictate').onclick();
  assert.match(request.url,/language=fi-FI/);assert.ok(request.options.body instanceof Blob);
  assert.equal(h.track.stopped,true);
  h.get('question').value='My typed edit';
  resolveTranscript(new Response(JSON.stringify({text:'Kuinka monta ovea?'})));
  await h.recorders[0].done;
  assert.equal(h.get('question').value,'My typed edit');assert.match(h.get('voice-status').textContent,/discarded/);
 }finally{h.stop();}
});
test('Google dictation inserts text for review and ignores late cancelled replies',async()=>{
 let resolveTranscript;
 const h=googleHarness(async url=>url.endsWith('config')?configResponse():new Promise(resolve=>{resolveTranscript=resolve;}));
 try{
  await tick();await h.get('voice-dictate').onclick();await h.get('voice-dictate').onclick();
  resolveTranscript(new Response(JSON.stringify({text:'Kuinka monta ovea?'})));await h.recorders[0].done;
  assert.equal(h.get('question').value,'Kuinka monta ovea?');assert.match(h.get('voice-status').textContent,/Review/);
  await h.get('voice-dictate').onclick();await h.get('voice-dictate').onclick();
  await h.get('voice-dictate').onclick();
  resolveTranscript(new Response(JSON.stringify({text:'Stale reply'})));await h.recorders[1].done;
  assert.equal(h.get('question').value,'Kuinka monta ovea?');
 }finally{h.stop();}
});
test('Google playback speaks current answer and stop revokes its audio URL',async()=>{
 let sent;
 const h=googleHarness(async(url,options)=>{
  if(url.endsWith('config'))return configResponse();
  sent=JSON.parse(options.body);return new Response(new Blob(['wav'],{type:'audio/wav'}));
 });
 try{
  publishEditor({wireScene:{path:'uploads/test.png'},mode:'shared',answer:{answer:19,unit:'count',confidence:.7,evidence:[]}});
  await tick();await h.get('voice-read').onclick();
  assert.match(sent.text,/Raportoitu vastaus\. 19/);
  assert.equal(h.audios.length,1);assert.equal(h.get('voice-stop').disabled,false);
  h.get('voice-stop').onclick();assert.equal(h.audios[0].paused,true);assert.equal(h.revoked.length,1);
 }finally{h.stop();publishEditor({wireScene:null,answer:null});}
});
test('changing drawings cancels recording and releases microphone tracks',async()=>{
 const h=googleHarness(async()=>configResponse());
 try{
  await tick();
  // Recording starts normally; changing source cancels it before any transcript is applied.
  publishEditor({wireScene:{path:'uploads/a.png'},answer:null});
  await h.get('voice-dictate').onclick();
  publishEditor({wireScene:{path:'uploads/b.png'},answer:null});
  assert.equal(h.recorders[0].state,'inactive');assert.equal(h.track.stopped,true);
  assert.match(h.get('voice-status').textContent,/Drawing or answer changed/);
 }finally{h.stop();publishEditor({wireScene:null,answer:null});}
});

test('Google streaming waits for readiness and Ask finalizes the transcript without another microphone click',async()=>{
 let socket;
 class Context{
  constructor(){this.sampleRate=16000;this.destination={};this.audioWorklet={addModule:async()=>{}};}
  resume(){return Promise.resolve();}close(){return Promise.resolve();}
  createMediaStreamSource(){return {connect(){},disconnect(){}};}
 }
 class Worklet{
  constructor(){this.port={postMessage:()=>this.port.onmessage({data:{type:'flushed'}})};}
  connect(){}disconnect(){}
 }
 class Socket{
  constructor(){socket=this;this.readyState=0;this.bufferedAmount=0;}
  send(){}close(){this.readyState=3;this.onclose?.();}
  receive(content){this.onmessage({data:JSON.stringify(content)});}
 }
 const h=googleHarness(async url=>new Response(JSON.stringify(url.endsWith('config')?{
  configured:true,live_transcription:true,max_record_seconds:30,
 }:{token:'auth_tokens/test',setup:{model:'models/gemini-3.5-transcribe-live'}})),{
  AudioContext:Context,AudioWorkletNode:Worklet,WebSocket:Socket,
 });
 try{
  await tick();h.get('question').value='My original draft';
  const recording=h.get('voice-dictate').onclick();
  for(let n=0;n<10&&!socket;n++)await tick();
  assert.match(h.get('voice-state').textContent,/Connecting/);
  assert.equal(h.get('ask-submit').disabled,true);
  assert.equal(h.get('voice-preview').hidden,true);
  assert.ok(socket);socket.readyState=1;socket.onopen();socket.receive({setupComplete:{}});await recording;
  assert.equal(h.get('voice-state').textContent,'Listening');assert.equal(h.get('ask-submit').disabled,false);
  socket.receive({serverContent:{interimInputTranscription:{text:'Kuinka monta'}}});
  assert.equal(h.get('voice-transcript').textContent,'Kuinka monta');
  assert.equal(h.get('voice-preview').hidden,false);assert.equal(h.get('question').value,'My original draft');
  socket.receive({serverContent:{inputTranscription:{text:'Kuinka monta ovea?'}}});
  assert.equal(h.get('question').value,'My original draft');
  const finished=finishVoiceInput();socket.receive({serverContent:{turnComplete:true}});assert.equal(await finished,true);
  assert.equal(h.get('question').value,'Kuinka monta ovea?');assert.equal(h.get('voice-state').textContent,'Review question');
  assert.equal(h.get('voice-preview').hidden,true);assert.equal(h.track.stopped,true);
 }finally{h.stop();}
});

test('Ask waits for recorded transcription and cannot submit a stale draft',async()=>{
 let resolveTranscript;
 const h=googleHarness(async url=>url.endsWith('config')?configResponse():new Promise(resolve=>{resolveTranscript=resolve;}));
 try{
  await tick();h.get('question').value='Old typed question';await h.get('voice-dictate').onclick();
  let submitted=false;
  const submission=finishVoiceInput().then(ready=>{if(ready)submitted=h.get('question').value;});
  await tick();assert.equal(submitted,false);assert.equal(h.get('ask-submit').disabled,true);
  resolveTranscript(new Response(JSON.stringify({text:'How many doors are there?'})));
  await submission;assert.equal(submitted,'How many doors are there?');
 }finally{h.stop();}
});

test('cancelling transcription while Ask waits prevents submission',async()=>{
 let resolveTranscript;
 const h=googleHarness(async url=>url.endsWith('config')?configResponse():new Promise(resolve=>{resolveTranscript=resolve;}));
 try{
  await tick();await h.get('voice-dictate').onclick();const submission=finishVoiceInput();
  await tick();await h.get('voice-dictate').onclick();
  resolveTranscript(new Response(JSON.stringify({text:'Stale words'})));
  assert.equal(await submission,false);assert.equal(h.get('question').value,'');
 }finally{h.stop();}
});

function voiceHarness(){
 const nodes=new Map(),events=new Map();let session;
 const get=id=>{if(!nodes.has(id))nodes.set(id,{value:'',textContent:'',hidden:true,attrs:{},listeners:new Map(),setAttribute(k,v){this.attrs[k]=v;},addEventListener(k,fn){this.listeners.set(k,fn);},dispatchEvent(event){this.listeners.get(event.type)?.(event);},focus(){}});return nodes.get(id);};
 class Recognition{constructor(){session=this;}start(){}abort(){this.aborted=true;this.onend?.();}}
 const stop=mountVoice({getElementById:get,addEventListener(){}},{SpeechRecognition:Recognition,addEventListener:(type,fn)=>events.set(type,fn)});
 return {get,events,stop,start(){get('voice-dictate').onclick();return session;},result(target,text,isFinal){target.onresult({results:[Object.assign([{transcript:text}],{isFinal})]});}};
}
test('live dictation shows interim words separately and only commits a final transcript for review',()=>{
 const h=voiceHarness();h.get('question').value='Typed draft';assert.equal(h.get('voice-state').textContent,'Ready');
 const session=h.start();assert.equal(session.interimResults,true);assert.equal(h.get('voice-state').textContent,'Listening');assert.equal(h.get('voice-dictate').attrs['aria-label'],'Cancel dictation');
 h.result(session,'How many',false);assert.equal(h.get('voice-transcript').textContent,'How many');assert.equal(h.get('voice-preview').hidden,false);assert.equal(h.get('question').value,'Typed draft');
 h.result(session,'How many windows',false);assert.equal(h.get('voice-transcript').textContent,'How many windows');
 session.onspeechend();assert.equal(h.get('voice-state').textContent,'Transcribing');
 h.result(session,'How many windows?',true);assert.equal(h.get('question').value,'How many windows?');assert.equal(h.get('voice-state').textContent,'Review question');assert.equal(h.get('voice-preview').hidden,true);assert.equal(h.get('voice-dictate').attrs['aria-pressed'],'false');assert.match(h.get('voice-status').textContent,/choose Ask/);h.stop();
});
test('cancelled and failed interim recognition preserve the typed question and ignore late transcripts',()=>{
 const h=voiceHarness();h.get('question').value='Keep this';const old=h.start();h.result(old,'unfinished',false);
 h.get('voice-dictate').onclick();assert.equal(h.get('voice-state').textContent,'Ready');assert.equal(h.get('voice-preview').hidden,true);
 const current=h.start();h.result(old,'Wrong late final',true);assert.equal(h.get('question').value,'Keep this');assert.equal(h.get('voice-state').textContent,'Listening');
 current.onerror({error:'network'});assert.equal(h.get('question').value,'Keep this');assert.match(h.get('voice-status').textContent,/could not connect/);assert.equal(h.get('voice-state').textContent,'Ready');h.stop();
});
test('typing, drawing changes and recognition ending discard partial speech without overwriting the draft',()=>{
 const h=voiceHarness();h.get('question').value='Original';let session=h.start();h.result(session,'Partial',false);
 h.get('question').value='Manual edit';h.get('question').dispatchEvent(new Event('input'));h.result(session,'Late answer',true);assert.equal(h.get('question').value,'Manual edit');assert.equal(session.aborted,true);
 session=h.start();h.result(session,'Another partial',false);session.onend();assert.equal(h.get('voice-state').textContent,'Ready');assert.equal(h.get('voice-preview').hidden,true);assert.equal(h.get('question').value,'Manual edit');
 session=h.start();h.events.get('stoa:drawing-reset')();h.result(session,'Wrong drawing',true);assert.equal(h.get('question').value,'Manual edit');assert.equal(h.get('voice-state').textContent,'Ready');h.stop();
});
