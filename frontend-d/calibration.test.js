import test from 'node:test';
import assert from 'node:assert/strict';
import {calibrate,updateCalibration,undoCalibration} from './calibration-state.js';
test('reference distance uses source pixels and converts units without mutating points',()=>{
 const points=[[10,20],[310,420]],copy=structuredClone(points);
 assert.equal(calibrate(points,500,'cm',1000,760).metresPerPixel,0.01);
 assert.equal(calibrate(points,5,'m',1000,760).metresPerPixel,0.01);
 assert.equal(calibrate(points,5000,'mm',1000,760).metresPerPixel,0.01);
 assert.equal(calibrate(points,1,'ft',1000,760).metresPerPixel,0.3048/500);
 assert.deepEqual(points,copy);
});
test('calibration rejects coincident, out of page, missing and invalid distances',()=>{
 for(const [points,length,unit] of [[[[0,0],[0,0]],1,'m'],[[[-1,0],[5,5]],1,'m'],[[[0,0],[1001,5]],1,'m'],[[[0,0],[5,5]],0,'m'],[[[0,0],[5,5]],Infinity,'m'],[[[0,0],[5,5]],1,'unknown'],[null,1,'m']])assert.throws(()=>calibrate(points,length,unit,1000,760));
});
test('reset and replacements undo back to original with isolated histories',()=>{
 const original={value:null,history:[]},a=calibrate([[0,0],[100,0]],1,'m',1000,760);
 const first=updateCalibration(original,a),second=updateCalibration(first,{...a,length:2});
 const reset=updateCalibration(second,null);
 assert.deepEqual(undoCalibration(reset),second);assert.deepEqual(undoCalibration(second),first);assert.deepEqual(undoCalibration(first),original);assert.deepEqual(original,{value:null,history:[]});
});

test('shared dataset scene uses its own page dimensions and preserves reported quantities',async()=>{
 const {readFile}=await import('node:fs/promises');
 const {adaptScene,adaptAnswer}=await import('./contracts.js');
 const raw=JSON.parse((await readFile(new URL('../fixtures/scene.example.json',import.meta.url),'utf8')).replace(/^\uFEFF/,''));
 const answer=JSON.parse((await readFile(new URL('../fixtures/answer.example.json',import.meta.url),'utf8')).replace(/^\uFEFF/,''));
 const before=JSON.stringify({raw,answer}),scene=adaptScene(raw,answer),page=raw.pages[0];
 const result=calibrate([[0,0],[page.width/2,0]],12.4,'m',page.width,page.height);
 assert.equal(page.width,2480);assert.equal(result.metresPerPixel,0.01);
 assert.equal(adaptAnswer(answer,scene).answer,72.01);
 assert.equal(JSON.stringify({raw,answer}),before);
});
