/** Lane D boundary adapter for blueprints/schemas.py v1. No query execution here. */
export const units={count:'count',m2:'m²',ft2:'ft²',m:'m',m3:'m³'};
const canonical=value=>JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.entries(item).sort(([a],[b])=>a.localeCompare(b))):item);
const finite=n=>typeof n==='number'&&Number.isFinite(n);
function requireValue(condition,message){if(!condition)throw new Error(message);}
export function validateScene(scene){requireValue(scene?.schema_version===1,'Unsupported Scene schema_version (expected 1).');requireValue(typeof scene.path==='string'&&Array.isArray(scene.pages)&&Array.isArray(scene.entities),'Invalid Scene shape.');const ids=new Set();for(const p of scene.pages)requireValue(typeof p.image==='string'&&finite(p.width)&&p.width>0&&finite(p.height)&&p.height>0,'Invalid page dimensions/image.');for(const e of scene.entities){requireValue(typeof e.id==='string'&&!ids.has(e.id),'Duplicate or invalid entity ID.');ids.add(e.id);validateEntity(e,scene.pages);}return scene;}
function validateEntity(e,pages){requireValue(typeof e.id==='string','Invalid entity ID.');requireValue(Number.isInteger(e.page??0)&&(e.page??0)>=0&&(pages===null||(e.page??0)<pages.length),'Entity page index is unavailable.');requireValue(Array.isArray(e.geom??[])&&(e.geom??[]).every(p=>Array.isArray(p)&&p.length===2&&p.every(n=>finite(n)&&n>=0&&n<=1)),'Geometry must use normalized 0..1 coordinates.');requireValue(e.conf===undefined||(finite(e.conf)&&e.conf>=0&&e.conf<=1),'Invalid entity confidence.');}
const title=s=>s?s[0].toUpperCase()+s.slice(1).replace(/_/g,' '):s;
const roomName=(label,number)=>[label,number].filter(Boolean).join(' ');
/** A reader-facing name and context line built only from fields the scene reports. */
export function describeEntity(e){
 const a=e.attrs??{},room=roomName(a.room_label,a.room),room2=roomName(a.room_label2,a.room2);
 const where=[room,room2].filter(Boolean).join(' ↔ ');
 const label=e.type==='room'?roomName(e.label,e.number)||title(e.subtype)||'Room'
  :e.type==='apartment'?`Apartment ${e.label??e.id}`
  :['door','window','fixture'].includes(e.type)?[title(e.subtype??e.type),where].filter(Boolean).join(' · ')
  :e.label??e.number??e.subtype??e.id;
 const context=[e.type!=='apartment'&&e.apartment?`Apartment ${e.apartment}`:null,e.outdoor?'outdoor':null,a.copies>1?`drawn ${a.copies}× on the same spot`:null].filter(Boolean).join(' · ');
 return {label,context:context||undefined};
}
export function adaptScene(raw,answer={}){validateScene(raw);const map=new Map(raw.entities.map(e=>[e.id,e]));for(const e of answer.extra_entities??[]){validateEntity(e,raw.pages);if(map.has(e.id)){requireValue(canonical(map.get(e.id))===canonical(e),'extra_entities ID conflicts with the scene.');continue;}map.set(e.id,e);}const entities=[...map.values()].map(e=>{const page=raw.pages[e.page??0];const quantities=Object.entries(e.qty??{});return {...e,...describeEntity(e),rawLabel:e.label,page:e.page??0,polygon:(e.geom??[]).map(([x,y])=>[x*page.width,y*page.height]),sourceRef:e.ref??`Entity ${e.id} (no source reference supplied)`,method:raw.builder,uncertain:(e.conf??1)<.8,quantity:quantities[0]?.[1],unit:units[quantities[0]?.[0]],quantityText:quantities.length?quantities.map(([u,v])=>`${v} ${units[u]??u}`).join(' · '):undefined};});return {raw,pages:raw.pages,entities,width:raw.pages[0]?.width??1000,height:raw.pages[0]?.height??760,page:1,level:'Not specified'};}
/** Inline answer evidence remains inspectable without inventing page dimensions or a Scene. */
export function adaptAnswerEvidence(raw){
 const map=new Map();
 for(const e of raw.extra_entities??[]){
  validateEntity(e,null);
  if(map.has(e.id)){requireValue(canonical(map.get(e.id))===canonical(e),'Conflicting inline evidence ID.');continue;}
  map.set(e.id,e);
 }
 return [...map.values()].map(e=>{
  const quantities=Object.entries(e.qty??{});
  return {...e,page:e.page??0,label:e.label??e.number??e.subtype??e.id,polygon:[],geometryUnavailable:true,
   sourceRef:e.ref??`Entity ${e.id} (no source reference supplied)`,method:'Answer-provided evidence; source coordinates unavailable',
   uncertain:(e.conf??1)<.8,quantity:quantities[0]?.[1],unit:units[quantities[0]?.[0]],
   quantityText:quantities.length?quantities.map(([u,v])=>`${v} ${units[u]??u}`).join(' · '):undefined};
 });
}
export function adaptAnswer(raw,scene){requireValue(raw&&Array.isArray(raw.evidence),'Invalid Answer evidence.');requireValue(raw.answer===null||['string','number'].includes(typeof raw.answer),'Invalid Answer value.');requireValue(['parse','detect','llm','none'].includes(raw.tier),'Invalid Answer tier.');requireValue(finite(raw.confidence)&&raw.confidence>=0&&raw.confidence<=1,'Invalid Answer confidence.');const missing=[...new Set(raw.evidence)].filter(id=>!scene.entities.some(e=>e.id===id));return {...raw,raw,unit:units[raw.unit]??raw.unit,status:missing.length?'Unresolved evidence':raw.answer===null?'Insufficient evidence':`Reported · ${raw.tier}`,scope:'Not specified by Answer',explanation:[raw.error,raw.interpretation,missing.length?`Missing evidence IDs: ${missing.join(', ')}`:null].filter(Boolean).join(' · ')||'Reported answer; inspect the contributing evidence.',missing,confidenceText:`${(raw.confidence*100).toFixed(1)}% · reported, uncalibrated`};}
export function parseEvalJSONL(text){return text.replace(/^\uFEFF/,'').split(/\r?\n/).filter(l=>l.trim()).map((l,i)=>{let r;try{r=JSON.parse(l);}catch{throw new Error(`Invalid JSON on row ${i+1}.`);}requireValue(typeof r.path==='string'&&typeof r.question==='string'&&typeof r.source==='string'&&typeof r.type==='string',`Invalid EvalRow identity on row ${i+1}.`);requireValue(r.correct===null||typeof r.correct==='boolean',`Invalid correctness on row ${i+1}.`);requireValue(finite(r.confidence)&&r.confidence>=0&&r.confidence<=1&&finite(r.cost_usd)&&r.cost_usd>=0&&finite(r.ms)&&r.ms>=0,`Invalid metrics on row ${i+1}.`);requireValue(['parse','detect','llm','none'].includes(r.tier)&&Object.hasOwn(r,'pred'),`Invalid prediction/tier on row ${i+1}.`);return r;});}
export function summarizeEval(rows){const answered=rows.filter(r=>r.pred!==null),scored=rows.filter(r=>r.correct!==null),answeredScored=answered.filter(r=>r.correct!==null),correct=scored.filter(r=>r.correct===true).length;return {n:rows.length,answered:answered.length,scored:scored.length,unscored:rows.length-scored.length,correct,accuracy:answeredScored.length?answeredScored.filter(r=>r.correct).length/answeredScored.length:null,scoredAccuracy:scored.length?correct/scored.length:null,coverage:rows.length?answered.length/rows.length:null,cost:rows.length?rows.reduce((n,r)=>n+r.cost_usd,0)/rows.length:null,ms:rows.length?rows.reduce((n,r)=>n+r.ms,0)/rows.length:null};}
export function imageURL(path){return `/api/image?path=${encodeURIComponent(path)}`;}

