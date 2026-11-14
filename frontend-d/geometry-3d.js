const KITCHEN=/k.?che|kitchen|cuisine|cucina|keitti|kök/i;
/** Which procedural model a fixture gets. The readers only report toilet, sink, bathtub and shower; anything else is a plain block. */
export function fixtureKind(e){
 if(e.type!=='fixture')return undefined;
 if(e.subtype==='sink')return KITCHEN.test(e.attrs?.room_label??'')?'counter':'basin';
 return ['toilet','bathtub','shower'].includes(e.subtype)?e.subtype:'block';
}
/** Display-only geometry: preserves page proportions, never supplies measurements.
 *  A drawing shows its current page. A model whose levels say where their page sits in metres (an IFC model: frame_*_m and
 *  elevation_m on each level) shows every storey in one model: each entity then has its `floor` (0 is the lowest), the
 *  storey's `elevation` and `storey` height in metres, and `spanM`, the true length of the model's longest side. */
export function displayGeometry(snapshot){
 if(snapshot.mode==='pending')return [];
 const levels=snapshot.entities.filter(e=>e.type==='level'&&e.attrs?.frame_w_m>0&&e.attrs.frame_h_m>0).sort((a,b)=>(a.attrs.elevation_m??0)-(b.attrs.elevation_m??0));
 const pages=snapshot.wireScene?.pages??[],frames=new Map(levels.filter(e=>pages[e.page??0]?.width>0&&pages[e.page??0].height>0).map((e,floor)=>[e.page??0,{...e.attrs,floor,w:pages[e.page??0].width,h:pages[e.page??0].height}])),stacked=frames.size>1;  // polygons are in page pixels
 const entities=snapshot.entities.filter(e=>(stacked?frames.has(e.page??0):(e.page??0)===snapshot.pageIndex)&&Array.isArray(e.polygon)&&e.polygon.length>=3&&e.polygon.every(p=>p.length===2&&p.every(Number.isFinite)));
 if(!entities.length)return [];
 const place=e=>{const f=stacked&&frames.get(e.page??0);return f?e.polygon.map(([x,y])=>[f.frame_x_m+x/f.w*f.frame_w_m,y/f.h*f.frame_h_m-f.frame_y_m]):e.polygon;};
 const placed=entities.map(e=>[e,place(e)]),points=placed.flatMap(([,p])=>p),xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
 const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys),span=Math.max(maxX-minX,maxY-minY);
 if(!span)return [];
 const cx=(minX+maxX)/2,cy=(minY+maxY)/2,ground=stacked?Math.min(...[...frames.values()].map(f=>f.elevation_m??0)):0;
 return placed.filter(([,p])=>Math.abs(p.reduce((a,q,i)=>{const r=p[(i+1)%p.length];return a+q[0]*r[1]-r[0]*q[1];},0))>1e-8*(stacked?span*span:1)).map(([e,p])=>{
  const f=stacked?frames.get(e.page??0):null;
  return {id:e.id,label:e.label,type:e.type,kind:fixtureKind(e),m2:e.qty?.m2??(e.unit==='m²'?e.quantity:undefined),height:e.attrs?.height_m,uncertain:e.uncertain,points:p.map(([x,y])=>[(x-cx)*10/span,(y-cy)*10/span]),depth:e.type==='room'?.65:.045,
   ...(f?{floor:f.floor,elevation:(f.elevation_m??0)-ground,storey:f.height_m,spanM:span}:{})};
 });
}

// ───────── placement: everything below works in scene units (the footprint's longest side is 10) ─────────
/** Wall thickness: fixed at about 15 cm for the footprint size the drawing starts with, never more than .09 units.
 *  Room polygons are inside faces, so each room grows its walls outward and neighbours meet in the gap between them. */
