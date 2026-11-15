/** Evaluation tab: eval.py run logs (runs/*.jsonl), compared side by side and broken down. Nothing leaves the browser. */
import {$,el} from './main.js';
import {parseEvalJSONL,summarizeEval} from './contracts.js';

const runs=[];let current=0;
const pct=v=>v===null?'n/a':(v*100).toFixed(1)+'%';
const usd=v=>v===null?'n/a':v===0?'$0':'$'+v.toFixed(v<.0001?6:4);
const time=ms=>ms===null?'n/a':ms<1000?Math.round(ms)+' ms':(ms/1000).toFixed(1)+' s';
const ROUTE={parse:'CAD geometry',detect:'Detector',llm:'Vision model and agent',none:'No answer'};
function runName(file){
 const m=file.replace(/\.(jsonl?|txt)$/,'').match(/^(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)\d\d-(.+)$/);
 return m?{name:m[6],when:`${m[3]}.${m[2]}. ${m[4]}:${m[5]}`}:{name:file,when:''};
}
function table(head,rows){
 return el('table',{},el('thead',{},el('tr',{},...head.map(([text,num])=>el('th',{class:num?'num':null},text)))),el('tbody',{},...rows));
}
const bar=(label,fraction,value,note,marker=null)=>el('div',{class:'bar-row'},
 el('span',{class:'name'},label,note?el('small',{},note):''),
 el('span',{class:'track'},el('i',{style:`width:${(fraction??0)*100}%`}),marker===null?'':el('u',{style:`left:${marker*100}%`})),
 el('span',{class:'v'},value));

