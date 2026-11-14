import test from 'node:test';
import assert from 'node:assert/strict';
import {spokenAnswer, mountVoice, mountGoogleVoice} from './voice.js';
import {publishEditor} from './editor-bridge.js';
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
function googleHarness(fetcher){
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
 const stop=mountGoogleVoice(root,host);
 return {get,recorders,audios,revoked,track,stop};
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
