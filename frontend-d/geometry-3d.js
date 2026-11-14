/** Display-only geometry: preserves page proportions, never supplies measurements. */
export function displayGeometry(snapshot){
 if(snapshot.mode==='pending')return [];
 const entities=snapshot.entities.filter(e=>(e.page??0)===snapshot.pageIndex&&Array.isArray(e.polygon)&&e.polygon.length>=3&&e.polygon.every(p=>p.length===2&&p.every(Number.isFinite)));
 if(!entities.length)return [];
 const points=entities.flatMap(e=>e.polygon),xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
 const minX=Math.min(...xs),maxX=Math.max(...xs),minY=Math.min(...ys),maxY=Math.max(...ys),span=Math.max(maxX-minX,maxY-minY);
 if(!span)return [];
 const cx=(minX+maxX)/2,cy=(minY+maxY)/2;
 return entities.filter(e=>Math.abs(e.polygon.reduce((a,p,i)=>{const q=e.polygon[(i+1)%e.polygon.length];return a+p[0]*q[1]-q[0]*p[1];},0))>1e-8).map(e=>({id:e.id,label:e.label,type:e.type,uncertain:e.uncertain,points:e.polygon.map(([x,y])=>[(x-cx)*10/span,(y-cy)*10/span]),depth:e.type==='room'?.65:.045}));
}
/** Sandbox sliders, in metres. The drawing carries none of these: every default is an assumption a person can drag. */
export const SANDBOX=[
 {key:'span',label:'Footprint, longest side',min:4,max:60,step:.5,value:12},
 {key:'wallHeight',label:'Wall height',min:1,max:4,step:.05,value:2.5},
 {key:'wallThickness',label:'Wall thickness',min:.05,max:.6,step:.01,value:.2},
 {key:'doorHeight',label:'Door height',min:1.5,max:3,step:.05,value:2.1},
 {key:'sill',label:'Window sill height',min:0,max:2,step:.05,value:.9},
 {key:'head',label:'Window head height',min:1,max:3,step:.05,value:2.1}
];
export const sandboxDefaults=()=>Object.fromEntries(SANDBOX.map(s=>[s.key,s.value]));
/** Metres to scene units (displayGeometry makes the longest side 10 units). Openings never rise above the wall. */
export function sandboxExtents(d){
 const k=10/d.span,top=Math.min(d.head,d.wallHeight),sill=Math.min(d.sill,top-.1);
 return {wallHeight:d.wallHeight*k,wallThickness:d.wallThickness*k,
  room:{base:0,height:.04},door:{base:0,height:Math.min(d.doorHeight,d.wallHeight)*k},window:{base:sill*k,height:(top-sill)*k},
  fixture:{base:0,height:.6*k},other:{base:0,height:.02}};
}
