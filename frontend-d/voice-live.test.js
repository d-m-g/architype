import test from 'node:test';
import assert from 'node:assert/strict';
import {PCMEncoder} from './voice-pcm-worklet.js';
import {TranscriptBuffer,startLiveTranscription} from './voice-live.js';

const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('microphone samples become 16 kHz little-endian PCM chunks with correct duration',()=>{
 const chunks=[],encoder=new PCMEncoder(48000,data=>chunks.push(data));
 encoder.push(new Float32Array(4800).fill(.5));
 assert.equal(chunks.length,1);assert.equal(chunks[0].byteLength,3200);
 assert.equal(new DataView(chunks[0]).getInt16(0,true),16384);
 encoder.push(new Float32Array(480).fill(-2));encoder.flush();
 assert.equal(chunks[1].byteLength,320);assert.equal(new DataView(chunks[1]).getInt16(0,true),-32768);
});
test('interim hypotheses replace each other without becoming final words',()=>{
 const transcript=new TranscriptBuffer();
 assert.equal(transcript.update({interimInputTranscription:{text:'How'}}),'How');
 assert.equal(transcript.update({interimInputTranscription:{text:'How many'}}),'How many');
 assert.equal(transcript.final,'');
 assert.equal(transcript.update({inputTranscription:{text:'How many',startOffset:'0s'}}),'How many');
 assert.equal(transcript.update({inputTranscription:{text:'How many windows?',startOffset:'0s'}}),'How many windows?');
 assert.equal(transcript.update({interimInputTranscription:{text:'On this floor'}}),'How many windows? On this floor');
 assert.equal(transcript.final,'How many windows?');
});

function harness(){
 const sockets=[],nodes=[],timers=new Map(),previews=[],errors=[],requests=[];let timerId=0;
 class Context{
  constructor(){this.sampleRate=16000;this.destination={};this.audioWorklet={addModule:async()=>{}};}
  resume(){return Promise.resolve();}close(){return Promise.resolve();}
  createMediaStreamSource(){return {connect(){},disconnect(){}};}
 }
 class Node{
  constructor(){nodes.push(this);this.port={postMessage:type=>{if(type==='flush')this.port.onmessage({data:{type:'flushed'}});}};}
  connect(){}disconnect(){}
 }
 class Socket{
  constructor(url){this.url=url;this.readyState=0;this.bufferedAmount=0;this.sent=[];sockets.push(this);}
  send(data){this.sent.push(JSON.parse(data));}close(){this.readyState=3;this.onclose?.();}
  open(){this.readyState=1;this.onopen();}receive(data){this.onmessage({data:JSON.stringify(data)});}
 }
 const host={AudioContext:Context,AudioWorkletNode:Node,WebSocket:Socket,
  fetch:async(url,options)=>{requests.push({url,options});return new Response(JSON.stringify({
   token:'auth_tokens/test-ephemeral',setup:{model:'models/gemini-3.5-transcribe-live',generationConfig:{responseModalities:['TEXT']}},
  }));},
  btoa:value=>Buffer.from(value,'binary').toString('base64'),
  setTimeout(fn,ms){const id=++timerId;timers.set(id,{fn,ms});return id;},
  clearTimeout(id){timers.delete(id);},
 };
 const controller=new AbortController();
 const start=startLiveTranscription({host,stream:{},language:'fi-FI',signal:controller.signal,
  onPreview:text=>previews.push(text),onError:error=>errors.push(error)});
 async function ready(){
  for(let n=0;n<10&&!sockets.length;n++)await tick();
  assert.equal(sockets.length,1);sockets[0].open();sockets[0].receive({setupComplete:{}});
  return start;
 }
 function fire(ms){for(const [id,timer] of [...timers])if(timer.ms===ms){timers.delete(id);timer.fn();}}
 return {ready,controller,sockets,nodes,previews,errors,requests,fire};
}

test('live session uses an ephemeral token, streams PCM and finalizes only after Finish',async()=>{
 const h=harness(),live=await h.ready(),socket=h.sockets[0];
 assert.match(socket.url,/BidiGenerateContentConstrained\?access_token=auth_tokens/);
 assert.equal(h.requests[0].url,'/api/voice/live-token');
 assert.deepEqual(JSON.parse(h.requests[0].options.body),{language:'fi-FI'});
 h.nodes[0].port.onmessage({data:{type:'audio',buffer:new Uint8Array([0,1,2,3]).buffer}});
 assert.deepEqual(socket.sent.at(-1),{realtimeInput:{audio:{data:'AAECAw==',mimeType:'audio/pcm;rate=16000'}}});
 socket.receive({serverContent:{interimInputTranscription:{text:'How many'}}});
 socket.receive({serverContent:{inputTranscription:{text:'How many windows?'}}});
 assert.equal(h.previews.at(-1),'How many windows?');
 const finished=live.finish();assert.deepEqual(socket.sent.at(-1),{realtimeInput:{audioStreamEnd:true}});
 socket.receive({serverContent:{turnComplete:true}});h.fire(800);
 assert.equal(await finished,'How many windows?');assert.equal(socket.readyState,3);
});
test('cancelled streaming ignores late text and closes the live connection',async()=>{
 const h=harness(),live=await h.ready(),socket=h.sockets[0];
 socket.receive({serverContent:{interimInputTranscription:{text:'Partial'}}});
 h.controller.abort();
 socket.receive({serverContent:{inputTranscription:{text:'Wrong late words'}}});
 await assert.rejects(live.finish(),{name:'AbortError'});
 assert.deepEqual(h.previews,['Partial']);assert.equal(socket.readyState,3);assert.equal(h.errors.length,0);
});
test('unfinalized interim text is never silently accepted as a completed question',async()=>{
 const h=harness(),live=await h.ready();
 h.sockets[0].receive({serverContent:{interimInputTranscription:{text:'Unfinished words'}}});
 const finished=live.finish();h.fire(10000);
 await assert.rejects(finished,/did not finalize/);assert.equal(h.errors.length,1);
});