/** Overview uses only this page and distinguishes missing coverage from a zero. */
export function summarizeScene(raw,pageIndex=0){
 const entities=raw.entities.filter(e=>(e.page??0)===pageIndex),covers=new Set(raw.covers);
 const of=type=>entities.filter(e=>e.type===type),rooms=of('room');
 const fullRooms=covers.has('room'),someRooms=fullRooms||raw.covers.some(key=>key.startsWith('room:'));
 const answer=fullRooms?`${rooms.length} detected room${rooms.length===1?'':'s'}`:someRooms||rooms.length?`${rooms.length} detected rooms (partial coverage)`:'Room detection unavailable';
 const counts=['apartment','door','window'].filter(type=>covers.has(type)).map(type=>{const n=of(type).length;return `${n} detected ${type}${n===1?'':'s'}`;});
 for(const type of ['toilet','sink','bathtub','shower'])if(covers.has('fixture')||covers.has('fixture:'+type)){const n=of('fixture').filter(e=>e.subtype===type).length;counts.push(`${n} detected ${type}${n===1?'':'s'}`);}
 const indoor=rooms.filter(e=>!e.outdoor),measured=indoor.filter(e=>Number.isFinite(e.qty?.m2)&&e.qty.m2>=0);
 const total=measured.reduce((sum,e)=>sum+e.qty.m2,0),complete=fullRooms&&measured.length===indoor.length;
 const formula=measured.length?`${complete?'Reported':'Partial reported'} indoor room area: ${total.toFixed(2)} m2 (${measured.length} of ${indoor.length} detected indoor rooms have areas${fullRooms?'':'; room detection coverage is incomplete'})`:null;
 return {answer,formula,evidence:rooms.map(e=>e.id),explanation:[counts.length?counts.join(', ')+'.':'No covered object totals available.',!someRooms?'The reader does not report room coverage.':!fullRooms?'Room totals cover only the supported room categories.':'Counts describe the reader detections, not independently verified quantities.',indoor.length&&!measured.length?'Indoor room areas are unavailable.':null,'Click a detected area to inspect its source; ask a question for an answer.'].filter(Boolean).join(' ')};
}
