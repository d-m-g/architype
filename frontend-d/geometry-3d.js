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
