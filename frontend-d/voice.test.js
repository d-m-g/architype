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
