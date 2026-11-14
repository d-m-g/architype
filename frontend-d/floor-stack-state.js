/** Display geometry only. No alignment or physical elevation is inferred. */
export function floorPlates(snapshot){
 const pages=snapshot?.wireScene?.pages;
 if(snapshot?.mode!=='shared'||!Array.isArray(pages)||pages.length<2)return [];
 return pages.map((page,index)=>({index,image:page.image,width:page.width,height:page.height,entities:snapshot.entities.filter(e=>e.page===index&&Array.isArray(e.polygon)&&e.polygon.length>=3&&e.polygon.every(p=>p.length===2&&p.every(Number.isFinite)))}));
}
export function visiblePlates(plates,mode,pageIndex){return mode==='single'?plates.filter(p=>p.index===pageIndex):plates;}
