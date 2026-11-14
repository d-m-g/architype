import * as THREE from 'three';
import {OrbitControls} from './vendor/three/examples/jsm/controls/OrbitControls.js';
import {GLTFLoader} from './vendor/three/examples/jsm/loaders/GLTFLoader.js';
import {displayGeometry,estimateSpan,estimateWallHeight,SANDBOX,sandboxDefaults,sandboxExtents,sunPosition,wallThickness,roomEdges,roomWalls,placeOpening,placeFixture,FIXTURES} from './geometry-3d.js';

const roundedSquare=(path,half,radius)=>{const a=half-radius;[0,1,2,3].forEach(i=>path.absarc(i%3?-a:a,i<2?a:-a,radius,i*Math.PI/2,(i+1)*Math.PI/2));return path;};
const upright=geometry=>geometry.rotateX(-Math.PI/2);  // extrusions run along z; the scene's up is y
const format=(spec,value)=>spec.unit==='h'?`${Math.floor(value)}:${String(Math.round(value%1*60)).padStart(2,'0')}`:spec.unit==='floors'?`${value} of ${spec.max}`:spec.unit==='×'?`${value.toFixed(1)}×`:`${value.toFixed(2)} m`;
const lit=(mesh,cast=true)=>{mesh.castShadow=cast;mesh.receiveShadow=true;return mesh;};
// Base colour per object, [light theme, dark theme]. A highlight is a glow in the app's own marks, laid over whatever the object
// is made of: yellow counted, pink needs review, accent selected.
const BASE={room:[0xcdd3db,0x5b6676],door:[0xd8ccba,0xb5a892],window:[0xeef0f2,0xc9cfd8],fixture:[0xffffff,0xe6e9ee],counter:[0xc3c8cf,0xaeb4bd],other:[0xbfc6cf,0x4a5463]};
const EVIDENCE=0xf2c84b,REVIEW=0xf08aa9,SELECTED=[0x1f4fd8,0x82a6ff];
// Downloaded fixture models (assets/models/CREDITS.md): [file, quarter turns that bring its length onto x and its back to where FIXTURES says].
const MODELS={toilet:['toilet',0],basin:['bathroomSinkSquare',0],counter:['kitchenSink',0],bathtub:['bathtub',0],shower:['shower',0]};

