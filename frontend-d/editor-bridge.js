/** Frontend-only renderer seam. Coordinates retain their documented source space. */
let snapshot=null;
const listeners=new Set();
export function publishEditor(next){snapshot=next;for(const listener of listeners)listener(snapshot);}
export function subscribeEditor(listener){listeners.add(listener);if(snapshot)listener(snapshot);return()=>listeners.delete(listener);}
export function getEditorSnapshot(){return snapshot;}
// A future renderer calls this with a stable evidence entity ID.
export function selectEditorEntity(id){window.dispatchEvent(new CustomEvent('stoa:select-entity',{detail:{id}}));}
