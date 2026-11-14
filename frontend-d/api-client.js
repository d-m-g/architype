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