export const WALL=.09;
export const wallThickness=span=>Math.min(WALL,Math.max(.03,1.5/span));
const signedArea=p=>p.reduce((a,[x,z],i)=>{const [u,v]=p[(i+1)%p.length];return a+x*v-u*z;},0)/2;
/** A room polygon's edges with their unit direction and outward unit normal; `room` says whose they are. */
export function roomEdges(points,room){
 const s=Math.sign(signedArea(points))||1;
 return points.map((a,i)=>{const b=points[(i+1)%points.length],length=Math.hypot(b[0]-a[0],b[1]-a[1]);return {a,b,length,dx:(b[0]-a[0])/length,dz:(b[1]-a[1])/length};})
  .filter(e=>e.length>1e-9).map(e=>({...e,nx:s*e.dz,nz:-s*e.dx,turn:s,room}));
}
/** Boxes just outside a room, edge by edge. A wall runs on past a convex corner to close it and stops at a concave one.
 *  `cuts` maps an edge to the openings in it ({from, to, type} along the edge): the wall is split there and the gap comes
 *  back as a piece whose `part` is the opening's type, for the lintel above it and the sill wall below a window. */
export function roomWalls(edges,t=WALL,cuts=new Map()){
 const convex=edges.map((e,i)=>{const p=edges[(i||edges.length)-1];return e.turn*(p.dx*e.dz-p.dz*e.dx)>0;});
 return edges.flatMap((e,i)=>{
  const end=e.length+(convex[(i+1)%edges.length]?t:0),spans=[];let at=convex[i]?-t:0;
  for(const cut of [...(cuts.get(e)??[])].sort((p,q)=>p.from-q.from)){
   if(cut.from>at+1e-6)spans.push({from:at,to:cut.from,part:'wall'});
   if(cut.to>at+1e-6){spans.push({from:Math.max(at,cut.from),to:cut.to,part:cut.type});at=cut.to;}
  }
  if(end>at+1e-6)spans.push({from:at,to:end,part:'wall'});
  return spans.map(({from,to,part})=>({x:e.a[0]+e.dx*(from+to)/2+e.nx*t/2,z:e.a[1]+e.dz*(from+to)/2+e.nz*t/2,angle:Math.atan2(e.dz,e.dx),length:to-from,part}));
 });
}
/** The box around a footprint, turned along its longest side: local x is the length, local z the width. */
export function fitFootprint(points){
 let angle=0,best=0;
 points.forEach((a,i)=>{const b=points[(i+1)%points.length],length=Math.hypot(b[0]-a[0],b[1]-a[1]);if(length>best){best=length;angle=Math.atan2(b[1]-a[1],b[0]-a[0]);}});
 const c=Math.cos(angle),s=Math.sin(angle),u=points.map(([x,z])=>x*c+z*s),v=points.map(([x,z])=>z*c-x*s);
 const u0=(Math.min(...u)+Math.max(...u))/2,v0=(Math.min(...v)+Math.max(...v))/2;
 return {x:u0*c-v0*s,z:u0*s+v0*c,angle,length:Math.max(...u)-Math.min(...u),width:Math.max(...v)-Math.min(...v)};
}
function nearest(x,z,edges){
 let best=null;
 for(const e of edges){const t=Math.max(0,Math.min(e.length,(x-e.a[0])*e.dx+(z-e.a[1])*e.dz)),px=e.a[0]+e.dx*t,pz=e.a[1]+e.dz*t,d=Math.hypot(x-px,z-pz);if(!best||d<best.d)best={d,px,pz,e};}
 return best;
}
/** Where a door or window stands and which room walls it opens: {x, z, angle, length, depth, cuts}.
 *  A CAD opening is a box filling the wall gap, its long sides on the faces of the rooms it joins, so it cuts every room
 *  edge lying along it and stands mid-gap. A box that is not a wall slot (a reader's square around a door swing) opens the
 *  nearest wall instead. Far from any room it keeps its own footprint and cuts nothing. */
