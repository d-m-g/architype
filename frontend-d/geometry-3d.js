const KITCHEN=/k.?che|kitchen|cuisine|cucina|keitti|kök/i;
/** Which procedural model a fixture gets. The readers only report toilet, sink, bathtub and shower; anything else is a plain block. */
export function fixtureKind(e){
 if(e.type!=='fixture')return undefined;
 if(e.subtype==='sink')return KITCHEN.test(e.attrs?.room_label??'')?'counter':'basin';
 return ['toilet','bathtub','shower'].includes(e.subtype)?e.subtype:'block';
}
/** Display-only geometry: preserves page proportions, never supplies measurements. */
export function displayGeometry(snapshot){
 if(snapshot.mode==='pending')return [];
 const entities=snapshot.entities.filter(e=>(e.page??0)===snapshot.pageIndex&&Array.isArray(e.polygon)&&e.polygon.length>=3&&e.polygon.every(p=>p.length===2&&p.every(Number.isFinite)));
 if(!entities.length)return [];
 const points=entities.flatMap(e=>e.polygon),xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
 const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys),span=Math.max(maxX-minX,maxY-minY);
 if(!span)return [];
 const cx=(minX+maxX)/2,cy=(minY+maxY)/2;
 return entities.filter(e=>Math.abs(e.polygon.reduce((a,p,i)=>{const q=e.polygon[(i+1)%e.polygon.length];return a+p[0]*q[1]-q[0]*p[1];},0))>1e-8).map(e=>({id:e.id,label:e.label,type:e.type,kind:fixtureKind(e),m2:e.qty?.m2??(e.unit==='m²'?e.quantity:undefined),uncertain:e.uncertain,points:e.polygon.map(([x,y])=>[(x-cx)*10/span,(y-cy)*10/span]),depth:e.type==='room'?.65:.045}));
}

// ───────── placement: everything below works in scene units (the footprint's longest side is 10) ─────────
/** Wall thickness: fixed at about 15 cm for the footprint size the drawing starts with, never more than .09 units.
 *  Room polygons are inside faces, so each room grows its walls outward and neighbours meet in the gap between them. */
