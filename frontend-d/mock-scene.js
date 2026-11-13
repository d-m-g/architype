const box = (x,y,w,h) => [[x,y],[x+w,y],[x+w,y+h],[x,y+h]];
export const scene = {
  id:'courtyard-demo', page:1, level:'Ground floor', width:1000, height:760,
  image:'plan.svg', coordinates:'image pixels; origin top-left; x right; y down',
  attribution:'Original synthetic illustration for Workstream D. Not dataset evidence.',
  entities:[
    ...[[225,120,100,18],[475,120,100,18],[780,205,18,90],[780,405,18,90],[480,612,100,18],[225,612,100,18]].map((b,i)=>({id:`window-${i+1}`,type:'window',label:`Window W0${i+1}`,polygon:box(...b),sourceRef:`A-101 / W0${i+1}`,method:'synthetic fixture',uncertain:false})),
    {id:'room-1',type:'room',label:'Living / dining',polygon:box(168,148,273,294),quantity:32.4,unit:'m²',sourceRef:'A-101 / room 01',method:'synthetic quantity',uncertain:false},
    {id:'room-2',type:'room',label:'Kitchen',polygon:box(459,148,313,196),quantity:18.6,unit:'m²',sourceRef:'A-101 / room 02',method:'synthetic quantity',uncertain:false},
    {id:'door-1',type:'door',label:'Entry door D01',polygon:box(677,562,77,76),sourceRef:'A-101 / D01',method:'synthetic detection',uncertain:false},
    {id:'door-2',type:'door',label:'Bedroom door D02',polygon:box(372,438,83,73),sourceRef:'A-101 / D02',method:'synthetic detection',uncertain:false},
    {id:'door-3',type:'door',label:'Bathroom door D03',polygon:box(535,438,81,73),sourceRef:'A-101 / D03',method:'synthetic detection',uncertain:true}
  ]
};
export const scenarios = {
  windows:{question:'How many windows are on this floor?',answer:'6',unit:'windows',status:'Mock count',scope:'Ground floor',explanation:'Six illustrated windows are linked below. Each number marks one object on the plan.',evidence:scene.entities.filter(e=>e.type==='window').map(e=>e.id)},
  area:{question:'What is the combined area of the living room and kitchen?',answer:'51.0',unit:'m²',status:'Mock calculation',scope:'Ground floor',formula:'32.4 m² + 18.6 m² = 51.0 m²',explanation:'Two supplied mock room quantities. These values are not measurements derived from this illustration.',evidence:['room-1','room-2']},
  doors:{question:'Which doors need review on this floor?',answer:'3',unit:'door candidates',status:'Needs review',scope:'Ground floor',explanation:'Two clear candidates and one uncertain candidate. This is not a verified physical door count.',evidence:['door-1','door-2','door-3']},
  missing:{question:'How many windows are in the whole building?',answer:null,status:'Insufficient evidence',scope:'Whole building',explanation:'Only a single ground-floor illustration is available. It cannot support a whole-building total.',evidence:[]}
};
export function resolveMockQuestion(question){return Object.values(scenarios).find(s=>s.question.toLowerCase()===question.trim().toLowerCase()) ?? {answer:null,status:'Unsupported sample',scope:'Not determined',explanation:'This prototype does not interpret arbitrary questions. Choose one of the four sample questions above.',evidence:[]};}
export function evidenceFor(result){return [...new Set(result.evidence)].map(id=>scene.entities.find(e=>e.id===id)).filter(Boolean);}
export function zoomView(view,factor){const width=Math.min(1500,Math.max(180,view.w/factor));const height=width*0.76;return {x:view.x+(view.w-width)/2,y:view.y+(view.h-height)/2,w:width,h:height};}

// Wire-format fixtures conform to shared schema v1; legacy display labels stay frontend-only.
export const sharedScene={schema_version:1,path:'data/demo/courtyard.svg',builder:'synthetic-demo@1',tier:'parse',covers:['window','door','room'],pages:[{image:'data/demo/courtyard.svg',width:scene.width,height:scene.height}],entities:scene.entities.map(e=>({id:e.id,type:e.type,label:e.label,level:scene.level,page:0,geom:e.polygon.map(([x,y])=>[x/scene.width,y/scene.height]),qty:e.quantity===undefined?{}:{m2:e.quantity},conf:e.uncertain?.55:1,ref:e.sourceRef})),trace:[]};
export const sharedAnswers=Object.fromEntries(Object.entries(scenarios).map(([key,s])=>[key,{answer:s.answer===null?null:Number(s.answer),unit:key==='area'?'m2':'count',confidence:key==='doors'?.55:key==='missing'?0:1,tier:key==='missing'?'none':'parse',evidence:s.evidence,extra_entities:[],formula:s.formula??null,interpretation:s.explanation,assumptions:{},trace:[],error:s.answer===null?s.explanation:null,cost_usd:0}]));
