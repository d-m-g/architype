import test from 'node:test';import assert from 'node:assert/strict';import {floorPlates,visiblePlates} from './floor-stack-state.js';
const page={image:'data/a.png',width:1000,height:700},entity={id:'a',page:0,polygon:[[0,0],[10,0],[10,10]],label:'A'};
test('floor plates require a shared multi-page scene and retain page evidence',()=>{const snapshot={mode:'shared',wireScene:{pages:[page,{...page,image:'data/b.png'}]},entities:[entity,{...entity,id:'b',page:1}]};const p=floorPlates(snapshot);assert.deepEqual(p.map(x=>x.entities.map(e=>e.id)),[['a'],['b']]);assert.deepEqual(visiblePlates(p,'single',1).map(x=>x.index),[1]);assert.equal(visiblePlates(p,'stack',1).length,2);assert.deepEqual(floorPlates({...snapshot,wireScene:{pages:[page]}}),[]);assert.deepEqual(floorPlates({...snapshot,mode:'pending'}),[]);});

test('importable two-page fixture keeps images and evidence within shared schema',async()=>{
 const {readFile}=await import('node:fs/promises');const {adaptScene,adaptAnswer}=await import('./contracts.js');
 const scene=JSON.parse(await readFile(new URL('./fixtures/floor-stack.scene.json',import.meta.url),'utf8'));
 const answer=JSON.parse(await readFile(new URL('./fixtures/floor-stack.answer.json',import.meta.url),'utf8'));
 const adapted=adaptScene(scene,answer),plates=floorPlates({mode:'shared',wireScene:scene,entities:adapted.entities});
 assert.equal(plates.length,2);assert.equal(plates[1].entities.length,3);assert.equal(adaptAnswer(answer,adapted).missing.length,0);
});