export const WALL=.09;
export const wallThickness=span=>Math.min(WALL,Math.max(.03,1.5/span));
const signedArea=p=>p.reduce((a,[x,z],i)=>{const [u,v]=p[(i+1)%p.length];return a+x*v-u*z;},0)/2;
/** A room polygon's edges with their unit direction and outward unit normal. */
export function roomEdges(points){
 const s=Math.sign(signedArea(points))||1;
 return points.map((a,i)=>{const b=points[(i+1)%points.length],length=Math.hypot(b[0]-a[0],b[1]-a[1]);return {a,b,length,dx:(b[0]-a[0])/length,dz:(b[1]-a[1])/length};})
  .filter(e=>e.length>1e-9).map(e=>({...e,nx:s*e.dz,nz:-s*e.dx,turn:s}));
}
/** One box per edge, just outside the room. A wall runs on past a convex corner to close it and stops at a concave one. */
export function roomWalls(points,t=WALL){
 const edges=roomEdges(points),convex=edges.map((e,i)=>{const p=edges[(i||edges.length)-1];return e.turn*(p.dx*e.dz-p.dz*e.dx)>0;});
 return edges.map((e,i)=>{const start=convex[i]?t:0,end=convex[(i+1)%edges.length]?t:0,shift=(end-start)/2;
  return {x:(e.a[0]+e.b[0])/2+e.dx*shift+e.nx*t/2,z:(e.a[1]+e.b[1])/2+e.dz*shift+e.nz*t/2,angle:Math.atan2(e.dz,e.dx),length:e.length+start+end};});
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
/** A door or window becomes a panel standing in the nearest room wall, a little thicker so it shows on both faces. Far from any room it keeps its own footprint. */
export function placeOpening(points,edges,t=WALL){
 const fit=fitFootprint(points),near=nearest(fit.x,fit.z,edges);
 if(!near||near.d>.35)return fit;
 return {x:near.px+near.e.nx*t/2,z:near.pz+near.e.nz*t/2,angle:Math.atan2(near.e.dz,near.e.dx),length:fit.length,width:t+.03};
}
/** Fixture models, authored on a 1 × 1 footprint (x along the length) with heights in metres.
 *  A part is [shared geometry, material, [x, y, z], [sx, sy, sz]]; `back` is the footprint point that belongs against a wall. */
const tap=(x,y,z)=>[['cyl','metal',[x,y,z],[.07,.13,.09]],['box','metal',[x,y+.1,z+.1],[.045,.025,.24]]];
export const FIXTURES={
 toilet:{nominal:.7,back:[-.5,0],parts:[['box','tint',[-.36,0,0],[.28,.8,.82]],['bowl','tint',[.14,0,0],[.72,1,.8]],['cyl','metal',[-.36,.8,0],[.09,.012,.16]]]},
 basin:{nominal:.55,back:[0,-.5],parts:[['rim','tint',[0,.72,0],[1,.13,1]],['pad','tint',[0,.74,0],[.88,.02,.88]],...tap(0,.85,-.42)]},
 counter:{nominal:.8,back:[0,-.5],parts:[['rim','cabinet',[0,0,0],[1,.86,1]],['rim','tint',[0,.86,0],[1,.02,1]],['pad','tint',[0,.68,0],[.88,.02,.88]],...tap(0,.88,-.42)]},
 bathtub:{nominal:1.7,back:[-.5,0],parts:[['rim','tint',[0,0,0],[1,.56,1]],['pad','tint',[0,.12,0],[.88,.02,.88]],['cyl','metal',[-.45,.56,0],[.035,.13,.08]],['box','metal',[-.41,.66,0],[.09,.025,.05]],['cyl','metal',[-.3,.14,0],[.03,.006,.07]]]},
 shower:{nominal:.9,back:[-.5,-.5],parts:[['rim','tint',[0,0,0],[1,.08,1]],['pad','tint',[0,.03,0],[.88,.02,.88]],['box','glass',[0,.08,.485],[1,1.9,.03]],['box','glass',[.485,.08,0],[.03,1.9,1]],['cyl','metal',[-.43,.08,-.43],[.035,2,.035]],['cyl','metal',[-.3,2.05,-.3],[.26,.03,.26]],['cyl','metal',[0,.05,0],[.08,.006,.08]]]},
 block:{back:[0,0],parts:[['box','tint',[0,0,0],[1,.6,1]]]}
};
/** Fit a fixture to its footprint and turn its back (cistern, tap) to the nearest room wall. */
export function placeFixture(points,kind,edges=[]){
 const fit=fitFootprint(points),[bx,bz]=FIXTURES[kind].back,c=Math.cos(fit.angle),s=Math.sin(fit.angle);
 const ox=bx*fit.length*c-bz*fit.width*s,oz=bx*fit.length*s+bz*fit.width*c,distance=sign=>nearest(fit.x+sign*ox,fit.z+sign*oz,edges)?.d??0;
 return distance(-1)<distance(1)?{...fit,angle:fit.angle+Math.PI}:fit;
}

// ───────── sandbox ─────────
/** Sandbox sliders. Lengths are metres and the drawing carries none of them: every default is an assumption a person can drag. */
export const SANDBOX=[
 {key:'sun',label:'Time of day',unit:'h',min:6,max:20,step:.25,value:14},
 {key:'span',label:'Footprint, longest side',unit:'m',min:4,max:100,step:.5,value:12},
 {key:'wallHeight',label:'Wall height',unit:'m',min:1,max:4,step:.05,value:2.5},
 {key:'doorHeight',label:'Door height',unit:'m',min:1.5,max:3,step:.05,value:2.1},
 {key:'sill',label:'Window sill height',unit:'m',min:0,max:2,step:.05,value:.9},
 {key:'head',label:'Window head height',unit:'m',min:1,max:3,step:.05,value:2.1}
];
export const sandboxDefaults=()=>Object.fromEntries(SANDBOX.map(s=>[s.key,s.value]));
/** Metres to scene units. Openings never rise above the wall; fixtures are modelled in metres, so their height is the scale itself, and they stand on the floor slab. */
export function sandboxExtents(d){
 const k=10/d.span,top=Math.min(d.head,d.wallHeight),sill=Math.min(d.sill,top-.1);
 return {wallHeight:d.wallHeight*k,room:{base:0,height:.02},door:{base:0,height:Math.min(d.doorHeight,d.wallHeight)*k},window:{base:sill*k,height:(top-sill)*k},
  fixture:{base:.02,height:k},other:{base:0,height:.01}};
}
/** A starting footprint size in metres: from room areas when the scene reports them, else from how big a toilet or bathtub usually is, else the slider default. */
export function estimateSpan(geometry){
 const rooms=geometry.filter(e=>e.type==='room'&&e.m2>0),ratios=geometry.filter(e=>FIXTURES[e.kind]?.nominal).map(e=>FIXTURES[e.kind].nominal/fitFootprint(e.points).length).sort((a,b)=>a-b);
 const perUnit=rooms.length?Math.sqrt(rooms.reduce((a,e)=>a+e.m2,0)/rooms.reduce((a,e)=>a+Math.abs(signedArea(e.points)),0)):ratios[ratios.length>>1];
 const {min,max,step,value}=SANDBOX[1];
 return perUnit>0?Math.min(max,Math.max(min,Math.round(perUnit*10/step)*step)):value;
}
/** Where the sun stands at an hour of the day: east at 6, high in the south at 13, west at 20. A unit-ish direction, y up. */
export function sunPosition(hour){
 const azimuth=Math.PI*Math.min(1,Math.max(0,(hour-6)/14)),elevation=(10+55*Math.sin(azimuth))*Math.PI/180;
 return [Math.cos(azimuth)*Math.cos(elevation),Math.sin(elevation),.6*Math.cos(elevation)];
}
