import {validateLocalDrawing} from './local-file.js';
const $ = id => document.getElementById(id);
let fileURL = null, selection = 0, previousMode = '';
function stopVoice() {
  if (!$('voice-stop').disabled) $('voice-stop').click();
  if ($('voice-dictate').getAttribute('aria-pressed') === 'true') $('voice-dictate').click();
}
function clearPreview() {
  selection++;
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
  const id = ++selection;
  $('upload-status').textContent = 'Checking file for local preview…';
  try {
    const type = await validateLocalDrawing(file);
    if (id !== selection) return;
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
    $('upload-status').textContent = 'Preview only. This file stays in your browser. The team API does not yet accept uploads.';
    $('local-name').focus();
  } catch(error) { if(id===selection) $('upload-status').textContent = error.message; }
};
$('close-local').onclick = () => { clearPreview(); $('upload-status').textContent = 'Local preview closed. Returned to the previous drawing; your question draft was kept.'; };
window.addEventListener('stoa:close-local', clearPreview);
$('use-live-drawing').onclick = () => {
  const path = $('live-path').value.trim();
  if (!path.startsWith('data/')) { $('upload-status').textContent = 'Enter a team dataset path beginning data/.'; return; }
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
