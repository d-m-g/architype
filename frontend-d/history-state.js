/** In-memory answer history. Workspace persistence below saves explicit browser snapshots. */
export function createHistoryStore(limit=20) {
  const drawings=new Map(),listeners=new Set();
  let current=null,viewed=null,sequence=0;
  const emit=()=>{for(const listener of listeners)listener();};
  return {
    setDrawing(key,label){if(!drawings.has(key))drawings.set(key,{label,entries:[]});current=key;viewed=null;emit();},
    add(key,label,entry){
      if(!drawings.has(key))drawings.set(key,{label,entries:[]});
      const saved=structuredClone({...entry,id:String(++sequence),createdAt:new Date().toISOString()});
      const group=drawings.get(key);group.entries.push(saved);
      if(group.entries.length>limit)group.entries.splice(0,group.entries.length-limit);
      current=key;viewed=null;emit();return saved.id;
    },
    get(key,id){const entry=drawings.get(key)?.entries.find(e=>e.id===id);return entry?structuredClone(entry):null;},
    list(key){return structuredClone(drawings.get(key)?.entries??[]).reverse();},
    groups(){return [...drawings].map(([key,value])=>({key,label:value.label,count:value.entries.length}));},
    current(){return current;},
    viewed(){return viewed;},
    markViewed(key,id){if(!drawings.get(key)?.entries.some(e=>e.id===id))return;current=key;viewed={key,id};emit();},
    subscribe(listener){listeners.add(listener);listener();return()=>listeners.delete(listener);}
  };
}
export const historyStore=createHistoryStore();

/** Large scenes belong in IndexedDB, rather than the small localStorage quota. */
export function createWorkspaceStorage(indexedDB=globalThis.indexedDB){
 let database;
 const open=()=>database??=new Promise((resolve,reject)=>{
  if(!indexedDB){reject(new Error('Browser storage is unavailable'));return;}
  const request=indexedDB.open('blueprints-workspaces',1);
  request.onupgradeneeded=()=>request.result.createObjectStore('drawings');
  request.onsuccess=()=>resolve(request.result);
  request.onerror=()=>reject(request.error);
  request.onblocked=()=>reject(new Error('Browser storage is blocked'));
 });
 async function run(mode,path,value){
  const db=await open();
  return new Promise((resolve,reject)=>{
   const transaction=db.transaction('drawings',mode),store=transaction.objectStore('drawings');
   const request=mode==='readonly'?store.get(path):store.put(value,path);
   transaction.oncomplete=()=>resolve(request.result);
   transaction.onabort=transaction.onerror=()=>reject(transaction.error??new Error('Could not save drawing'));
  });
 }
 return {
  async load(path){const saved=await run('readonly',path);return saved?.version===1&&saved.path===path?saved:null;},
  save(snapshot){return run('readwrite',snapshot.path,structuredClone({...snapshot,version:1}));}
 };
}
