import {LIVE_REQUEST_TIMEOUT_MS} from './api-client.js';
import http from 'node:http';
import {readFile,realpath,stat} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const root=path.dirname(fileURLToPath(import.meta.url));
const types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.png':'image/png','.jpg':'image/jpeg','.json':'application/json','.txt':'text/plain; charset=utf-8'};
function sendJSON(res,status,value){res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(value));}
export function createFrontendServer({apiOrigin='http://127.0.0.1:8000',timeout=LIVE_REQUEST_TIMEOUT_MS}={}){
 const target=new URL(apiOrigin);
 if(target.protocol!=='http:'||!['127.0.0.1','localhost','[::1]'].includes(target.hostname)||target.username||target.password||target.pathname!=='/'||target.search||target.hash)throw new Error('API origin must be a loopback HTTP origin without credentials or a path.');
 return http.createServer(async(req,res)=>{
  try{
   const url=new URL(req.url,'http://127.0.0.1');
   if(url.pathname==='/__frontend/api-status'){
    if(req.method!=='GET')return sendJSON(res,405,{detail:'GET required'});
    try{const r=await fetch(new URL('/openapi.json',target),{signal:AbortSignal.timeout(Math.min(timeout,5000))});if(!r.ok)throw new Error('OpenAPI unavailable');const spec=await r.json();const ready=['/api/ask','/api/scene','/api/image'].every(p=>p in (spec.paths??{}));return sendJSON(res,ready?200:502,{connected:ready,origin:target.origin,title:spec.info?.title??'Unknown API',upload_available:Boolean(spec.paths?.['/api/upload']?.post),recompute_available:Boolean(spec.paths?.['/api/recompute']?.post),message:ready?'API reachable; scene builder availability is checked per drawing.':'Required API routes are missing.'});}catch{return sendJSON(res,503,{connected:false,origin:target.origin,message:'Team API is not reachable. Start it using the frontend README instructions.'});}
   }
   if(url.pathname.startsWith('/api/')||url.pathname==='/openapi.json'){
    const allowed={'/api/ask':'POST','/api/scene':'GET','/api/image':'GET','/api/upload':'POST','/api/plan':'GET','/api/recompute':'POST','/api/voice/config':'GET','/api/voice/transcribe':'POST','/api/voice/speak':'POST','/openapi.json':'GET'};
    if(!(url.pathname in allowed))return sendJSON(res,404,{detail:'Unknown API route'});
    if(req.method!==allowed[url.pathname])return sendJSON(res,405,{detail:'Method not allowed'});
    const chunks=[];let size=0;for await(const chunk of req){size+=chunk.length;if(size>(url.pathname==='/api/upload'?50:10)*1024*1024)return sendJSON(res,413,{detail:'Request is too large'});chunks.push(chunk);}
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),timeout);res.on('close',()=>{if(!res.writableEnded)controller.abort();});
    try{const upstream=await fetch(new URL(url.pathname+url.search,target),{method:req.method,headers:req.method==='POST'?{'Content-Type':['/api/upload','/api/voice/transcribe'].includes(url.pathname)?(req.headers['content-type']??'application/octet-stream'):'application/json'}:{},body:req.method==='POST'?Buffer.concat(chunks):undefined,signal:controller.signal,redirect:'error'});res.writeHead(upstream.status,{'Content-Type':upstream.headers.get('content-type')??'application/octet-stream','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});for await(const chunk of upstream.body??[])res.write(chunk);res.end();}catch(error){if(!res.headersSent)sendJSON(res,controller.signal.aborted?504:502,{detail:controller.signal.aborted?'Team API request timed out.':'Team API unavailable. Check that it is running.'});else res.destroy();}finally{clearTimeout(timer);}return;
   }
   if(req.method!=='GET'&&req.method!=='HEAD')return sendJSON(res,405,{detail:'Method not allowed'});
   let relative;try{relative=decodeURIComponent(url.pathname).replace(/^\/+/, '')||'index.html';}catch{return sendJSON(res,400,{detail:'Invalid path'});}
   if(relative.split(/[\\/]/).some(p=>p.startsWith('.'))||!types[path.extname(relative)])return sendJSON(res,404,{detail:'Not found'});
   const file=await realpath(path.resolve(root,relative));const rel=path.relative(root,file);if(rel.startsWith('..')||path.isAbsolute(rel)||!(await stat(file)).isFile())return sendJSON(res,404,{detail:'Not found'});
   res.writeHead(200,{'Content-Type':types[path.extname(file)],'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});res.end(req.method==='HEAD'?undefined:await readFile(file));
  }catch{if(!res.headersSent)sendJSON(res,404,{detail:'Not found'});else res.destroy();}
 });
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 const port=Number(process.env.PORT??5173),server=createFrontendServer({apiOrigin:process.env.BLUEPRINTS_API_ORIGIN??'http://127.0.0.1:8000'});
 server.on('error',error=>{console.error(error.message);process.exitCode=1;});server.listen(port,'127.0.0.1',()=>console.log(`Frontend: http://127.0.0.1:${port} · Team API: ${process.env.BLUEPRINTS_API_ORIGIN??'http://127.0.0.1:8000'}`));
}
