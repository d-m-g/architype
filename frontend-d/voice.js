import {subscribeEditor} from './editor-bridge.js';

const WORDS={
 'fi':{mock:'Esittelyesimerkki. ',reported:'Raportoitu vastaus. ',none:'Ei vahvistettua vastausta. ',missing:'Todisteita ei ole riittävästi.',confidence:n=>`Raportoitu luottamus ${n} prosenttia, ei välttämättä kalibroitu.`,uncalibrated:'Luottamusta ei ole kalibroitu.',review:n=>`${n} havaintoa tarvitsee tarkistuksen.`},
 'sv':{mock:'Demonstration. ',reported:'Rapporterat svar. ',none:'Inget verifierat svar. ',missing:'Otillräckligt underlag.',confidence:n=>`Rapporterad säkerhet ${n} procent, inte nödvändigtvis kalibrerad.`,uncalibrated:'Säkerheten är inte kalibrerad.',review:n=>`${n} observationer behöver granskas.`},
 'de':{mock:'Demonstration. ',reported:'Gemeldete Antwort. ',none:'Keine bestätigte Antwort. ',missing:'Nicht genügend Belege.',confidence:n=>`Gemeldete Sicherheit ${n} Prozent, nicht unbedingt kalibriert.`,uncalibrated:'Die Sicherheit ist nicht kalibriert.',review:n=>`${n} Erkennungen müssen geprüft werden.`},
 'fr':{mock:'Démonstration. ',reported:'Réponse rapportée. ',none:'Aucune réponse vérifiée. ',missing:'Preuves insuffisantes.',confidence:n=>`Confiance rapportée de ${n} pour cent, pas nécessairement calibrée.`,uncalibrated:'La confiance n’est pas calibrée.',review:n=>`${n} détections nécessitent une vérification.`},
 'it':{mock:'Dimostrazione. ',reported:'Risposta riportata. ',none:'Nessuna risposta verificata. ',missing:'Prove insufficienti.',confidence:n=>`Confidenza riportata del ${n} per cento, non necessariamente calibrata.`,uncalibrated:'La confidenza non è calibrata.',review:n=>`${n} rilevamenti richiedono una verifica.`},
 'es':{mock:'Demostración. ',reported:'Respuesta indicada. ',none:'No hay respuesta verificada. ',missing:'No hay pruebas suficientes.',confidence:n=>`Confianza indicada del ${n} por ciento, no necesariamente calibrada.`,uncalibrated:'La confianza no está calibrada.',review:n=>`${n} detecciones necesitan revisión.`},
};
export function spokenAnswer(snapshot,language='en-GB') {
  const answer = snapshot?.answer;
  if (!answer) return '';
  const words=WORDS[language.split('-')[0]];
  if(words){
    const prefix=snapshot.mode==='mock'?words.mock:words.reported;
    if(answer.answer==null)return prefix+words.none+(answer.error||answer.explanation||words.missing);
    const ids=new Set(answer.evidence||[]);
    const uncertain=(snapshot.entities||[]).filter(e=>ids.has(e.id)&&(e.uncertain||e.conf<.7)).length;
    return prefix+answer.answer+' '+(answer.unit||'')+'. '+(Number.isFinite(answer.confidence)?words.confidence(Math.round(answer.confidence*100)):words.uncalibrated)+' '+words.review(uncertain);
  }
  const prefix = snapshot.mode === 'mock' ? 'Mock demonstration. ' : 'Reported answer. ';
  if (answer.answer == null) return prefix + 'No verified answer. ' + (answer.error || answer.explanation || answer.status || 'Not enough evidence.');
  const confidence = Number.isFinite(answer.confidence)
    ? 'Reported confidence ' + Math.round(answer.confidence * 100) + ' percent, not necessarily calibrated.'
    : 'Confidence is not calibrated.';
  const ids = new Set(answer.evidence || []);
  const uncertain = (snapshot.entities || []).filter(e => ids.has(e.id) && (e.uncertain || e.conf < .7)).length;
  return prefix + answer.answer + ' ' + (answer.unit || '') + '. ' + confidence + ' ' + uncertain + ' evidence items need review.';
}

