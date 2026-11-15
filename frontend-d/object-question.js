/** Keep the exact source identity in requests and a readable label in the conversation. */
const MARKER='\n\nSelected drawing object: ';
export function scopedQuestion(question,entity){
 if(!entity||question.includes(MARKER))return question;
 const {id,type,label,number,page}=entity;
 return question+MARKER+`type=${type}; id=${JSON.stringify(id)}; name=${JSON.stringify([label||type,number].filter(Boolean).join(' '))}; page=${(page??0)+1}`+
  '\nResolve references such as this room or this object to this entity. Use its exact id for object queries, or connected_room_id for objects connected to this room. Use recorded quantities; say when information is missing.';
}
export function questionLabel(question){
 const split=question.indexOf(MARKER);
 if(split<0)return question;
 try{
  const name=question.slice(split+MARKER.length).split('\n')[0].match(/; name=("(?:\\.|[^"\\])*")/);
  return question.slice(0,split)+' — '+JSON.parse(name[1]);
 }catch{return question.slice(0,split);}
}
