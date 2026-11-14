import test from 'node:test';
import assert from 'node:assert/strict';
import {spokenAnswer, mountVoice} from './voice.js';
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