export function placeOpening(points,edges,t=WALL){
 const fit=fitFootprint(points),square=fit.width>fit.length*.5,c=Math.cos(fit.angle),s=Math.sin(fit.angle);
 let hits=edges.map(edge=>{const along=(fit.x-edge.a[0])*edge.dx+(fit.z-edge.a[1])*edge.dz,out=(fit.x-edge.a[0])*edge.nx+(fit.z-edge.a[1])*edge.nz;
  return {edge,out,from:Math.max(0,along-fit.length/2),to:Math.min(edge.length,along+fit.length/2),parallel:Math.abs(c*edge.dz-s*edge.dx)<.2};})
  .filter(h=>(h.parallel||square)&&Math.abs(h.out)<=fit.width/2+.08&&h.to-h.from>fit.length*.5).sort((p,q)=>Math.abs(p.out)-Math.abs(q.out));
 if(!hits.length)return {x:fit.x,z:fit.z,angle:fit.angle,length:fit.length,depth:fit.width,cuts:[]};
 const {edge,out,from,to}=hits[0];hits=hits.filter(h=>Math.abs(h.edge.dx*edge.dz-h.edge.dz*edge.dx)<.2);
 const through=!square&&hits.some(h=>h.edge.room!==edge.room),offset=through?out:t/2;
 return {x:edge.a[0]+edge.dx*(from+to)/2+edge.nx*offset,z:edge.a[1]+edge.dz*(from+to)/2+edge.nz*offset,angle:Math.atan2(edge.dz,edge.dx),length:to-from,depth:through?fit.width:t,
  cuts:hits.map(h=>({edge:h.edge,from:h.from,to:h.to}))};
}
/** Procedural fixture models, authored on a 1 × 1 footprint (x along the length) with heights in metres; `height` is what a loaded model is scaled to.
 *  A part is [shared geometry, material, [x, y, z], [sx, sy, sz]]; `back` is the footprint point that belongs against a wall. */
const tap=(x,y,z)=>[['cyl','metal',[x,y,z],[.07,.13,.09]],['box','metal',[x,y+.1,z+.1],[.045,.025,.24]]];
export const FIXTURES={
 toilet:{height:.8,nominal:.7,back:[-.5,0],parts:[['box','tint',[-.36,0,0],[.28,.8,.82]],['bowl','tint',[.14,0,0],[.72,1,.8]],['cyl','metal',[-.36,.8,0],[.09,.012,.16]]]},
 basin:{height:.85,nominal:.55,back:[0,-.5],parts:[['rim','tint',[0,.72,0],[1,.13,1]],['pad','tint',[0,.74,0],[.88,.02,.88]],...tap(0,.85,-.42)]},
 counter:{height:.9,nominal:.8,back:[0,-.5],parts:[['rim','cabinet',[0,0,0],[1,.86,1]],['rim','tint',[0,.86,0],[1,.02,1]],['pad','tint',[0,.68,0],[.88,.02,.88]],...tap(0,.88,-.42)]},
 bathtub:{height:.56,nominal:1.7,back:[-.5,0],parts:[['rim','tint',[0,0,0],[1,.56,1]],['pad','tint',[0,.12,0],[.88,.02,.88]],['cyl','metal',[-.45,.56,0],[.035,.13,.08]],['box','metal',[-.41,.66,0],[.09,.025,.05]],['cyl','metal',[-.3,.14,0],[.03,.006,.07]]]},
 shower:{height:2.1,nominal:.9,back:[-.5,-.5],parts:[['rim','tint',[0,0,0],[1,.08,1]],['pad','tint',[0,.03,0],[.88,.02,.88]],['box','glass',[0,.08,.485],[1,1.9,.03]],['box','glass',[.485,.08,0],[.03,1.9,1]],['cyl','metal',[-.43,.08,-.43],[.035,2,.035]],['cyl','metal',[-.3,2.05,-.3],[.26,.03,.26]],['cyl','metal',[0,.05,0],[.08,.006,.08]]]},
 block:{back:[0,0],parts:[['box','tint',[0,0,0],[1,.6,1]]]}
};
/** Fit a fixture to its footprint and turn its back (cistern, tap) to the nearest room wall. */
export function placeFixture(points,kind,edges=[]){
 const fit=fitFootprint(points),[bx,bz]=FIXTURES[kind].back,c=Math.cos(fit.angle),s=Math.sin(fit.angle);
 const ox=bx*fit.length*c-bz*fit.width*s,oz=bx*fit.length*s+bz*fit.width*c,distance=sign=>nearest(fit.x+sign*ox,fit.z+sign*oz,edges)?.d??0;
 return distance(-1)<distance(1)?{...fit,angle:fit.angle+Math.PI}:fit;
}

