/** Session-only snapshots; never persists questions or source data to storage. */
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
