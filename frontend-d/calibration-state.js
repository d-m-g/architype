export function calibrate(points,length,unit,width,height){
 const factors={m:1,cm:0.01,mm:0.001,ft:0.3048};
 if(!Array.isArray(points)||points.length!==2||points.some(p=>!Array.isArray(p)||p.length!==2||p.some(n=>!Number.isFinite(n)))||!Number.isFinite(width)||!Number.isFinite(height)||width<=0||height<=0||points.some(([x,y])=>x<0||y<0||x>width||y>height))throw new Error('Choose two points inside the source page.');
 if(!Number.isFinite(length)||length<=0||!Object.hasOwn(factors,unit))throw new Error('Enter a positive known length and supported unit.');
 const pixels=Math.hypot(points[1][0]-points[0][0],points[1][1]-points[0][1]);
 if(pixels<1)throw new Error('Choose two distinct points at least one source pixel apart.');
 const metresPerPixel=length*factors[unit]/pixels;
 if(!Number.isFinite(metresPerPixel)||metresPerPixel<=0)throw new Error('This length is outside the supported range.');
 return {points:points.map(p=>[...p]),length,unit,metresPerPixel};
}
export function updateCalibration(draft,value){return {value,history:[...draft.history,draft.value]};}
export function undoCalibration(draft){return draft.history.length?{value:draft.history.at(-1),history:draft.history.slice(0,-1)}:draft;}
