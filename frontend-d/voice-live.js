/** Gemini Live transcription with ephemeral credentials and separate interim/final text. */
const LIVE_URL='wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContentConstrained';
export const supportsLiveAudio=host=>Boolean(host.WebSocket&&host.AudioWorkletNode&&(host.AudioContext||host.webkitAudioContext));

export class TranscriptBuffer {
 constructor(){this.segments=[];this.interim='';}
 update(content){
  const final=content.inputTranscription;
  if(final?.text){
   const key=final.startOffset;
   const existing=key===undefined?-1:this.segments.findIndex(segment=>segment.key===key);
   if(existing<0)this.segments.push({key,text:final.text});else this.segments[existing].text=final.text;
   this.interim='';
  }
  if(content.interimInputTranscription)this.interim=content.interimInputTranscription.text||'';
  return this.preview;
 }
 get final(){return this.segments.map(segment=>segment.text.trim()).filter(Boolean).join(' ');}
 get preview(){return [this.final,this.interim.trim()].filter(Boolean).join(' ');}
}

export async function startLiveTranscription({host=window,stream,language='auto',signal,onPreview=()=>{},onError=()=>{}}){
 const Context=host.AudioContext||host.webkitAudioContext;
 const context=new Context({sampleRate:16000});
 let socket,node,source,ended=false,stopping=false,ready=false,quietTimer,finalTimer,setupTimer,flushed=false;
 let resolveReady,rejectReady,resolveFinal,rejectFinal;
 const transcript=new TranscriptBuffer();
 const done=new Promise((resolve,reject)=>{resolveFinal=resolve;rejectFinal=reject;});
 done.catch(()=>{}); // A disconnect before Finish still reports through onError.
 const connected=new Promise((resolve,reject)=>{resolveReady=resolve;rejectReady=reject;});
 connected.catch(()=>{});
 const closeAudio=()=>{source?.disconnect();node?.disconnect();context.close().catch(()=>{});};
 const cleanup=()=>{
  host.clearTimeout(quietTimer);host.clearTimeout(finalTimer);host.clearTimeout(setupTimer);
  signal?.removeEventListener('abort',abort);closeAudio();
  if(socket&&socket.readyState<2)socket.close();
 };
 const fail=error=>{
  if(ended)return;ended=true;cleanup();rejectReady(error);rejectFinal(error);
  if(ready&&error.name!=='AbortError')onError(error);
 };
 const abort=()=>{const error=new Error('Dictation cancelled.');error.name='AbortError';fail(error);};
 const complete=()=>{
  if(ended)return;
  if(transcript.interim.trim()){fail(new Error('Final transcription did not arrive. Your typed question is unchanged. Please retry.'));return;}
  ended=true;cleanup();resolveFinal(transcript.final);
 };
 const settle=()=>{
  host.clearTimeout(quietTimer);
  if(stopping&&flushed&&transcript.final&&!transcript.interim.trim())quietTimer=host.setTimeout(complete,800);
 };
 const send=value=>{
  if(socket?.readyState!==1)throw new Error('Live transcription connection was lost.');
  if(socket.bufferedAmount>512*1024)throw new Error('Live connection is too slow. Try again with a shorter question.');
  socket.send(JSON.stringify(value));
 };
 const streamEnd=()=>{
  if(ended||flushed)return;
  flushed=true;closeAudio();
  try{send({realtimeInput:{audioStreamEnd:true}});}
  catch(error){fail(error);return;}
  // Do not commit interim guesses or assume earlier finalized segments include the tail of the recording.
  finalTimer=host.setTimeout(()=>{
   if(transcript.final&&!transcript.interim.trim())complete();
   else fail(new Error('Google did not finalize the recording. Your typed question is unchanged.'));
  },10000);
 };
 if(signal?.aborted){abort();throw new Error('Dictation cancelled.');}
 signal?.addEventListener('abort',abort,{once:true});
 try{
  await context.resume();
  await context.audioWorklet.addModule(new URL('./voice-pcm-worklet.js',import.meta.url));
  const response=await host.fetch('/api/voice/live-token',{
   method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({language}),signal,
  });
  const credentials=await response.json();
  if(!response.ok)throw new Error(credentials.detail||'Live transcription is unavailable.');
  if(ended)throw new Error('Dictation cancelled.');
  socket=new host.WebSocket(LIVE_URL+'?access_token='+encodeURIComponent(credentials.token));
  socket.binaryType='arraybuffer';
  setupTimer=host.setTimeout(()=>fail(new Error('Google live connection timed out.')),15000);
  socket.onopen=()=>{try{send({setup:credentials.setup});}catch(error){fail(error);}};
  socket.onerror=()=>fail(new Error('Could not connect to Google live transcription.'));
  socket.onclose=()=>{if(!ended)fail(new Error('Google live transcription disconnected before completion.'));};
  socket.onmessage=event=>{
   if(ended)return;
   try{
    const raw=typeof event.data==='string'?event.data:new TextDecoder().decode(event.data);
    const message=JSON.parse(raw);
    if(message.error)throw new Error(message.error.message||'Google live transcription failed.');
    if(message.setupComplete&&!ready){host.clearTimeout(setupTimer);ready=true;resolveReady();}
    if(message.serverContent){
     const content=message.serverContent;
     const changed=Boolean(content.inputTranscription||content.interimInputTranscription);
     if(changed){host.clearTimeout(quietTimer);transcript.update(content);onPreview(transcript.preview);}
     if(stopping&&flushed&&content.turnComplete)settle();
    }
   }catch(error){fail(error);}
  };
  await connected;
  if(ended)throw new Error('Dictation cancelled.');
  source=context.createMediaStreamSource(stream);
  node=new host.AudioWorkletNode(context,'blueprints-pcm');
  node.port.onmessage=event=>{
   if(ended||flushed)return;
   if(event.data.type==='flushed'){streamEnd();return;}
   if(event.data.type==='audio'){
    try{
     const bytes=new Uint8Array(event.data.buffer);
     let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);
     send({realtimeInput:{audio:{data:(host.btoa||btoa)(binary),mimeType:'audio/pcm;rate=16000'}}});
    }catch(error){fail(error);}
   }
  };
  source.connect(node);node.connect(context.destination);
  return {
   finish(){if(!ended&&!stopping){stopping=true;source.disconnect();node.port.postMessage('flush');}return done;},
   cancel:abort,
  };
 }catch(error){fail(error);throw error;}
}
