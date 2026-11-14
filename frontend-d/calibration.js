import {subscribeEditor} from './editor-bridge.js';
import {calibrate,updateCalibration,undoCalibration} from './calibration-state.js';
const $=id=>document.getElementById(id),svg=$('drawing'),NS='http://www.w3.org/2000/svg',sessions=new Map();
const layer=document.createElementNS(NS,'g');layer.id='calibration-overlay';layer.style.pointerEvents='none';svg.append(layer);
let snapshot,key=null,draft=null,picking=false,points=[];
const status=text=>{$('calibration-status').textContent=text;};
const size=()=>snapshot?.wireScene?.pages[snapshot.pageIndex??0]??{width:1000,height:760};
function cancel(){picking=false;points=[];svg.classList.remove('calibrating');$('calibration-pick').setAttribute('aria-pressed','false');}
function paint(){
 layer.replaceChildren();const ps=picking?points:draft?.value?.points??[];
 const add=(tag,attrs)=>{const el=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))el.setAttribute(k,v);layer.append(el);};
 if(ps.length===2)add('line',{x1:ps[0][0],y1:ps[0][1],x2:ps[1][0],y2:ps[1][1],stroke:'#2459a6','stroke-width':3,'vector-effect':'non-scaling-stroke'});
 for(const [cx,cy] of ps)add('circle',{cx,cy,r:6,fill:'#fff',stroke:'#2459a6','stroke-width':3,'vector-effect':'non-scaling-stroke'});
}
function render(){
 $('calibration-fields').disabled=!draft;$('calibration-undo').disabled=!draft?.history.length;$('calibration-reset').disabled=!draft?.value;
 const v=draft?.value;$('calibration-summary').textContent=v?`Local draft: ${v.length} ${v.unit} between marked points (${v.metresPerPixel.toPrecision(5)} m/source pixel). Reported areas and answers unchanged.`:'No local calibration on this page. Reported areas and answers unchanged.';
 paint();
}
function save(value){draft=updateCalibration(draft,value);sessions.set(key,draft);cancel();render();}
$('calibration-pick').onclick=()=>{
 if(picking){cancel();render();status('Point selection cancelled. Previous calibration retained.');return;}
 if($('correction-enable').checked)$('correction-enable').click();
 $('view-2d').click();picking=true;points=[];$('calibration-pick').setAttribute('aria-pressed','true');svg.classList.add('calibrating');paint();svg.focus();status('Click the first point, then the second. Escape cancels. Enter the known length, then save.');
};
svg.addEventListener('pointerdown',e=>{
 if(!picking||e.button!==0)return;e.preventDefault();e.stopImmediatePropagation();
 const matrix=svg.getScreenCTM();if(!matrix)return;
 const p=svg.createSVGPoint();p.x=e.clientX;p.y=e.clientY;const q=p.matrixTransform(matrix.inverse()),{width,height}=size();
 if(q.x<0||q.y<0||q.x>width||q.y>height){status('Choose a point inside the source page.');return;}
 if(points.length===2)points=[];points.push([q.x,q.y]);
 points.forEach(([x,y],i)=>{$('calibration-x'+(i+1)).value=(x/width*100).toFixed(6);$('calibration-y'+(i+1)).value=(y/height*100).toFixed(6);});
 paint();status(points.length===1?'First point set. Click the second point.':'Two points selected. Enter the known length and save calibration.');
},true);
svg.addEventListener('click',e=>{if(picking){e.preventDefault();e.stopImmediatePropagation();}},true);
svg.addEventListener('keydown',e=>{if(picking&&e.key==='Escape'){cancel();render();status('Point selection cancelled.');}});
$('calibration-save').onclick=()=>{try{
 if(picking&&points.length!==2)throw new Error('Select both points before saving, or cancel selection to enter coordinates.');
 const {width,height}=size();const values=['x1','y1','x2','y2'].map(id=>{const v=$('calibration-'+id).value;if(!v.trim())throw new Error('Enter all four point coordinates, or select two points on the drawing.');return Number(v);});
 const ps=[[values[0]*width/100,values[1]*height/100],[values[2]*width/100,values[3]*height/100]];
 save(calibrate(ps,Number($('calibration-length').value),$('calibration-unit').value,width,height));status('Calibration saved locally for this page. No measurements recalculated.');
 }catch(error){status(error.message);}};
$('calibration-undo').onclick=()=>{draft=undoCalibration(draft);sessions.set(key,draft);cancel();render();status('Last calibration change undone.');};
$('calibration-reset').onclick=()=>{save(null);status('Calibration reset for this page. Undo can restore it.');};
for(const id of ['view-3d','correction-enable','demo-start'])$(id).addEventListener('click',()=>{cancel();render();});
window.addEventListener('hashchange',()=>{cancel();render();});
window.addEventListener('stoa:close-local',()=>{cancel();render();});
subscribeEditor(next=>{
 const source=next.mode==='mock'?'mock:courtyard':next.mode==='shared'?next.wireScene?.path:null;
 const p=next.wireScene?.pages[next.pageIndex??0];const nextKey=source?JSON.stringify([source,next.pageIndex??0,p?.image,p?.width??1000,p?.height??760]):null;
 snapshot=next;if(key!==nextKey){key=nextKey;draft=key?(sessions.get(key)??{value:null,history:[]}):null;cancel();
 for(const id of ['x1','y1','x2','y2','length'])$('calibration-'+id).value='';
 status(draft?'Select two points or enter their page percentages below.':'Load a source scene before calibrating.');render();}
});
