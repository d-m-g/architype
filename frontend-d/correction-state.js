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

// ───────── a room's outline, corrected corner by corner (used by edit.js) ─────────
// Geometry only, on wire entities (geom 0..1 of the page): no page element, no model call.

/** Area and perimeter of a polygon in page pixels. geom is 0..1 of the page on each axis and a page is rarely square,
 * so the points are scaled by the page's width and height before anything is measured. */
export function measure(geom,{width,height}){
 let twice=0,perimeter=0;
 geom.forEach(([x,y],i)=>{
  const [x2,y2]=geom[(i+1)%geom.length],ax=x*width,ay=y*height,bx=x2*width,by=y2*height;
  twice+=ax*by-bx*ay;perimeter+=Math.hypot(bx-ax,by-ay);
 });
 return {area:Math.abs(twice)/2,perimeter};
}
/** What each quantity of a room grows with. Volume is floor area times a height the edit does not touch.
 * Wall area is perimeter times height minus doors and windows: the openings are held fixed here, so scaling it by the
 * perimeter is an approximation (it scales the openings too). */
const FOLLOWS={m2:'area',ft2:'area',m3:'area',m:'perimeter',wall_m2:'perimeter'};
/** The entity with a new outline, its quantities scaled by how the outline's area and perimeter changed. A room the
 * reader gave no quantity stays without one: nothing here knows the drawing's scale. */
// ponytail: a self-crossing outline is measured by the shoelace formula as it stands; reject crossings if users draw them.
export function reshaped(entity,geom,page){
 const was=measure(entity.geom,page),now=measure(geom,page);
 const qty=Object.fromEntries(Object.entries(entity.qty??{}).map(([unit,value])=>{
  const ratio=now[FOLLOWS[unit]]/was[FOLLOWS[unit]];
  // An added corner on a straight edge changes no measure, so the reader's number stays as it was written.
  return [unit,Number.isFinite(ratio)&&Math.abs(ratio-1)>1e-9?Math.round(value*ratio*100)/100:value];
 }));
 return {...entity,geom,...(entity.qty?{qty}:{})};
}
/** The edge nearest a point: {index of the edge's first corner, the point on that edge, distance to it}. */
export function nearestEdge(points,[px,py]){
 let best={distance:Infinity};
 points.forEach(([ax,ay],index)=>{
  const [bx,by]=points[(index+1)%points.length],dx=bx-ax,dy=by-ay,length=dx*dx+dy*dy;
  const t=length?Math.min(1,Math.max(0,((px-ax)*dx+(py-ay)*dy)/length)):0,point=[ax+t*dx,ay+t*dy];
  const distance=Math.hypot(px-point[0],py-point[1]);
  if(distance<best.distance)best={index,point,distance};
 });
 return best;
}
/** A new corner after corner `index`; and the outline without corner `index`, or null when that leaves fewer than 3. */
export const withCorner=(geom,index,point)=>[...geom.slice(0,index+1),point,...geom.slice(index+1)];
export const withoutCorner=(geom,index)=>geom.length>3?geom.filter((_,i)=>i!==index):null;
