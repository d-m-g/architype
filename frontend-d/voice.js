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
if (typeof document !== 'undefined') mountVoice();
