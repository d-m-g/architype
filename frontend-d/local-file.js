export const MAX_LOCAL_BYTES = 25 * 1024 * 1024;
export async function validateLocalDrawing(file) {
  if (!file || file.size === 0) throw new Error('Choose a non-empty PDF, PNG, JPEG, WebP or DXF drawing.');
  if (file.size > MAX_LOCAL_BYTES) throw new Error('Choose a file no larger than 25 MB for local preview.');
  const bytes = new Uint8Array(await file.slice(0,256).arrayBuffer());
  const ascii = (start,end) => String.fromCharCode(...bytes.slice(start,end));
  if (ascii(0,5) === '%PDF-') return 'application/pdf';
  if ([137,80,78,71,13,10,26,10].every((n,i)=>bytes[i]===n)) return 'image/png';
  if (bytes[0]===255 && bytes[1]===216 && bytes[2]===255) return 'image/jpeg';
  if (ascii(0,4)==='RIFF' && ascii(8,12)==='WEBP') return 'image/webp';
  // ASCII DXF opens with group code 0 and SECTION (optionally after a 999 comment); binary DXF has its own sentinel.
  const head = ascii(0,bytes.length);
  if (/^\s*(?:999\r?\n[^\n]*\n\s*)?0\r?\n\s*SECTION/.test(head) || head.startsWith('AutoCAD Binary DXF')) return 'image/vnd.dxf';
  throw new Error('This file is not a supported PDF, PNG, JPEG, WebP or DXF. Export other drawing formats first.');
}
