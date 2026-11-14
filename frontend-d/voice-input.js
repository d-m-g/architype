/** The Ask handler waits for active dictation instead of submitting an old draft. */
let handler=null;
export function registerVoiceInput(next){
 handler=next;
 return ()=>{if(handler===next)handler=null;};
}
export async function finishVoiceInput(){return handler?await handler():true;}