export function mountBrowserVoice(root = document, host = window) {
  const get = id => root.getElementById(id);
  const question = get('question'), dictate = get('voice-dictate'), read = get('voice-read');
  const stop = get('voice-stop'), language = get('voice-language'), status = get('voice-status');
  const Recognition = host.SpeechRecognition || host.webkitSpeechRecognition;
  const synthesis = host.speechSynthesis;
  let recognition = null, latest = '', speaking = false, activeUtterance = null;
  const say = text => { status.textContent = text; };
  const resetInput = () => { dictate.textContent = 'Dictate question'; dictate.setAttribute('aria-pressed', 'false'); language.disabled = false; };
  const cancelInput = () => { const old = recognition; recognition = null; old?.abort(); resetInput(); };
  const cancelOutput = () => { const wasSpeaking = speaking; activeUtterance = null; speaking = false; stop.disabled = true; if (wasSpeaking) synthesis?.cancel(); };
  dictate.disabled = !Recognition;
  read.disabled = !synthesis || !host.SpeechSynthesisUtterance;
  say(Recognition ? '' : 'Dictation is unavailable in this browser. You can still type your question.');
  dictate.onclick = () => {
    if (recognition) { cancelInput(); say('Dictation stopped. Your question is unchanged.'); return; }
    cancelOutput();
    const session = new Recognition();
    recognition = session;
    const original = question.value;
    session.lang = language.value;
    session.continuous = false;
    session.interimResults = false;
    language.disabled = true;
    dictate.textContent = 'Cancel dictation';
    dictate.setAttribute('aria-pressed', 'true');
    say('Listening… Speak your question.');
    session.onresult = event => {
      if (recognition !== session) return;
      const transcript = Array.from(event.results).filter(result => result.isFinal).map(result => result[0].transcript).join(' ').trim();
      if (question.value !== original) { say('Your question changed while listening. Dictation was discarded.'); }
      else if (transcript) { question.value = transcript; question.dispatchEvent(new Event('input', {bubbles:true})); say('Question transcribed. Review it, then inspect evidence.'); }
      else say('No speech recognised. Try again or type your question.');
      cancelInput();
    };
    session.onerror = event => {
      if (recognition !== session) return;
      const messages = {'not-allowed':'Microphone permission was denied. Enable it in your browser or type your question.', 'audio-capture':'No microphone is available. Type your question instead.', 'no-speech':'No speech detected. Try again.', 'network':'Speech service could not connect. Type your question or try again.'};
      say(messages[event.error] || 'Dictation could not finish. Try again or type your question.');
      cancelInput();
    };
    session.onend = () => { if (recognition === session) { recognition = null; resetInput(); say('Listening ended. Try again or type your question.'); } };
    try { session.start(); } catch { cancelInput(); say('Dictation could not start. Type your question or try again.'); }
  };
  read.onclick = () => {
    cancelInput(); cancelOutput();
    const utterance = new host.SpeechSynthesisUtterance(latest);
    // Answer templates are English; the language picker controls input only.
    utterance.lang = 'en-GB';
    activeUtterance = utterance; speaking = true; stop.disabled = false;
    utterance.onend = () => { if (activeUtterance !== utterance) return; activeUtterance = null; speaking = false; stop.disabled = true; say('Finished reading.'); };
    utterance.onerror = () => { if (activeUtterance !== utterance) return; activeUtterance = null; speaking = false; stop.disabled = true; say('Reading stopped or unavailable. The answer remains on screen.'); };
    say('Reading the current answer…');
    try { synthesis.speak(utterance); } catch { cancelOutput(); say('Reading is unavailable. The answer remains on screen.'); }
  };
  stop.onclick = () => { cancelOutput(); say('Reading stopped.'); };
  const unsubscribe = subscribeEditor(snapshot => {
    const next = spokenAnswer(snapshot);
    if (next !== latest) { cancelOutput(); if (recognition) { cancelInput(); say('Drawing or answer changed. Dictation stopped.'); } latest = next; }
  });
  const leave = () => { cancelInput(); cancelOutput(); };
  host.addEventListener('pagehide', leave);
  host.addEventListener('hashchange', leave);
  root.addEventListener('visibilitychange', () => { if (root.hidden) leave(); });
  get('ask-form').addEventListener('submit', cancelInput);
  return unsubscribe;
}