export class RoomRenderer {
 mount(container,{selectEntity}){
  this.container=container;this.selectEntity=selectEntity;this.visible=false;this.objects=[];this.walls=[];this.floors=[];this.overhead=[];this.geometry=[];this.models={};this.real=null;this.realPath='';this.signature='';this.dims=sandboxDefaults();
  container.innerHTML=`<div class="three-ui"><div class="three-canvas"><details class="three-sandbox"><summary>Adjust model</summary><p class="three-note">Assumed values, not measured from the drawing.</p>${SANDBOX.map(s=>`<div><label for="sandbox-${s.key}">${s.label}</label><output for="sandbox-${s.key}"></output><input id="sandbox-${s.key}" name="${s.key}" type="range" min="${s.min}" max="${s.max}" step="${s.step}"></div>`).join('')}<button type="button">Reset</button></details><div class="three-controls" role="group" aria-label="3D camera"><button data-action="left" aria-label="Rotate left">↶</button><button data-action="right" aria-label="Rotate right">↷</button><button data-action="in" aria-label="Zoom 3D in">+</button><button data-action="out" aria-label="Zoom 3D out">−</button><button data-action="reset" aria-label="Reset 3D view">Reset</button><button data-action="top" aria-label="Top view">Top</button><button data-action="full" aria-label="Full screen">⛶</button></div></div><div class="three-foot"><label class="three-select">Inspect <select aria-label="Inspect 3D object"><option value="">Choose an object</option></select></label><p class="three-status" role="status"></p></div></div>`;
  this.host=container.querySelector('.three-canvas');this.note=container.querySelector('.three-note');this.status=container.querySelector('.three-status');this.select=container.querySelector('select');
  this.renderer=new THREE.WebGLRenderer({antialias:true,alpha:false});this.renderer.setPixelRatio(Math.min(devicePixelRatio,2));this.host.prepend(this.renderer.domElement);
  this.renderer.toneMapping=THREE.NeutralToneMapping;this.renderer.shadowMap.enabled=true;this.renderer.shadowMap.type=THREE.PCFShadowMap;
  this.renderer.shadowMap.autoUpdate=false;  // the shadow map is redrawn only when the model or the sun changes, not on every camera move
  this.renderer.domElement.setAttribute('aria-label','Interactive illustrative 3D model. Drag to orbit, right-drag to pan, scroll to zoom; left and right arrows rotate, up and down tilt, plus and minus zoom, Home resets. Use the object selector to inspect evidence.');this.renderer.domElement.tabIndex=0;
  this.world=new THREE.Scene();this.camera=new THREE.PerspectiveCamera(45,1,.05,400);
  this.controls=new OrbitControls(this.camera,this.renderer.domElement);this.controls.enableDamping=!matchMedia('(prefers-reduced-motion: reduce)').matches;this.controls.dampingFactor=.12;this.controls.zoomToCursor=true;this.controls.minDistance=.4;this.controls.maxDistance=120;this.controls.maxPolarAngle=Math.PI*.495;
  this.controls.addEventListener('change',()=>this.draw());this.controls.addEventListener('start',()=>{this.dragging=true;this.animate();});this.controls.addEventListener('end',()=>{this.dragging=false;});
  // Soft sky fill plus one shadow-casting sun. The model always fits a 10-unit square at the origin, so the shadow camera is fixed around it.
  this.fill=new THREE.HemisphereLight(0xffffff,0xb4bcc8,1.5);this.sun=new THREE.DirectionalLight(0xffffff,2.6);this.sun.castShadow=true;this.sun.shadow.mapSize.set(2048,2048);this.sun.shadow.radius=4;this.sun.shadow.bias=-.0004;this.sun.shadow.normalBias=.03;
  Object.assign(this.sun.shadow.camera,{left:-8.5,right:8.5,top:8.5,bottom:-8.5,near:1,far:45});
  this.ground=new THREE.Mesh(upright(new THREE.PlaneGeometry(80,80)),new THREE.ShadowMaterial({opacity:.16}));this.ground.position.y=-.003;this.ground.receiveShadow=true;
  this.grid=new THREE.GridHelper(24,24,0xffffff,0xffffff);this.grid.position.y=-.001;this.grid.material.transparent=true;this.grid.material.depthWrite=false;  // a floor reference: one quiet tone, set per theme
  this.world.add(this.fill,this.sun,this.ground,this.grid);
  // Shared by every wall and fixture: a wall or a fixture part is one of these, moved and scaled.
  const well=roundedSquare(new THREE.Shape(),.5,.12);well.holes.push(roundedSquare(new THREE.Path(),.42,.09));const solid={depth:1,bevelEnabled:false,curveSegments:6};
  this.parts={box:new THREE.BoxGeometry(1,1,1).translate(0,.5,0),cyl:new THREE.CylinderGeometry(.5,.5,1,24).translate(0,.5,0),pad:upright(new THREE.ExtrudeGeometry(roundedSquare(new THREE.Shape(),.5,.12),solid)),rim:upright(new THREE.ExtrudeGeometry(well,solid)),
   bowl:new THREE.LatheGeometry([[.26,0],[.32,.1],[.5,.36],[.5,.41],[.42,.41],[.36,.28],[0,.24]].map(([r,y])=>new THREE.Vector2(r,y)),28)};
  this.materials={wall:new THREE.MeshStandardMaterial({roughness:.95}),cabinet:new THREE.MeshStandardMaterial({roughness:.8}),metal:new THREE.MeshStandardMaterial({color:0xc4c9d0,metalness:.55,roughness:.3}),glass:new THREE.MeshStandardMaterial({color:0x2a3642,roughness:.08,transparent:true,opacity:.3}),pane:new THREE.MeshStandardMaterial({color:0xbfd6e4,roughness:.05,transparent:true,opacity:.28})};
  const sandbox=container.querySelector('.three-sandbox');this.inputs=[...sandbox.querySelectorAll('input')];sandbox.oninput=e=>{this.dims[e.target.name]=+e.target.value;this.applyDimensions();};sandbox.querySelector('button').onclick=()=>{this.dims={...sandboxDefaults(),span:this.span??this.dims.span,wallHeight:this.wallHeight??this.dims.wallHeight,floors:this.floors.length||1};this.applyDimensions();};
  this.group=new THREE.Group();this.world.add(this.group);this.raycaster=new THREE.Raycaster();this.pointer=new THREE.Vector2();
  this.onTheme=()=>this.applyTheme();window.addEventListener('stoa:theme-change',this.onTheme);this.applyTheme();this.applyDimensions();
  const canvas=this.renderer.domElement;canvas.addEventListener('pointerdown',e=>{this.down={x:e.clientX,y:e.clientY};});canvas.addEventListener('pointerup',e=>{if(!this.down||Math.hypot(e.clientX-this.down.x,e.clientY-this.down.y)>5)return;const rect=canvas.getBoundingClientRect();this.pointer.set((e.clientX-rect.left)/rect.width*2-1,-(e.clientY-rect.top)/rect.height*2+1);this.raycaster.setFromCamera(this.pointer,this.camera);let hit=this.raycaster.intersectObjects(this.floors.filter(f=>f.visible))[0]?.object;while(hit&&!hit.userData.id)hit=hit.parent;if(hit)this.selectEntity(hit.userData.id);});canvas.addEventListener('pointercancel',()=>this.down=null);
  canvas.addEventListener('webglcontextlost',e=>{e.preventDefault();this.lost=true;this.status.textContent='3D graphics context lost. Return to the plan; reload to restore 3D.';});
  this.select.onchange=()=>{if(!this.select.value)return;this.selectEntity(this.select.value);this.focus(this.select.value);};container.querySelectorAll('[data-action]').forEach(b=>b.onclick=()=>this.action(b.dataset.action));
  canvas.addEventListener('keydown',e=>{const actions={ArrowLeft:'left',ArrowRight:'right',ArrowUp:'up',ArrowDown:'down','+':'in','=':'in','-':'out',Home:'reset'};if(actions[e.key]){e.preventDefault();this.action(actions[e.key]);}});
  this.observer=new ResizeObserver(()=>this.resize());this.observer.observe(this.host);this.reset();this.loadModels();
 }
 /** Each model is loaded once, stood on a 1 × 1 footprint at its real height and relit; fixtures then hold clones.
  *  Until it arrives, or if it never does, the procedural model stands in. */
 loadModels(){
  const loader=new GLTFLoader();
  Promise.allSettled(Object.entries(MODELS).map(([kind,[file,turn]])=>loader.loadAsync(new URL(`./assets/models/${file}.glb`,import.meta.url).href).then(({scene})=>{
   scene.traverse(o=>{if(!o.isMesh)return;const from=o.material,glass=from.name==='glass';o.material=new THREE.MeshStandardMaterial({color:from.color,roughness:glass?.08:.45,metalness:/metal/i.test(from.name)?.35:0,transparent:glass,opacity:glass?.35:1});from.dispose();lit(o,!glass);});
   scene.rotation.y=turn*Math.PI/2;const box=new THREE.Box3().setFromObject(scene),size=box.getSize(new THREE.Vector3()),centre=box.getCenter(new THREE.Vector3()),holder=new THREE.Group();
   scene.position.set(-centre.x,-box.min.y,-centre.z);holder.add(scene);holder.scale.set(1/size.x,FIXTURES[kind].height/size.y,1/size.z);this.models[kind]=holder;
  }))).then(()=>{if(this.renderer&&this.snapshot){this.build();this.refresh();}});
 }
 /** A model that knows where its storeys sit (IFC) also has its own 3D geometry: real walls with their openings cut, slabs,
  *  columns, stairs, roof. It replaces the walls and openings inferred from room outlines as soon as it arrives. */
 loadReal(path){
  if(path===this.realPath)return;this.realPath=path;this.disposeReal();if(!path)return;
  this.note.textContent='Loading the model’s own geometry…';
  new GLTFLoader().loadAsync(`/api/model?path=${encodeURIComponent(path)}`).then(({scene})=>{
   if(path!==this.realPath||!this.renderer)return;
   scene.updateMatrixWorld(true);scene.traverse(o=>{if(!o.isMesh)return;const m=o.material,hsl=m.color.getHSL({});m.color.setHSL(hsl.h,Math.min(hsl.s,.25),hsl.l);m.roughness=.85;m.metalness=0;if(m.opacity<1)m.transparent=true;/* the model's colours, muted: authoring tools paint roofs and finishes in loud flat tones */lit(o,!m.transparent);});
   this.real=scene;this.build();this.dims.floors=this.floors.length;this.applyDimensions();this.refresh();this.reset();
  }).catch(()=>{if(path===this.realPath)this.note.textContent='Walls are inferred from the room outlines: the model’s own geometry could not be loaded.';});
 }
 disposeReal(){this.real?.traverse(o=>{if(o.isMesh){o.geometry.dispose();o.material.dispose();}});this.real=null;}
 applyTheme(){
  this.dark=document.documentElement.dataset.theme==='dark';
  this.renderer.setClearColor(new THREE.Color(getComputedStyle(document.documentElement).getPropertyValue('--desk').trim()));
  this.materials.wall.color.setHex(this.dark?0xbfc6d0:0xf4f5f7);this.materials.cabinet.color.setHex(this.dark?0x8d96a3:0xe3e6ea);this.ground.material.opacity=this.dark?.4:.16;this.grid.material.color.setHex(this.dark?0x2a3442:0xb3bcc8);this.grid.material.opacity=this.dark?.45:.45;this.paint();
 }
 /** Sliders only move, scale and relight what is already in the scene: nothing is rebuilt or disposed. */
 applyDimensions(){
  const extents=sandboxExtents(this.dims),[x,y,z]=sunPosition(this.dims.sun);
  for(const object of this.objects){if(object.userData.real)continue;const e=extents[object.userData.type]??extents.other;object.position.y=e.base;object.scale.y=e.height;}
  // A wall piece is the full height, or what is left above an opening (the lintel), or below a window (the sill wall).
  // Storeys stand at their own elevation, pulled apart by the spacing slider; the ones above 'Floors shown' are lifted off.
  this.floors.forEach((floor,i)=>{floor.position.y=floor.userData.elevation*extents.k*this.dims.gap;floor.visible=i<this.dims.floors;});
  for(const [object,floor] of this.overhead)object.visible=this.dims.floors===this.floors.length||floor<this.dims.floors-1;  // the top storey left standing is open from above
  for(const wall of this.walls){const u=wall.userData,top=Math.min(extents.wallHeight,u.storey?u.storey*extents.k:Infinity)*u.lift,o=extents[u.part],[from,to]=u.part==='wall'?[0,top]:u.below?[0,o.base]:[o.base+o.height,top];wall.position.y=from;wall.scale.y=Math.max(to-from,1e-4);wall.visible=to-from>1e-3;}
  this.sun.position.set(x*20,y*20,z*20);this.sun.intensity=1+2*y;this.sun.color.setRGB(1,.8+.2*y,.6+.4*y);  // low sun: weaker and warmer
  SANDBOX.forEach((spec,i)=>{const input=this.inputs[i];if(spec.key==='floors'){spec={...spec,max:Math.max(1,this.floors.length)};input.max=spec.max;}input.parentElement.hidden=spec.key==='floors'||spec.key==='gap'?this.floors.length<2:spec.key!=='sun'&&this.realOn;/* a model's own geometry has its own sizes */const text=format(spec,this.dims[spec.key]);input.value=this.dims[spec.key];input.previousElementSibling.value=text;input.setAttribute('aria-valuetext',text);});
  this.renderer.shadowMap.needsUpdate=true;this.draw();
 }
 paint(){
  const evidence=new Set(this.snapshot?.answer?.evidence??[]),theme=this.dark?1:0;
  for(const {userData:u} of this.objects){
   const [glow,strength]=u.id===this.snapshot.selectedId?[SELECTED[theme],.5]:u.uncertain?[REVIEW,.4]:evidence.has(u.id)?[EVIDENCE,.4]:[0,0];
   u.tint?.color.setHex((BASE[u.kind]??BASE[u.type]??BASE.other)[theme]);for(const material of u.materials){material.emissive.setHex(glow);material.emissiveIntensity=strength;}
  }
  this.draw();
 }
 /** Put the whole model in view from a direction, whatever the canvas shape. */
 frame(x,y,z){
  const box=new THREE.Box3().setFromObject(this.group),radius=box.isEmpty()?6:box.getBoundingSphere(new THREE.Sphere()).radius,half=this.camera.fov*Math.PI/360;
  const distance=radius*.9/Math.sin(Math.atan(Math.tan(half)*Math.min(1,this.camera.aspect)));
  if(box.isEmpty())this.controls.target.set(0,0,0);else box.getCenter(this.controls.target);
  this.camera.position.copy(this.controls.target).addScaledVector(new THREE.Vector3(x,y,z).normalize(),Math.min(distance,this.controls.maxDistance));this.controls.update();this.draw();
 }
 reset(){this.frame(.35,1,.6);}
 /** Choosing an object in the list brings the camera to it; clicking one in the scene only selects it. */
 focus(id){const object=this.objects.find(o=>o.userData.id===id);if(!object)return;const box=new THREE.Box3().setFromObject(object),offset=this.camera.position.clone().sub(this.controls.target);box.getCenter(this.controls.target);offset.y=Math.hypot(offset.x,offset.z)*2;  // steep, so the room's own walls do not hide it
  this.camera.position.copy(this.controls.target).add(offset.setLength(Math.max(2,box.getSize(new THREE.Vector3()).length()*3)));this.controls.update();this.draw();}
 action(action){
  if(action==='reset')return this.reset();
  if(action==='top')return this.frame(0,1,.001);
  if(action==='full')return document.fullscreenElement?document.exitFullscreen():this.host.parentElement.requestFullscreen?.().catch(()=>{});
  const offset=this.camera.position.clone().sub(this.controls.target);
  if(action==='left'||action==='right')offset.applyAxisAngle(new THREE.Vector3(0,1,0),action==='left'?.25:-.25);
  else if(action==='up'||action==='down'){const angle=new THREE.Spherical().setFromVector3(offset);angle.phi=Math.min(this.controls.maxPolarAngle,Math.max(.05,angle.phi+(action==='up'?-.15:.15)));offset.setFromSpherical(angle);}
  else offset.multiplyScalar(action==='in'?.75:1/.75).clampLength(this.controls.minDistance,this.controls.maxDistance);
  this.camera.position.copy(this.controls.target).add(offset);this.controls.update();this.draw();
 }
 /** Runs only while a drag or its damping is still moving the camera; otherwise the scene is drawn on demand. */
 animate(){if(this.frameId)return;const step=()=>{this.frameId=0;if(!this.visible)return;if(this.controls.update()||this.dragging)this.frameId=requestAnimationFrame(step);};this.frameId=requestAnimationFrame(step);}
 clearObjects(){for(const object of this.objects){for(const material of object.userData.materials)material.dispose();if(object.userData.own)object.geometry.dispose();}this.group.clear();this.objects=[];this.walls=[];this.floors=[];this.overhead=[];}
 update(snapshot){
  this.snapshot=snapshot;const geometry=displayGeometry(snapshot),signature=JSON.stringify(geometry);
  if(signature!==this.signature){this.signature=signature;const path=geometry[0]?.origin?snapshot.wireScene?.path??'':'';if(path!==this.realPath)this.disposeReal();/* never dress a new drawing in the last model's geometry */this.geometry=geometry;this.span=estimateSpan(geometry);this.dims.span=this.span;this.wallHeight=estimateWallHeight(geometry);this.dims.wallHeight=this.wallHeight;this.dims.gap=1;this.build();this.dims.floors=this.floors.length;this.applyDimensions();this.reset();this.loadReal(path);}
  this.refresh();
 }
 /** Rebuilds the model from the current geometry: on a new drawing, and once more when the fixture models have loaded. */
 build(){
  this.clearObjects();this.select.replaceChildren(new Option('Choose an object',''));
  const thickness=wallThickness(this.span),real=this.real;this.realOn=Boolean(real);
  this.note.textContent=real?'Geometry, sizes and heights come from the model itself.':this.realPath?this.note.textContent:'Assumed values, not measured from the drawing.';
  const add=(parent,geometry,material,position,scale,cast=true)=>{const mesh=lit(new THREE.Mesh(this.parts[geometry],material),cast);mesh.position.set(...position);mesh.scale.set(...scale);parent.add(mesh);return mesh;};
  const place=(object,fit)=>{object.position.set(fit.x,0,fit.z);object.rotation.y=-fit.angle;return object;};
  // One group per storey (a drawing is a single one), so a door only opens the walls of its own floor.
  for(const index of [...new Set(this.geometry.map(e=>e.floor??0))].sort((a,b)=>a-b)){
  const items=this.geometry.filter(e=>(e.floor??0)===index),floor=new THREE.Group();floor.userData={elevation:items[0].elevation??0};this.group.add(floor);this.floors.push(floor);
  const rooms=new Map(items.filter(e=>e.type==='room').map(e=>[e.id,roomEdges(e.points,e.id)])),edges=[...rooms.values()].flat(),order=[...rooms.keys()];
  // Doors and windows first: each says which room walls it opens, so the walls can be built around it.
  const openings=new Map(),cuts=new Map();
  for(const entity of items)if(entity.type==='door'||entity.type==='window'){const fit=placeOpening(entity.points,edges,thickness);openings.set(entity.id,fit);for(const cut of fit.cuts)cuts.set(cut.edge,[...(cuts.get(cut.edge)??[]),{...cut,type:entity.type}]);}
  for(const entity of items){
   if(real?entity.type!=='room':entity.type==='wall'&&rooms.size)continue;  // the model's own geometry supplies everything but the room floors; without it, a model's flat wall outlines are drawn only where no rooms give walls with openings
   let object,own=false,tint;const materials=[],opening=openings.get(entity.id);
   const tinted=options=>{tint=new THREE.MeshStandardMaterial(options);materials.push(tint);return tint;};
   if(entity.kind){  // a fixture, fitted to its footprint: the downloaded model when there is one, else shared procedural parts
    const fit=placeFixture(entity.points,entity.kind,edges);object=place(new THREE.Group(),fit);object.scale.set(fit.length,1,fit.width);
    if(this.models[entity.kind]){const copy=this.models[entity.kind].clone();copy.traverse(o=>{if(o.isMesh){o.material=o.material.clone();materials.push(o.material);}});object.add(copy);}
    else{tinted(entity.kind==='counter'?{metalness:.55,roughness:.3}:{roughness:.25});for(const [part,material,position,scale] of FIXTURES[entity.kind].parts)add(object,part,material==='tint'?tint:this.materials[material],position,scale,material!=='glass');}
   }else if(opening){  // stands in the gap its cuts left: x runs along the wall, z through it, and y is 0 to 1 so the sliders only scale it
    const length=opening.length,depth=opening.depth+.012,jamb=Math.min(length*.08,.05);object=place(new THREE.Group(),opening);tinted({roughness:.7});
    for(const side of [-1,1])add(object,'box',tint,[side*(length-jamb)/2,0,0],[jamb,1,depth]);
    if(entity.type==='door'){  // frame, and a leaf left ajar so the rooms visibly connect
     add(object,'box',tint,[0,.96,0],[length,.04,depth]);const hinge=new THREE.Group(),leaf=length-2*jamb;hinge.position.x=jamb-length/2;hinge.rotation.y=-.75;object.add(hinge);
     add(hinge,'box',tint,[leaf/2,0,0],[leaf,.96,.014]);add(hinge,'box',this.materials.metal,[leaf*.88,.47,0],[.022,.012,.05]);
    }else{for(const y of [0,.95])add(object,'box',tint,[0,y,0],[length,.05,depth]);add(object,'box',tint,[0,0,0],[jamb*.7,1,depth*.6]);add(object,'box',this.materials.pane,[0,0,0],[length,1,.008],false);}
   }else{const shape=new THREE.Shape();entity.points.forEach(([x,y],i)=>i?shape.lineTo(x,-y):shape.moveTo(x,-y));shape.closePath();object=lit(new THREE.Mesh(upright(new THREE.ExtrudeGeometry(shape,{depth:1,bevelEnabled:false,steps:1})),tinted({roughness:.9})),false);own=true;}
   object.userData={...entity,tint,materials,own};floor.add(object);this.objects.push(object);this.select.append(new Option(`${entity.label}${entity.uncertain?' · needs review':''}`,entity.id));
   // Open roof: a room is its floor plus wall boxes along each edge, split around its openings. Each room's walls are a hair
   // taller than the last one's, so where two rooms' walls overlap one top wins cleanly instead of flickering.
   if(entity.type==='room'&&!real)for(const fit of roomWalls(rooms.get(entity.id),thickness,cuts))for(const below of fit.part==='window'?[false,true]:[false]){
    const wall=place(lit(new THREE.Mesh(this.parts.box,this.materials.wall)),fit);wall.scale.set(fit.length,1,thickness);wall.userData={id:entity.id,type:'wall',part:fit.part,below,storey:entity.storey,lift:1+order.indexOf(entity.id)%7*.0012};floor.add(wall);this.walls.push(wall);}
  }
  }
  if(real&&this.floors.length){
   // Each element of the model joins its storey: the scene entity it is (by GlobalId) says which, else its height does.
   const [cx,ground,cz]=this.geometry[0].origin,k=10/this.geometry[0].spanM,byRef=new Map(this.geometry.map(e=>[e.ref,e])),levels=this.floors.map(f=>f.userData.elevation+ground),box=new THREE.Box3();
   const holders=this.floors.map((floor,i)=>{const holder=new THREE.Group();holder.scale.setScalar(k);holder.position.set(-cx*k,-levels[i]*k,-cz*k);floor.add(holder);return holder;});
   for(const node of real.children){
    const entity=byRef.get('ifc:'+(node.userData.name??node.name)),object=node.clone(),materials=[];let index=entity?.floor;
    box.setFromObject(node);if(index===undefined){const y=box.getCenter(new THREE.Vector3()).y;index=Math.max(0,levels.findLastIndex(level=>level<=y+.05));}
    if(box.min.y-levels[index]>1.8)this.overhead.push([object,index]);  // a ceiling, beam or the slab above: lifted off with the storeys above it

    if(entity){object.traverse(o=>{if(o.isMesh){o.material=o.material.clone();materials.push(o.material);}});object.userData={...entity,materials,real:true};this.objects.push(object);this.select.append(new Option(`${entity.label}${entity.uncertain?' · needs review':''}`,entity.id));}
    holders[index].add(object);
   }
  }
  this.applyDimensions();
 }
 refresh(){
  const snapshot=this.snapshot,selected=this.objects.find(o=>o.userData.id===snapshot.selectedId),count=type=>this.geometry.filter(e=>e.type===type).length;this.select.value=snapshot.selectedId??'';
  this.status.textContent=this.geometry.length?`${this.floors.length>1?this.floors.length+' storeys · ':''}${count('room')} rooms · ${count('fixture')} fixtures${selected?' · Selected: '+selected.userData.label:''}`:'Nothing to show in 3D on this page.';this.paint();
 }
 resize(){if(!this.renderer)return;const w=this.host.clientWidth,h=this.host.clientHeight;if(!w||!h)return;this.renderer.setSize(w,h,false);this.camera.aspect=w/h;this.camera.updateProjectionMatrix();this.draw();}
 draw(){if(this.visible&&!this.lost&&this.renderer)this.renderer.render(this.world,this.camera);}
 setVisible(visible){this.visible=visible;this.controls.enabled=visible;if(visible)this.resize();}
 dispose(){cancelAnimationFrame(this.frameId);this.disposeReal();for(const model of Object.values(this.models??{}))model.traverse(o=>{if(o.isMesh){o.geometry.dispose();o.material.dispose();}});window.removeEventListener('stoa:theme-change',this.onTheme);this.observer?.disconnect();this.controls?.dispose();if(this.group)this.clearObjects();for(const item of [...Object.values(this.parts??{}),...Object.values(this.materials??{}),this.ground?.geometry,this.ground?.material,this.grid?.geometry,this.grid?.material])item?.dispose();this.renderer?.dispose();this.renderer=null;this.container?.replaceChildren();}
}
