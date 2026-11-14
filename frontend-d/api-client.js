export async function requestJSON(url,options={}){
 const response=await fetch(url,{...options,signal:options.signal??AbortSignal.timeout(60000)});
 let value;try{value=await response.json();}catch{throw new Error(`Unexpected response (${response.status}). Run the frontend proxy, not a static-only server.`);}
 if(!response.ok)throw new Error(`${response.status}: ${typeof value.detail==='string'?value.detail:value.message??response.statusText}`);
 return value;
}
/** Scene and Answer are independent API responses; an absent builder must not mask Answer.error. */
export async function requestDrawing(path,question,signal,onProgress=()=>{}){
 // Request scene first so a successful builder populates its cache before /ask.
 onProgress('Loading drawing evidence…');
 const [scene]=await Promise.allSettled([requestJSON(`/api/scene?${new URLSearchParams({path})}`,{signal})]);
 if(signal?.aborted)throw new DOMException('Request cancelled','AbortError');
 onProgress('Asking the live question…');
 const [answer]=await Promise.allSettled([requestJSON('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path,question}),signal})]);
 if(signal?.aborted)throw new DOMException('Request cancelled','AbortError');
 return {scene:scene.status==='fulfilled'?scene.value:null,answer:answer.status==='fulfilled'?answer.value:null,errors:[scene.status==='rejected'?`Scene: ${scene.reason.message}`:null,answer.status==='rejected'?`Answer: ${answer.reason.message}`:null].filter(Boolean)};
}

export const isDrawingPath=path=>typeof path==='string'&&(path.startsWith('data/')||path.startsWith('uploads/'));
export async function uploadDrawing(file,filename,signal){
 const value=await requestJSON('/api/upload?'+new URLSearchParams({filename}),{method:'POST',headers:{'Content-Type':file.type||'application/octet-stream'},body:file,signal});
 if(typeof value.path!=='string'||!value.path.startsWith('uploads/'))throw new Error('Upload response did not include a valid drawing identifier.');
 return value;
}

/** Friendly recovery copy only; raw backend details remain on the answer. */
export function recoveryMessage(error){
 const text=String(error?.message??error??'');
 if(/GEMINI_API_KEY|api.?key.*(?:not set|missing)|missing.*api.?key/i.test(text))return 'Model setup needed. The server is missing its model API key. Ask the team to configure it, then retry.';
 if(/timed? ?out|timeout|\b504\b/i.test(text))return 'The server took too long. Retry when it is ready; cancelling here may not stop work already running on the server.';
 if(/not written|not implemented|no scene builder|reader pending|\b501\b/i.test(text))return 'Drawing reader not ready. This format or dataset still needs its team reader. Retry after the reader is available, or choose another supported drawing.';
 if(/failed to fetch|fetch failed|network|unreachable|unavailable|not reachable|\b50[23]\b/i.test(text))return 'Cannot reach the team service. Check the connection and that the API is running, then retry.';
 if(/\b404\b|not found/i.test(text))return 'Drawing not found. Check the server drawing path, or upload the file again.';
 if(/\b413\b|too large/i.test(text))return 'The server rejected the file size. Choose a smaller file and upload again.';
 if(/\b415\b|unsupported.*(?:file|format)/i.test(text))return 'This file format is not supported by the server. Choose PDF, PNG or JPEG.';
 return 'The request could not be completed. Review the details and retry when ready. '+text;
}