async function checkedResponse(response){
 if(response.ok)return response;
 const body=await response.json().catch(()=>({}));
 throw new Error(body.detail||body.error||`Voice request failed (${response.status}).`);
}

/** Record short questions and speak exact answer summaries through our authenticated backend. */
export function mountGoogleVoice(root=document,host=window){
 const get=id=>root.getElementById(id),question=get('question'),dictate=get('voice-dictate'),read=get('voice-read');
 const stop=get('voice-stop'),language=get('voice-language'),status=get('voice-status');
 let disposed=false,session=null,playback=null,snapshot=null,source='',fallback=null,settings=null;
 const cache=new Map();
 const say=text=>{status.textContent=text;};
 const reset=()=>{dictate.textContent='Dictate question';dictate.setAttribute('aria-pressed','false');language.disabled=false;};
 const release=s=>{host.clearTimeout(s.timer);s.stream?.getTracks().forEach(track=>track.stop());};
 const cancelInput=()=>{
  const s=session;session=null;
  if(s){s.discard=true;s.controller?.abort();release(s);if(s.recorder?.state==='recording')s.recorder.stop();}
  reset();
 };
 const cancelOutput=()=>{
  const p=playback;playback=null;stop.disabled=true;
  if(p){p.controller.abort();if(p.audio){p.audio.onended=null;p.audio.onerror=null;p.audio.pause();}if(p.url)host.URL.revokeObjectURL(p.url);}
 };
 const finish=()=>{if(session?.recorder?.state==='recording'){session.recorder.stop();release(session);}};
 const unsubscribe=subscribeEditor(next=>{
  const changed=source!==(next.wireScene?.path||'')||spokenAnswer(snapshot,language.value)!==spokenAnswer(next,language.value);
  snapshot=next;source=next.wireScene?.path||'';
  read.disabled=!settings||!snapshot?.answer;
  if(changed){cancelOutput();if(session){cancelInput();say('Drawing or answer changed. Dictation stopped.');}}
 });
 dictate.disabled=read.disabled=true;
 say('Checking voice availability…');
 const configController=new AbortController();
 host.fetch('/api/voice/config',{credentials:'same-origin',signal:configController.signal})
  .then(checkedResponse).then(r=>r.json()).then(config=>{
   if(disposed)return;
   if(!config.configured)throw new Error('Google voice is not configured.');
   settings=config;dictate.disabled=false;read.disabled=!snapshot?.answer;
   dictate.title='Record a question for Google transcription (maximum 30 seconds)';
   say('Google voice ready. Record a question, then review the transcript.');
  }).catch(error=>{
   if(disposed)return;
   unsubscribe();fallback=mountBrowserVoice(root,host);
   say(`${error.message} Browser voice is available where supported.`);
  });
 dictate.onclick=async()=>{
  if(!settings)return;
  if(session){if(session.recorder?.state==='recording')finish();else{cancelInput();say('Dictation cancelled.');}return;}
  cancelOutput();
  const s={original:question.value,language:language.value,chunks:[],size:0,controller:new AbortController()};
  session=s;language.disabled=true;dictate.textContent='Cancel dictation';dictate.setAttribute('aria-pressed','true');
  say('Allow microphone access to record your question.');
  try{
   s.stream=await host.navigator.mediaDevices.getUserMedia({audio:true});
   if(session!==s){release(s);return;}
   const types=['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4'];
   const mime=types.find(type=>host.MediaRecorder.isTypeSupported(type));
   if(!mime)throw new Error('This browser cannot record a supported audio format. Type your question instead.');
   s.recorder=new host.MediaRecorder(s.stream,{mimeType:mime});
   s.recorder.ondataavailable=event=>{
    if(session!==s||!event.data.size)return;
    s.size+=event.data.size;
    if(s.size>settings.max_audio_bytes){cancelInput();say('Recording is too large. Try a shorter question.');return;}
    s.chunks.push(event.data);
   };
   s.recorder.onerror=()=>{if(session===s){cancelInput();say('Recording failed. Try again or type your question.');}};
   s.recorder.onstop=async()=>{
    release(s);if(session!==s||s.discard)return;
    dictate.textContent='Cancel transcription';say('Transcribing with Google…');
    const timer=host.setTimeout(()=>s.controller.abort(),100000);
    try{
     const audio=new (host.Blob||Blob)(s.chunks,{type:s.recorder.mimeType});
     if(!audio.size)throw new Error('No audio was recorded. Try again.');
     const response=await checkedResponse(await host.fetch('/api/voice/transcribe?language='+encodeURIComponent(s.language),{
      method:'POST',credentials:'same-origin',headers:{'Content-Type':audio.type},body:audio,signal:s.controller.signal,
     }));
     const body=await response.json();
     if(session!==s)return;
     if(question.value!==s.original)say('Your question changed while recording. Transcription was discarded.');
     else if(body.text?.trim()){
      question.value=body.text.trim();question.dispatchEvent(new Event('input',{bubbles:true}));
      say('Question transcribed. Review it, then press Ask.');
     }else say('No speech recognised. Try again or type your question.');
    }catch(error){if(session===s)say(error.name==='AbortError'?'Transcription timed out. Try a shorter question.':error.message);}
    finally{host.clearTimeout(timer);if(session===s){session=null;reset();}}
   };
   s.recorder.start(250);dictate.textContent='Finish question';
   say('Recording… Click Finish question when ready. Audio is sent to Google for transcription.');
   s.timer=host.setTimeout(finish,Math.min(30,settings.max_record_seconds||30)*1000);
  }catch(error){if(session===s){cancelInput();say(error.name==='NotAllowedError'?'Microphone permission was denied. You can still type.':error.message);}}
 };
 read.onclick=async()=>{
  if(!settings||!snapshot?.answer)return;
  cancelInput();cancelOutput();
  const text=spokenAnswer(snapshot,language.value);
  const p={controller:new AbortController()};playback=p;stop.disabled=false;say('Preparing spoken answer…');
  const timer=host.setTimeout(()=>p.controller.abort(),100000);
  try{
   let blob=cache.get(text);
   if(!blob){
    const response=await checkedResponse(await host.fetch('/api/voice/speak',{
     method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
     body:JSON.stringify({text}),signal:p.controller.signal,
    }));
    blob=await response.blob();
    if(playback!==p)return;
    cache.set(text,blob);if(cache.size>3)cache.delete(cache.keys().next().value);
   }
   if(playback!==p)return;
   p.url=host.URL.createObjectURL(blob);p.audio=new host.Audio(p.url);
   p.audio.onended=()=>{if(playback===p){cancelOutput();say('Finished reading.');}};
   p.audio.onerror=()=>{if(playback===p){cancelOutput();say('Audio playback failed. The answer remains on screen.');}};
   await p.audio.play();
   if(playback===p)say('Reading the current answer…');
  }catch(error){if(playback===p){cancelOutput();say(error.name==='AbortError'?'Speech generation timed out. Try again.':error.message);}}
  finally{host.clearTimeout(timer);}
 };
 stop.onclick=()=>{cancelOutput();say('Reading stopped.');};
 const changeLanguage=()=>{cancelInput();cancelOutput();};
 language.addEventListener('change',changeLanguage);
 const leave=()=>{cancelInput();cancelOutput();};
 const visibility=()=>{if(root.hidden)leave();};
 host.addEventListener('pagehide',leave);host.addEventListener('hashchange',leave);
 root.addEventListener('visibilitychange',visibility);get('ask-form').addEventListener('submit',cancelInput);
 return ()=>{
  disposed=true;configController.abort();leave();unsubscribe();fallback?.();
  host.removeEventListener?.('pagehide',leave);host.removeEventListener?.('hashchange',leave);
  root.removeEventListener?.('visibilitychange',visibility);language.removeEventListener?.('change',changeLanguage);
  get('ask-form').removeEventListener?.('submit',cancelInput);
 };
}

export function mountVoice(root=document,host=window){
 if(host.fetch&&host.MediaRecorder&&host.Audio&&host.navigator?.mediaDevices?.getUserMedia)return mountGoogleVoice(root,host);
 return mountBrowserVoice(root,host);
}
if (typeof document !== 'undefined') mountVoice();
