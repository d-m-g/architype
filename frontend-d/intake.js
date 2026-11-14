import {uploadDrawing,isDrawingPath,recoveryMessage} from './api-client.js';
import {prepareLocalDrawing,renderPDFPreview} from './local-file.js';
const $ = id => document.getElementById(id);
let fileURL = null, selection = 0, previousMode = '', uploadFile=null, uploadName='', originalName='', uploadAbort=null;
const intake=document.querySelector('.drawing-intake');
function intakeStage(stage){
 intake.dataset.stage=stage;
 const step=stage==='uploading'?'open':stage==='review'?'review':'choose';
 document.querySelectorAll('[data-intake-step]').forEach(item=>{if(item.dataset.intakeStep===step)item.setAttribute('aria-current','step');else item.removeAttribute('aria-current');});
 $('replace-drawing').disabled=stage==='uploading';$('cancel-intake').disabled=stage==='uploading';
}
function stopVoice() {
  if (!$('voice-stop').disabled) $('voice-stop').click();
  if ($('voice-dictate').getAttribute('aria-pressed') === 'true') $('voice-dictate').click();
}
function clearPreview() {
  selection++;uploadAbort?.abort();uploadAbort=null;uploadFile=null;uploadControls(false);
  $('local-content').replaceChildren();
  if (fileURL) URL.revokeObjectURL(fileURL);
  fileURL = null;
  if (!$('local-preview').hidden||(intake.dataset.stage==='preparing'&&$('editor-workspace').hidden)) {
    $('question').value = $('local-question').value;
    $('mode-label').textContent = previousMode;
  }
  $('local-preview').hidden = true;
  $('editor-workspace').hidden = false;
  $('source-pill').hidden = false;
  $('editor-connection-status').hidden = false;
  $('drawing-file').value = '';
  $('upload-status').textContent = 'Choose a file to get started.';intakeStage('choose');
}
$('drawing-file').onchange = async () => {
  const file = $('drawing-file').files[0];
  if (!file) return;
  if($('local-preview').hidden&&intake.dataset.stage==='choose'){previousMode=$('mode-label').textContent;$('local-question').value=$('question').value;}
  const id = ++selection;uploadAbort?.abort();uploadAbort=null;uploadFile=null;uploadControls(false);$('upload-drawing').disabled=true;
  intakeStage('preparing');$('local-preview').hidden=true;$('upload-status').textContent = 'Preparing your preview...';
  try {
    const prepared = await prepareLocalDrawing(file),type=prepared.type;
    if (id !== selection) return;
    uploadFile=prepared.blob;
    uploadName=prepared.name;originalName=file.name;$('upload-drawing').disabled=false;$('upload-drawing').textContent='Open drawing';
    stopVoice();
    window.dispatchEvent(new Event('stoa:cancel-request'));
    $('local-content').replaceChildren();
    if (fileURL) URL.revokeObjectURL(fileURL);
    fileURL = URL.createObjectURL(uploadFile);
    const dxf = type === 'image/vnd.dxf',pdf=type==='application/pdf';
    let preview=document.createElement(dxf?'p':'img'),pdfNote='';
    if(dxf){preview.className='muted';preview.textContent='DXF drawings cannot be previewed in the browser. Upload it: the server reads its layers and draws the page.';}
    else if(pdf){
      $('upload-status').textContent='Rendering PDF page 1 for the drawing viewer...';
      try{
        const rendered=await renderPDFPreview(uploadFile);if(id!==selection)return;
        preview.src=rendered.displayURL;preview.alt='PDF drawing preview, page 1 of '+rendered.pageCount;
        pdfNote=' - Page 1 of '+rendered.pageCount+'; analysis covers page 1';
      }catch(error){if(id!==selection)return;preview=document.createElement('p');preview.className='muted';preview.textContent=error.message+' You can upload the original PDF below to use the server reader.';}
    }else{preview.alt='Local drawing preview: '+file.name;preview.onerror=()=>{if(id===selection)$('upload-status').textContent='This image could not be decoded. Choose another file. Nothing was uploaded.';};preview.src=fileURL;}
    $('local-content').append(preview);
    $('local-name').textContent=file.name;
    $('local-file-meta').textContent=(file.size/1024/1024).toFixed(2)+' MB'+(prepared.converted?' - converted to PNG':'')+(prepared.resized?' - resized to '+prepared.width+' x '+prepared.height+' px':'')+pdfNote;
    $('local-analysis-note').textContent=pdf?'PDF analysis covers page 1 only. Other pages are not analysed.':dxf?'The server will render this CAD drawing and extract its available areas.':'Detected areas appear after the drawing opens and analysis finishes.';
    intakeStage('review');
    $('local-preview').hidden = false;
    $('editor-workspace').hidden = true;
    $('source-pill').hidden = true;
    $('editor-connection-status').hidden = true;
    $('mode-label').textContent = 'LOCAL PREVIEW · NOT ANALYSED';
    $('upload-status').textContent='Preview ready. Open drawing to upload this file and detect its areas.';
    $('local-name').focus();
  } catch(error) { if(id===selection){uploadFile=null;uploadControls(false);intakeStage('choose');$('local-preview').hidden=true;$('editor-workspace').hidden=false;$('source-pill').hidden=false;$('editor-connection-status').hidden=false;$('mode-label').textContent=previousMode;$('upload-status').textContent=error.message;} }
};
$('close-local').onclick=()=>{clearPreview();intake.open=false;};
$('cancel-intake').onclick=()=>{clearPreview();intake.open=false;};
$('replace-drawing').onclick=()=>{$('drawing-file').value='';$('drawing-file').click();};
intake.addEventListener('toggle',()=>{if(!intake.open&&intake.dataset.stage!=='choose')clearPreview();});
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
 const id=selection,file=uploadFile,displayName=originalName;
 uploadAbort?.abort();const controller=new AbortController();uploadAbort=controller;
 let timedOut=false;const timer=setTimeout(()=>{timedOut=true;controller.abort();},60000);
 uploadControls(true);$('upload-status').textContent='Uploading your drawing... Your question stays editable.';
 try{
  const uploaded=await uploadDrawing(file,uploadName,controller.signal);
  if(id!==selection)return;
  if(controller.signal.aborted)throw new DOMException('Upload cancelled','AbortError');
  const question=$('local-question').value;
  clearPreview();$('live-path').value=uploaded.path;
  window.dispatchEvent(new CustomEvent('stoa:open-case',{detail:{path:uploaded.path,question,displayName}}));
  $('upload-status').textContent='Drawing uploaded. Detecting areas; your question is ready in the viewer.';
  $('contract-status').textContent=$('upload-status').textContent;document.querySelector('.drawing-intake').open=false;
  $('question').focus({preventScroll:true});
 }catch(error){if(id===selection){$('upload-status').textContent=(controller.signal.aborted?(timedOut?recoveryMessage('Upload timed out'):'Upload cancelled locally. The server may have received the file.'):recoveryMessage(error))+' Your preview and question are kept. Retry upload when ready.';$('upload-drawing').textContent='Try opening again';}}
 finally{clearTimeout(timer);if(id===selection){uploadAbort=null;uploadControls(false);}}
};

function uploadControls(busy){
 intakeStage(busy?'uploading':uploadFile?'review':'choose');
 if(busy)$('upload-drawing').textContent='Uploading...';
 $('cancel-upload').hidden=!busy;
 $('upload-drawing').disabled=busy||!uploadFile;
 $('local-preview').setAttribute('aria-busy',String(busy));
}
$('cancel-upload').onclick=()=>{if(uploadAbort){uploadAbort.abort();$('upload-status').textContent='Cancelling upload… Your preview and question will be kept.';}};
