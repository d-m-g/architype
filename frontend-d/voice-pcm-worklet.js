/** Streaming 16 kHz mono PCM encoder. Exported for offline format/resampling checks. */
export class PCMEncoder {
 constructor(rate,send,frames=1600){
  this.rate=rate;this.send=send;this.frames=frames;this.phase=0;this.sum=0;this.samples=0;
  this.buffer=new ArrayBuffer(frames*2);this.view=new DataView(this.buffer);this.used=0;
 }
 push(input){
  for(const value of input){
   this.sum+=value;this.samples++;this.phase+=16000;
   if(this.phase>=this.rate){
    this.phase-=this.rate;
    const sample=Math.max(-1,Math.min(1,this.sum/this.samples));this.sum=0;this.samples=0;
    this.view.setInt16(this.used*2,Math.round(sample*(sample<0?32768:32767)),true);this.used++;
    if(this.used===this.frames)this.flush();
   }
  }
 }
 flush(){
  if(!this.used)return;
  const chunk=this.buffer.slice(0,this.used*2);this.used=0;this.send(chunk);
 }
}

if(typeof registerProcessor==='function'){
 registerProcessor('blueprints-pcm',class extends AudioWorkletProcessor {
  constructor(){
   super();this.encoder=new PCMEncoder(sampleRate,buffer=>this.port.postMessage({type:'audio',buffer},[buffer]));
   this.stopped=false;
   this.port.onmessage=event=>{
    if(event.data==='flush'){this.stopped=true;this.encoder.flush();this.port.postMessage({type:'flushed'});}
   };
  }
  process(inputs){
   if(!this.stopped&&inputs[0]?.[0])this.encoder.push(inputs[0][0]);
   return true; // Output stays silent; microphone audio is never played back.
  }
 });
}
