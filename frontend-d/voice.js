import {subscribeEditor} from './editor-bridge.js';

export function spokenAnswer(snapshot) {
  const answer = snapshot?.answer;
  if (!answer) return '';
  const prefix = snapshot.mode === 'mock' ? 'Mock demonstration. ' : 'Reported answer. ';
  if (answer.answer == null) return prefix + 'No verified answer. ' + (answer.error || answer.explanation || answer.status || 'Not enough evidence.');
  const confidence = Number.isFinite(answer.confidence)
    ? 'Reported confidence ' + Math.round(answer.confidence * 100) + ' percent, not necessarily calibrated.'
    : 'Confidence is not calibrated.';
  const ids = new Set(answer.evidence || []);
  const uncertain = (snapshot.entities || []).filter(e => ids.has(e.id) && (e.uncertain || e.conf < .7)).length;
  return prefix + answer.answer + ' ' + (answer.unit || '') + '. ' + confidence + ' ' + uncertain + ' evidence items need review.';
}

export function mountVoice(root = document, host = window) {
  const get = id => root.getElementById(id);
  const question = get('question'), dictate = get('voice-dictate'), read = get('voice-read');
  const stop = get('voice-stop'), language = get('voice-language'), status = get('voice-status');
  const inputState = get('voice-state'), preview = get('voice-preview'), transcript = get('voice-transcript');
  const Recognition = host.SpeechRecognition || host.webkitSpeechRecognition;
  const synthesis = host.speechSynthesis;
  let recognition = null, latest = '', speaking = false, activeUtterance = null, originalDraft = '';
  const say = text => { status.textContent = text; };
  const phase = value => {
    inputState.textContent = {ready:'Ready',listening:'Listening',transcribing:'Transcribing',review:'Review question',unavailable:'Unavailable'}[value];
    inputState.setAttribute('data-state',value);
  };
  const resetInput = () => {
    dictate.textContent = 'Dictate question'; dictate.setAttribute('aria-pressed', 'false'); language.disabled = !Recognition;
    dictate.setAttribute('aria-label','Start dictation'); dictate.title='Start dictation';
  };
  const cancelInput = (next='ready') => {
    const old = recognition; recognition = null; old?.abort(); resetInput();
    preview.hidden=true;transcript.textContent='';phase(Recognition?next:'unavailable');
  };
  const cancelOutput = () => { const wasSpeaking = speaking; activeUtterance = null; speaking = false; stop.disabled = true; if (wasSpeaking) synthesis?.cancel(); };
  dictate.disabled = !Recognition;
  language.disabled = !Recognition;
  read.disabled = !synthesis || !host.SpeechSynthesisUtterance;
  phase(Recognition?'ready':'unavailable');
  say(Recognition ? '' : 'Dictation is unavailable in this browser. You can still type your question.');
  dictate.onclick = () => {
    if (recognition) { cancelInput(); say('Dictation stopped. Your question is unchanged.'); return; }
    cancelOutput();
    const session = new Recognition();
    recognition = session;
    const original = question.value;
    originalDraft = original;
    session.lang = language.value;
    session.continuous = false;
    session.interimResults = true;
    language.disabled = true;
    dictate.textContent = 'Cancel dictation';
    dictate.setAttribute('aria-pressed', 'true');
    dictate.setAttribute('aria-label','Cancel dictation');dictate.title='Cancel dictation';
    preview.hidden=false;transcript.textContent='Your words will appear here as you speak.';
    phase('listening');
    say('Listening… Speak your question.');
    const transcribing=()=>{if(recognition===session){phase('transcribing');say('Finishing your question...');}};
    session.onspeechend=transcribing;session.onaudioend=transcribing;
    session.onresult = event => {
      if (recognition !== session) return;
      if (question.value !== original) { cancelInput();say('Your question changed while listening. Dictation was discarded.');return; }
      const results=Array.from(event.results),words=results.map(result=>result[0]?.transcript??'').join(' ').trim();
      if(words){transcript.textContent=words;preview.hidden=false;}
      if(results.length&&results.every(result=>result.isFinal)&&words){
        cancelInput('review');question.value=words;question.dispatchEvent(new Event('input',{bubbles:true}));
        say('Question transcribed. Review it, then choose Ask.');question.focus?.({preventScroll:true});
      }
    };
    session.onerror = event => {
      if (recognition !== session) return;
      const messages = {'not-allowed':'Microphone permission was denied. Enable it in your browser or type your question.', 'audio-capture':'No microphone is available. Type your question instead.', 'no-speech':'No speech detected. Try again.', 'network':'Speech service could not connect. Type your question or try again.'};
      say(messages[event.error] || 'Dictation could not finish. Try again or type your question.');
      cancelInput();
    };
    session.onend = () => { if (recognition === session) { recognition = null; resetInput(); preview.hidden=true;transcript.textContent='';phase('ready');say('Listening ended without a final transcript. Your question is unchanged. Try again or type it.'); } };
    try { session.start(); } catch { cancelInput(); say('Dictation could not start. Type your question or try again.'); }
  };
  question.addEventListener('input',()=>{
    if(recognition&&question.value!==originalDraft){cancelInput();say('Dictation stopped because you edited the question. Your edit is kept.');}
  });
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
  host.addEventListener('stoa:drawing-reset', leave);
  root.addEventListener('visibilitychange', () => { if (root.hidden) leave(); });
  get('ask-form').addEventListener('submit', () => cancelInput());
  return unsubscribe;
}
if (typeof document !== 'undefined') mountVoice();
