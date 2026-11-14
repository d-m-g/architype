import {historyStore} from './history-state.js';
const $=id=>document.getElementById(id);
let browsing=null,lastCurrent=null;
function render() {
  const current=historyStore.current();
  if(current!==lastCurrent){browsing=current;lastCurrent=current;}
  const groups=historyStore.groups(),picker=$('history-drawing');
  picker.replaceChildren();
  for(const group of groups){const option=document.createElement('option');option.value=group.key;option.textContent=group.label+' · '+group.count;picker.append(option);}
  picker.value=browsing??'';
  const entries=historyStore.list(browsing);$('history-list').replaceChildren();
  $('history-empty').hidden=entries.length>0;
  $('history-summary').textContent=entries.length+' saved result'+(entries.length===1?'':'s')+' · newest first';
  for(const entry of entries) {
    const item=document.createElement('li'),heading=document.createElement('p'),meta=document.createElement('p'),answer=document.createElement('p'),actions=document.createElement('div');
    heading.className='history-question';heading.textContent=entry.question??'Imported answer · question not supplied';
    const r=entry.payload.result;
    meta.className='muted small';
    const confidence=Number.isFinite(r.confidence)?Math.round(r.confidence*100)+'% reported confidence':'Confidence unavailable / uncalibrated';
    const cost=entry.origin==='mock'?'No model call':Number.isFinite(r.cost_usd)?'$'+r.cost_usd.toFixed(4)+' reported query cost':'Cost unavailable';
    meta.textContent=entry.origin.toUpperCase()+' · '+new Date(entry.createdAt).toLocaleString()+' · '+confidence+' · '+cost;
    answer.textContent=(r.answer===null?'No answer':String(r.answer)+' '+(r.unit??''))+' · '+r.status;
    const open=document.createElement('button');open.type='button';open.textContent='Open saved answer';
    open.setAttribute('aria-pressed',String(historyStore.viewed()?.key===browsing&&historyStore.viewed()?.id===entry.id));
    const key=browsing;
    open.onclick=()=>window.dispatchEvent(new CustomEvent('stoa:restore-answer',{detail:{key,id:entry.id}}));
    const reuse=document.createElement('button');reuse.type='button';reuse.textContent='Reuse question';reuse.disabled=entry.question===null;
    reuse.onclick=()=>window.dispatchEvent(new CustomEvent('stoa:restore-answer',{detail:{key,id:entry.id,reuse:true}}));
    actions.append(open,reuse);item.append(heading,meta,answer,actions);$('history-list').append(item);
  }
}
$('history-drawing').onchange=()=>{browsing=$('history-drawing').value;render();};
historyStore.subscribe(render);
