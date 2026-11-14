/** Local-only correction state. Never executes queries or mutates reported entities. */
export const ENTITY_TYPES = ['room','apartment','level','door','window','fixture','wall','slab','column','row','other'];
const copy = value => structuredClone(value);
export function createDraft(entities) { return {entities:copy(entities), history:[]}; }
export function boxPolygon(x1,y1,x2,y2,width,height) {
  if (![x1,y1,x2,y2,width,height].every(Number.isFinite) || width<=0 || height<=0 ||
      Math.min(x1,x2)<0 || Math.min(y1,y2)<0 || Math.max(x1,x2)>width || Math.max(y1,y2)>height)
    throw new Error('Box corners must lie within the current page.');
  if (Math.abs(x1-x2)<1 || Math.abs(y1-y2)<1) throw new Error('Draw a box at least one source pixel wide and high.');
  const left=Math.min(x1,x2),top=Math.min(y1,y2),right=Math.max(x1,x2),bottom=Math.max(y1,y2);
  return [[left,top],[right,top],[right,bottom],[left,bottom]];
}
export function applyCorrection(draft, action) {
  const before=copy(draft.entities), entities=copy(before), index=entities.findIndex(e=>e.id===action.id);
  let description;
  if(action.kind==='add') {
    const e=action.entity;
    if(!e?.id || entities.some(v=>v.id===e.id) || !ENTITY_TYPES.includes(e.type) ||
       !Number.isInteger(e.page) || e.page<0 || !Array.isArray(e.polygon) || e.polygon.length!==4 ||
       !e.polygon.every(p=>p.length===2&&p.every(Number.isFinite))) throw new Error('Invalid new box.');
    entities.push(copy(e));description='Added '+(e.label||e.id)+' as '+e.type;
  } else {
    if(index<0)throw new Error('Select an existing draft item.');
    const e=entities[index], label=e.label||e.id;
    if(action.kind==='delete'){entities.splice(index,1);description='Deleted '+label;}
    else if(action.kind==='type') {
      if(!ENTITY_TYPES.includes(action.value))throw new Error('Choose a supported entity type.');
      if(e.type===action.value)return draft;
      description=label+': type '+e.type+' → '+action.value;e.type=action.value;e.subtype=null;
    } else if(action.kind==='area') {
      if(!Number.isFinite(action.value)||action.value<0||!['m2','ft2'].includes(action.unit))throw new Error('Enter a non-negative area and its unit.');
      const old=e.qty?.[action.unit]??((e.unit===(action.unit==='m2'?'m²':'ft²'))?e.quantity:undefined);
      if(old===action.value)return draft;
      description=label+': '+(old??'no value')+' → '+action.value+' '+action.unit;
      // Remove the other area unit rather than leaving two contradictory areas.
      e.qty={...(e.qty??{})};delete e.qty.m2;delete e.qty.ft2;e.qty[action.unit]=action.value;
      e.quantity=action.value;e.unit=action.unit==='m2'?'m²':'ft²';e.quantityText=action.value+' '+e.unit+' · user-entered draft';
    } else if(action.kind==='confirm') {
      if(e.reviewedByUser)return draft;
      description='Confirmed '+label+' by user; reported confidence unchanged';e.reviewedByUser=true;
    } else throw new Error('Unsupported correction.');
  }
  return {entities,history:[...draft.history,{before,description}]};
}
export function undoCorrection(draft) {
  if(!draft.history.length)return draft;
  return {entities:copy(draft.history.at(-1).before),history:draft.history.slice(0,-1)};
}
