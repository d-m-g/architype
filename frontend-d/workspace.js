import {subscribeEditor,selectEditorEntity} from './editor-bridge.js';
import {imageURL} from './contracts.js';
const $=id=>document.getElementById(id);
function syncThemeControls(){
 const dark=document.documentElement.dataset.theme==='dark';
 $('setting-theme').value=window.stoaTheme.getPreference();
 $('theme-toggle').setAttribute('aria-pressed',String(dark));
 $('theme-toggle').title=dark?'Switch to light mode':'Switch to dark mode';
 $('theme-status').textContent=(window.stoaTheme.getPreference()==='system'?'Following your device. ':window.stoaTheme.isPersistent()?'Saved on this device. ':'Browser storage is unavailable; this tab only. ')+(dark?'Dark':'Light')+' appearance is active.';
}
$('setting-theme').onchange=event=>window.stoaTheme.setPreference(event.target.value);
$('theme-toggle').onclick=()=>window.stoaTheme.setPreference(document.documentElement.dataset.theme==='dark'?'light':'dark');
window.addEventListener('stoa:theme-change',syncThemeControls);
syncThemeControls();

let loading3D=false;
async function load3D(){if(renderer||loading3D)return;loading3D=true;try{const {RoomRenderer}=await import('./room-renderer.js');register3DRenderer(new RoomRenderer());}catch(error){$('three-placeholder').hidden=false;$('three-placeholder').querySelector('h3').textContent='3D could not start.';$('three-placeholder').querySelector('p:not(.eyebrow)').textContent='The graphics module or WebGL is unavailable. The 2D editor remains fully usable.';$('view-status').textContent='3D unavailable';}finally{loading3D=false;}}
let panel='editor',view='2d',renderer=null,unsubscribeRenderer=null;
function showPanel(name,focus=false){panel=['editor','dashboard','sources','settings'].includes(name)?name:'editor';document.querySelectorAll('[data-workspace-panel]').forEach(p=>p.hidden=p.dataset.workspacePanel!==panel);document.querySelectorAll('[data-panel]').forEach(link=>{if(link.dataset.panel===panel)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});document.title=`Stoa / ${panel[0].toUpperCase()+panel.slice(1)}`;if(focus){const heading=$(`panel-${panel}`).querySelector('h1');heading?.focus();heading?.scrollIntoView({block:'start'});}}
function setView(next){view=next==='3d'?'3d':'2d';if(view==='3d'&&!renderer)load3D();$('viewport-2d').hidden=view!=='2d';$('viewport-3d').hidden=view!=='3d';$('view-2d').setAttribute('aria-pressed',String(view==='2d'));$('view-3d').setAttribute('aria-pressed',String(view==='3d'));$('view-status').textContent=view==='2d'?'2D source evidence':renderer?'Illustrative 3D · not to scale':'3D preview · renderer not installed';renderer?.setVisible?.(view==='3d'&&panel==='editor');}
function route(){const hash=location.hash.slice(1);if(hash==='editor-3d'){showPanel('editor');setView('3d');}else if(hash.startsWith('scenario=')){showPanel('editor');setView('2d');}else if(['editor','dashboard','sources','settings'].includes(hash)){showPanel(hash);}else showPanel('editor');renderer?.setVisible?.(view==='3d'&&panel==='editor');}
window.addEventListener('hashchange',route);
$('view-2d').onclick=()=>{setView('2d');history.replaceState(null,'',location.pathname+location.search+'#editor');};$('view-3d').onclick=()=>{setView('3d');history.replaceState(null,'',location.pathname+location.search+'#editor-3d');};$('back-to-2d').onclick=()=>{$('view-2d').click();$('view-2d').focus();};
$('setting-labels').onchange=e=>document.body.classList.toggle('hide-evidence-labels',!e.target.checked);$('setting-compact').onchange=e=>document.body.classList.toggle('compact-evidence',e.target.checked);
subscribeEditor(state=>{const entity=state.entities.find(e=>e.id===state.selectedId);$('three-selection').textContent=entity?`Selected: ${entity.label} · ${entity.sourceRef} · Page ${entity.page+1}`:`${state.entities.length} scene entities · Page ${state.pageIndex+1} · No evidence selected.`;});
// Same-document links keep filters, imports, viewport and answers alive.
document.addEventListener('click',event=>{const link=event.target.closest('a');if(!link||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey||event.button!==0)return;const url=new URL(link.href,location.href);if(url.origin!==location.origin)return;if(link.matches('.skip')){event.preventDefault();history.replaceState(null,'',location.pathname+location.search+'#editor');showPanel('editor');const heading=$('panel-editor').querySelector('h1');heading.focus();heading.scrollIntoView({block:'start'});return;}if(link.matches('[data-panel]')){showPanel(link.dataset.panel,true);return;}if(url.pathname.endsWith('/index.html')||url.pathname.endsWith('/')){if(url.searchParams.has('path')){event.preventDefault();history.pushState(null,'',url.pathname+url.search+'#editor');window.dispatchEvent(new CustomEvent('stoa:open-case',{detail:{path:url.searchParams.get('path'),question:url.searchParams.get('question')??''}}));showPanel('editor',true);setView('2d');}else if(url.hash.startsWith('#scenario=')){event.preventDefault();if(location.hash===url.hash)window.dispatchEvent(new Event('hashchange'));else location.hash=url.hash;}else if(!url.hash){event.preventDefault();location.hash='editor';showPanel('editor',true);}}});
window.addEventListener('popstate',route);
route();