// ───────── sandbox ─────────
/** Sandbox sliders. Lengths are metres and a drawing carries none of them: every default is an assumption a person can drag. An IFC model's room heights set the wall height (estimateWallHeight). */
export const SANDBOX=[
 {key:'sun',label:'Time of day',unit:'h',min:6,max:20,step:.25,value:14},
 {key:'span',label:'Footprint, longest side',unit:'m',min:4,max:100,step:.5,value:12},
 {key:'wallHeight',label:'Wall height',unit:'m',min:1,max:4,step:.05,value:2.5},
 {key:'doorHeight',label:'Door height',unit:'m',min:1.5,max:3,step:.05,value:2.1},
 {key:'sill',label:'Window sill height',unit:'m',min:0,max:2,step:.05,value:.9},
 {key:'head',label:'Window head height',unit:'m',min:1,max:3,step:.05,value:2.1},
 // Only for a model with several storeys (the renderer hides them otherwise and sets the floor count).
 {key:'floors',label:'Floors shown',unit:'floors',min:1,max:1,step:1,value:1},
 {key:'gap',label:'Floor spacing',unit:'×',min:1,max:3,step:.1,value:1}
];
export const sandboxDefaults=()=>Object.fromEntries(SANDBOX.map(s=>[s.key,s.value]));
/** Metres to scene units. Openings never rise above the wall; fixtures are modelled in metres, so their height is the scale itself, and they stand on the floor slab. */
export function sandboxExtents(d){
 const k=10/d.span,top=Math.min(d.head,d.wallHeight),sill=Math.min(d.sill,top-.1);
 return {wallHeight:d.wallHeight*k,room:{base:0,height:.02},door:{base:0,height:Math.min(d.doorHeight,d.wallHeight)*k},window:{base:sill*k,height:(top-sill)*k},
  fixture:{base:.02,height:k},other:{base:0,height:.01},wall:{base:0,height:d.wallHeight*k},column:{base:0,height:d.wallHeight*k},k};
}
/** A starting footprint size in metres: from room areas when the scene reports them, else from how big a toilet or bathtub usually is, else the slider default. */
export function estimateSpan(geometry){
 const rooms=geometry.filter(e=>e.type==='room'&&e.m2>0),ratios=geometry.filter(e=>FIXTURES[e.kind]?.nominal).map(e=>FIXTURES[e.kind].nominal/fitFootprint(e.points).length).sort((a,b)=>a-b);
 const perUnit=rooms.length?Math.sqrt(rooms.reduce((a,e)=>a+e.m2,0)/rooms.reduce((a,e)=>a+Math.abs(signedArea(e.points)),0)):ratios[ratios.length>>1];
 const {min,max,step,value}=SANDBOX[1];
 if(geometry[0]?.spanM>0)return Math.round(geometry[0].spanM*10)/10;  // a stacked model is already in metres
 return perUnit>0?Math.min(max,Math.max(min,Math.round(perUnit*10/step)*step)):value;
}
/** A starting wall height in metres: the median room height when the scene reports one (IFC models do), else the slider default. */
export function estimateWallHeight(geometry){
 const heights=geometry.filter(e=>e.type==='room'&&e.height>0).map(e=>e.height).sort((a,b)=>a-b),{min,max,step,value}=SANDBOX[2];
 return heights.length?Math.min(max,Math.max(min,Math.round(heights[heights.length>>1]/step)*step)):value;
}
/** Where the sun stands at an hour of the day: east at 6, high in the south at 13, west at 20. A unit-ish direction, y up. */
export function sunPosition(hour){
 const azimuth=Math.PI*Math.min(1,Math.max(0,(hour-6)/14)),elevation=(10+55*Math.sin(azimuth))*Math.PI/180;
 return [Math.cos(azimuth)*Math.cos(elevation),Math.sin(elevation),.6*Math.cos(elevation)];
}