async function load(files){
 const problems=[],first=runs.length;
 for(const file of files){
  try{runs.push({...runName(file.name),rows:parseEvalJSONL(await file.text())});}
  catch(error){problems.push(`${file.name}: ${error.message}`);}
 }
 if(runs.length>first)current=first;
 $('run-status').textContent=problems.join(' ');
 $('run-file').value='';
 fillFilters();render();
}
function fillFilters(){
 const rows=runs[current]?.rows??[];
 for(const [id,key,all] of [['log-source','source','All sources'],['log-type','type','All types']])
  $(id).replaceChildren(new Option(all,''),...[...new Set(rows.map(r=>r[key]))].sort().map(v=>new Option(v,v)));
}
function render(){
 $('eval-empty').hidden=runs.length>0;$('eval-body').hidden=!runs.length;
 if(!runs.length)return;
 const best=Math.max(...runs.map(run=>summarizeEval(run.rows).scoredAccuracy??0));
 $('run-compare').replaceChildren(table([['Run'],['Questions',1],['Accuracy',1],['Answered',1],['Cost per question',1],['Time per question',1]],runs.map((run,i)=>{
  const s=summarizeEval(run.rows);
  return el('tr',{class:'pick','aria-selected':String(i===current),tabindex:'0',onclick:()=>{current=i;fillFilters();render();},
   onkeydown:event=>{if(event.key==='Enter'){current=i;fillFilters();render();}}},
   el('td',{},el('strong',{},run.name),run.when?el('small',{},' '+run.when):''),el('td',{class:'num'},String(s.n)),
   el('td',{class:'num'},s.scoredAccuracy===best&&runs.length>1?el('strong',{},pct(s.scoredAccuracy)):pct(s.scoredAccuracy)),
   el('td',{class:'num'},pct(s.coverage)),el('td',{class:'num'},usd(s.cost)),el('td',{class:'num'},time(s.ms)));
 })));

 const source=$('log-source').value,type=$('log-type').value;
 const rows=runs[current].rows.filter(r=>(!source||r.source===source)&&(!type||r.type===type)),s=summarizeEval(rows);
 $('metrics').replaceChildren(...[
  ['Accuracy',pct(s.scoredAccuracy),`${s.correct} of ${s.scored} scored questions`],
  ['Answered',pct(s.coverage),`${s.answered} of ${s.n} questions`],
  ['Cost per question',usd(s.cost),`${usd(s.cost===null?null:s.cost*s.n)} for all ${s.n}`],
  ['Time per question',time(s.ms),'reading the drawing excluded'],
 ].map(([label,value,detail])=>el('div',{class:'metric'},el('h3',{},label),el('strong',{},value),el('p',{},detail))));

 const groups=(key,name=v=>v)=>[...new Set(rows.map(r=>r[key]))].sort().map(group=>{
  const g=summarizeEval(rows.filter(r=>r[key]===group));
  return bar(name(group),g.scoredAccuracy,pct(g.scoredAccuracy),`${g.n}, ${usd(g.cost)} each`);
 });
 $('by-source').replaceChildren(...groups('source'));
 $('by-tier').replaceChildren(...groups('tier',v=>ROUTE[v]??v));
 $('log-reliability').replaceChildren(...[[0,.5],[.5,.8],[.8,1.01]].map(([lo,hi])=>{
  const bin=rows.filter(r=>r.correct!==null&&r.confidence>=lo&&r.confidence<hi);
  const observed=bin.length?bin.filter(r=>r.correct).length/bin.length:null,stated=bin.length?bin.reduce((n,r)=>n+r.confidence,0)/bin.length:null;
  return bar(lo===0?'Under 50% confident':hi>1?'Over 80% confident':'50 to 80% confident',observed,bin.length?pct(observed):'none',`${bin.length} answers`,stated);
 }),el('p',{class:'muted small'},'Bar: share that was correct. Tick: the confidence the system stated.'));

 const shown=rows.filter(r=>!$('log-failures').checked||r.correct===false||r.pred===null),limit=200;
 $('log-cases').replaceChildren(shown.length?table([['Question'],['Expected',1],['Answered',1],['Result'],['']],shown.slice(0,limit).map(r=>el('tr',{},
  el('td',{},el('span',{class:'q'},r.question),el('small',{},r.path)),
  el('td',{class:'num'},`${r.gold} ${r.gold_unit??''}`),el('td',{class:'num'},r.pred===null?'none':`${r.pred} ${r.pred_unit??''}`),
  el('td',{},r.pred===null?el('span',{class:'tag none',title:r.error??''},'No answer'):r.correct===null?el('span',{class:'tag none'},'Not scored'):el('span',{class:r.correct?'tag ok':'tag'},r.correct?'Correct':'Wrong')),
  el('td',{},el('button',{class:'btn quiet',type:'button',onclick:()=>{location.hash='ask';window.dispatchEvent(new CustomEvent('stoa:open-case',{detail:{path:r.path,question:r.question}}));}},'Open'))))):el('p',{class:'muted'},'Nothing to show for these filters.'),
  shown.length>limit?el('p',{class:'muted small'},`Showing the first ${limit} of ${shown.length}.`):'');
}
$('run-file').onchange=()=>load([...$('run-file').files]);
$('run-choose').onclick=()=>$('run-file').click();
for(const id of ['log-source','log-type','log-failures'])$(id).onchange=render;
// Same 1000 held-out questions: our pipeline vs three plain Gemini models. The first one listed is shown first.
const PRELOAD=['20261115-041145-pipeline.jsonl','20261115-040600-plain-gemini-4-carbon.jsonl',
 '20261115-034947-plain-gemini-4-argon.jsonl','20261115-040157-plain-gemini-4-hydrogen.jsonl'];
Promise.all(PRELOAD.map(name=>fetch('runs/'+name).then(r=>r.ok?r.text():Promise.reject()).then(text=>({name,text:()=>text}))))
 .then(files=>{if(!runs.length)load(files);}).catch(()=>{});
const panel=$('panel-evaluation');
panel.addEventListener('dragover',event=>{event.preventDefault();$('eval-empty').classList.add('over');});
panel.addEventListener('dragleave',()=>$('eval-empty').classList.remove('over'));
panel.addEventListener('drop',event=>{event.preventDefault();$('eval-empty').classList.remove('over');load([...event.dataTransfer.files]);});
