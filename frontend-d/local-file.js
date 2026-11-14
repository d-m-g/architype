export const MAX_LOCAL_BYTES = 25 * 1024 * 1024;
const FORMATS='PDF, PNG, JPEG, WebP, GIF, BMP, AVIF or DXF';
export async function validateLocalDrawing(file) {
  if (!file || file.size === 0) throw new Error(`Choose a non-empty ${FORMATS} drawing.`);
  if (file.size > MAX_LOCAL_BYTES) throw new Error('Choose a file no larger than 25 MB for local preview.');
  const bytes = new Uint8Array(await file.slice(0,256).arrayBuffer());
  const ascii = (start,end) => String.fromCharCode(...bytes.slice(start,end));
  if (ascii(0,5) === '%PDF-') return 'application/pdf';
  if ([137,80,78,71,13,10,26,10].every((n,i)=>bytes[i]===n)) return 'image/png';
  if (bytes[0]===255 && bytes[1]===216 && bytes[2]===255) return 'image/jpeg';
  if (ascii(0,4)==='RIFF' && ascii(8,12)==='WEBP') return 'image/webp';
  if (['GIF87a','GIF89a'].includes(ascii(0,6))) return 'image/gif';
  if (ascii(0,2)==='BM') return 'image/bmp';
  if (ascii(4,8)==='ftyp' && [8,16,20,24,28,32,36,40,44,48,52,56,60].some(i=>['avif','avis'].includes(ascii(i,i+4)))) return 'image/avif';
  // ASCII DXF opens with group code 0 and SECTION (optionally after a 999 comment); binary DXF has its own sentinel.
  const head = ascii(0,bytes.length);
  if (/^\s*(?:999\r?\n[^\n]*\n\s*)?0\r?\n\s*SECTION/.test(head) || head.startsWith('AutoCAD Binary DXF')) return 'image/vnd.dxf';
  throw new Error(`This file is not a supported ${FORMATS}. Export TIFF, HEIC, SVG and DWG files to PNG, JPEG, PDF or DXF first.`);
}

export function validateImageSize(width,height){
  if(!Number.isInteger(width)||!Number.isInteger(height)||width<1||height<1||width>16384||height>16384||width*height>32000000)throw new Error('This image is too large to process safely in the browser. Export it below 32 megapixels and 16,384 pixels per side.');
}