// Figma's page rail uses only pages actually supplied by the active scene.
// The synthetic example retains its real illustration and an explicit label.
let pageRailSignature='';
subscribeEditor(state=>{
 const pages=state.wireScene?.pages??(state.mode==='mock'?[{image:'plan.svg'}]:[]);
 const signature=JSON.stringify([state.mode,state.wireScene?.path,pages.map(p=>p.image)]);
 if(signature!==pageRailSignature){
  pageRailSignature=signature;
  $('sheet-thumbnails').replaceChildren();
  pages.forEach((page,index)=>{
   const button=document.createElement('button');button.type='button';button.className='sheet-thumb';button.dataset.page=String(index);
   button.setAttribute('aria-label',`${state.mode==='mock'?'Mock drawing':'Source page'} ${index+1}`);
   const image=document.createElement('img');image.src=state.mode==='mock'?'plan.svg':imageURL(page.image);image.alt='';image.loading='lazy';
   image.addEventListener('error',()=>{image.hidden=true;button.classList.add('image-unavailable');});
   const title=document.createElement('strong');title.textContent=`Page ${index+1}`;
   const subtitle=document.createElement('span');subtitle.textContent=state.mode==='mock'?'Synthetic example':'Source drawing';
   button.append(image,title,subtitle);
   button.onclick=()=>{if(state.mode==='mock'){$('fit').click();return;}$('page-select').value=String(index);$('page-select').dispatchEvent(new Event('change',{bubbles:true}));};
   $('sheet-thumbnails').append(button);
  });
 }
 $('sheet-count').textContent=pages.length?`Page ${state.pageIndex+1} of ${pages.length}`:'No loaded pages';
 document.querySelectorAll('.sheet-thumb').forEach(button=>button.setAttribute('aria-pressed',String(Number(button.dataset.page)===state.pageIndex)));
});
$('add-drawing-shortcut').onclick=()=>{const intake=document.querySelector('.drawing-intake');intake.open=true;intake.scrollIntoView({block:'nearest',behavior:'auto'});$('drawing-file').focus();};
/** Attach an optional real renderer later; no synthetic heights or model units are invented.
 * renderer.mount(container, {selectEntity}); renderer.update(snapshot);
 * renderer.setVisible(boolean); renderer.dispose();
 * Snapshot contains wireScene (normalized v1) when present, plus page/pixel display geometry.
 */
export function register3DRenderer(next){unsubscribeRenderer?.();renderer?.dispose?.();$('three-mount').replaceChildren();renderer=next;try{renderer?.mount($('three-mount'),{selectEntity:selectEditorEntity});unsubscribeRenderer=renderer?subscribeEditor(state=>renderer.update(state)):null;$('three-placeholder').hidden=Boolean(renderer);setView(view);}catch(error){renderer?.dispose?.();renderer=null;unsubscribeRenderer=null;$('three-mount').replaceChildren();$('three-placeholder').hidden=false;setView(view);throw error;}return()=>register3DRenderer(null);}
