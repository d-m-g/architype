import {uploadDrawing,isDrawingPath} from './api-client.js';
import {validateLocalDrawing} from './local-file.js';
const $ = id => document.getElementById(id);
let fileURL = null, selection = 0, previousMode = '', uploadFile=null, uploadName='', uploadAbort=null;
function stopVoice() {
  if (!$('voice-stop').disabled) $('voice-stop').click();
  if ($('voice-dictate').getAttribute('aria-pressed') === 'true') $('voice-dictate').click();
}
function clearPreview() {
  selection++;uploadAbort?.abort();uploadAbort=null;uploadFile=null;
  $('local-content').replaceChildren();
  if (fileURL) URL.revokeObjectURL(fileURL);
  fileURL = null;
  if (!$('local-preview').hidden) {
    $('question').value = $('local-question').value;
    $('mode-label').textContent = previousMode;
  }
  $('local-preview').hidden = true;
  $('editor-workspace').hidden = false;
  $('source-pill').hidden = false;
  $('editor-connection-status').hidden = false;
  $('drawing-file').value = '';
  $('upload-status').textContent = 'Choose a local preview or select a team dataset drawing for live questions.';
}
$('drawing-file').onchange = async () => {
  const file = $('drawing-file').files[0];
  if (!file) return;
  const id = ++selection;uploadAbort?.abort();uploadAbort=null;uploadFile=null;$('upload-drawing').disabled=true;
  $('upload-status').textContent = 'Checking file for local preview…';
  try {
    const type = await validateLocalDrawing(file);
    if (id !== selection) return;
    uploadFile=new Blob([file],{type});
    const suffix={'application/pdf':'pdf','image/png':'png','image/jpeg':'jpg'}[type];
    uploadName='drawing.'+(suffix??'webp');$('upload-drawing').disabled=!suffix;
    stopVoice();
    window.dispatchEvent(new Event('stoa:cancel-request'));
    if ($('local-preview').hidden) { previousMode = $('mode-label').textContent; $('local-question').value = $('question').value; }
    $('local-content').replaceChildren();
    if (fileURL) URL.revokeObjectURL(fileURL);
    fileURL = URL.createObjectURL(new Blob([file], {type}));
    const preview = document.createElement(type === 'application/pdf' ? 'iframe' : 'img');
    if (type === 'application/pdf') { preview.title = 'Local PDF preview'; }
    else { preview.alt = 'Local drawing preview: ' + file.name; preview.onerror = () => { if(id===selection) $('upload-status').textContent = 'This image could not be decoded. Choose another file. Nothing was uploaded.'; }; }
    preview.src = fileURL;
    $('local-content').append(preview);
    $('local-name').textContent = file.name + ' · ' + (file.size/1024/1024).toFixed(2) + ' MB';
    $('local-preview').hidden = false;
    $('editor-workspace').hidden = true;
    $('source-pill').hidden = true;
    $('editor-connection-status').hidden = true;
    $('mode-label').textContent = 'LOCAL PREVIEW · NOT ANALYSED';
    $('upload-status').textContent = 'Local preview only until you choose Upload drawing. WebP preview is supported, but the team upload API accepts PDF, PNG and JPEG only.';
    $('local-name').focus();
  } catch(error) { if(id===selection) $('upload-status').textContent = error.message; }
};
$('close-local').onclick = () => { clearPreview(); $('upload-status').textContent = 'Local preview closed. Returned to the previous drawing; your question draft was kept.'; };
window.addEventListener('stoa:close-local', clearPreview);
$('use-live-drawing').onclick = () => {
  const path = $('live-path').value.trim();
  if (!isDrawingPath(path)) { $('upload-status').textContent = 'Enter a data/ or uploads/ drawing path.'; return; }
  clearPreview();
  stopVoice();
  window.dispatchEvent(new CustomEvent('stoa:open-case',{detail:{path,question:$('question').value}}));
  $('upload-status').textContent = 'Live drawing selected. Edit your question below, then choose Ask live drawing.';
  $('question').focus();
};
document.addEventListener('click', event => {
  if (event.target.closest('#demo-start,#load-json,#ask-api,[data-scenario]')) clearPreview();
}, true);
window.addEventListener('pagehide', () => { if(fileURL) URL.revokeObjectURL(fileURL); });

$('upload-drawing').onclick=async()=>{
 if(!uploadFile)return;
 const id=selection,file=uploadFile,question=$('local-question').value;
 uploadAbort?.abort();const controller=new AbortController();uploadAbort=controller;
 const timer=setTimeout(()=>controller.abort(),60000);
 $('upload-drawing').disabled=true;$('upload-status').textContent='Uploading drawing to the team server…';
 try{
  const uploaded=await uploadDrawing(file,uploadName,controller.signal);
  if(id!==selection)return;
  clearPreview();$('live-path').value=uploaded.path;
  window.dispatchEvent(new CustomEvent('stoa:open-case',{detail:{path:uploaded.path,question}}));
  $('upload-status').textContent='Uploaded. Reader: '+(uploaded.reader??'not reported')+'. Ask a live question to build the scene and inspect the answer; analysis may use a model.';
  $('question').focus();
 }catch(error){if(id===selection)$('upload-status').textContent=controller.signal.aborted?'Upload timed out. Retry or close the preview.':error.message;}
 finally{clearTimeout(timer);if(id===selection){uploadAbort=null;$('upload-drawing').disabled=false;}}
};