export function sourcePreviewKind(path){
  if(typeof path!=='string'||! /^(data|uploads)\//.test(path))return null;
  if(/\.pdf$/i.test(path))return 'pdf';
  return /\.(png|jpe?g|webp|gif|bmp|avif)$/i.test(path)?'image':null;
}

/** Source display does not depend on a model, and late loads cannot replace a newer file. */
export function createSourcePreviewLoader(loadImage,loadPDF){
  let generation=0;const cache=new Map();
  return {
    cancel(){generation++;},
    async select(path,onReady,onError=()=>{}){
      const token=++generation,kind=sourcePreviewKind(path);if(!kind)return;
      try{
        let preview=cache.get(path);
        if(!preview){
          preview={path,image:path,type:kind};
          if(kind==='image'){const size=await loadImage(path);validateImageSize(size.width,size.height);preview={...preview,width:size.width,height:size.height};}
          else if(loadPDF){const page=await loadPDF(path);validateImageSize(page.width,page.height);preview={...preview,...page};}
          cache.set(path,preview);if(cache.size>4)cache.delete(cache.keys().next().value);
        }
        if(token===generation)onReady(preview);
      }catch(error){if(token===generation)onError(error);}
    }
  };
}

/** Normalize browser-supported raster formats to the existing server upload contract. */
export async function prepareLocalDrawing(file){
  const originalType=await validateLocalDrawing(file),source=new Blob([file],{type:originalType});
  if(originalType==='application/pdf')return {blob:source,type:originalType,name:'drawing.pdf',converted:false};
  if(originalType==='image/vnd.dxf')return {blob:source,type:originalType,name:'drawing.dxf',converted:false};  // parsed on the server
  let bitmap;
  try{bitmap=await createImageBitmap(source);}catch{throw new Error('Your browser could not decode this image. Export it as PNG or JPEG and try again.');}
  try{
    validateImageSize(bitmap.width,bitmap.height);
    const size={width:bitmap.width,height:bitmap.height};
    if(['image/png','image/jpeg'].includes(originalType))return {blob:source,type:originalType,name:originalType==='image/png'?'drawing.png':'drawing.jpg',converted:false,...size};
    const canvas=document.createElement('canvas');canvas.width=bitmap.width;canvas.height=bitmap.height;
    const context=canvas.getContext('2d');if(!context)throw new Error('Image conversion is unavailable in this browser. Export as PNG or JPEG.');
    context.drawImage(bitmap,0,0);
    const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
    if(!blob)throw new Error('This image could not be converted. Export it as PNG or JPEG.');
    if(blob.size>50*1024*1024)throw new Error('The converted PNG exceeds the server’s 50 MB limit. Reduce the image dimensions and try again.');
    return {blob,type:'image/png',name:'drawing.png',converted:true,originalType,...size};
  }finally{bitmap.close();}
}

// Pinned, lazy-loaded PDF.js. PDF bytes stay in this browser; only renderer assets use the CDN.
const PDF_ASSETS='https://cdn.jsdelivr.net/npm/pdfjs-dist@5.4.624/';
let pdfLibrary;
async function getPDFLibrary(){
 if(!pdfLibrary)pdfLibrary=import(PDF_ASSETS+'build/pdf.min.mjs').then(lib=>{lib.GlobalWorkerOptions.workerSrc=PDF_ASSETS+'build/pdf.worker.min.mjs';return lib;}).catch(error=>{pdfLibrary=null;throw new Error('PDF preview could not load. Check your connection and retry; you can still upload the original PDF for analysis.');});
 return pdfLibrary;
}
export function pdfPreviewSize(width,height){
 if(!Number.isFinite(width)||!Number.isFinite(height)||width<=0||height<=0)throw new Error('This PDF page has invalid dimensions.');
 const scale=Math.min(3,4096/Math.max(width,height),Math.sqrt(8000000/(width*height)));
 return {scale,width:Math.ceil(width*scale),height:Math.ceil(height*scale)};
}
/** Render page one without a browser PDF plugin, matching the current upload reader's page scope. */
export async function renderPDFPreview(file,{loadLibrary=getPDFLibrary,createCanvas=()=>document.createElement('canvas')}={}){
 const lib=await loadLibrary(),data=new Uint8Array(await file.arrayBuffer());
 const task=lib.getDocument({data,isEvalSupported:false,useSystemFonts:true,cMapUrl:PDF_ASSETS+'cmaps/',cMapPacked:true,standardFontDataUrl:PDF_ASSETS+'standard_fonts/',wasmUrl:PDF_ASSETS+'wasm/'});
 let pdf,canvas;
 try{
  pdf=await task.promise;const page=await pdf.getPage(1),base=page.getViewport({scale:1}),size=pdfPreviewSize(base.width,base.height),viewport=page.getViewport({scale:size.scale});
  canvas=createCanvas();canvas.width=size.width;canvas.height=size.height;
  const context=canvas.getContext('2d');if(!context)throw new Error('PDF canvas rendering is unavailable in this browser.');
  await page.render({canvasContext:context,viewport,background:'rgb(255,255,255)'}).promise;
  return {displayURL:canvas.toDataURL('image/png'),width:size.width,height:size.height,pageCount:pdf.numPages,previewPage:1};
 }catch(error){throw new Error(error?.name==='PasswordException'?'This PDF requires a password. Export an unlocked copy before uploading.':'PDF preview failed: '+(error?.message??'The document could not be rendered.'));}
 finally{if(pdf)await pdf.destroy();else await task.destroy();if(canvas){canvas.width=1;canvas.height=1;}}
}
export async function loadPDFPreview(path){
 const response=await fetch('/api/image?'+new URLSearchParams({path}),{signal:AbortSignal.timeout(60000)});
 if(!response.ok)throw new Error('PDF source could not be loaded ('+response.status+'). Check your sign-in and retry.');
 const file=await response.blob();if(file.size>50*1024*1024)throw new Error('PDF is too large for browser preview.');
 return renderPDFPreview(file);
}
