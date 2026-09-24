import {test} from 'node:test';
import assert from 'node:assert/strict';
import {buildVisualizerLevels,smoothVisualizerLevels} from '../src/helper/static/playlist-visualizer.js';

test('real spectrum detail forms one continuous lively center-weighted waveform',()=>{
 const bins=new Uint8Array(128);
 [255,210,245,150,235,120,205,85,180,70,155,60].forEach((value,index)=>{bins[index+1]=value;});
 bins[18]=190;bins[25]=140;bins[36]=170;bins[48]=110;
 const levels=buildVisualizerLevels(bins,121);
 const center=levels.slice(18,103);
 const edges=[...levels.slice(0,12),...levels.slice(-12)];
 const peaks=center.filter((value,index)=>index>0&&index<center.length-1&&value>center[index-1]&&value>center[index+1]&&value>.42);
 const tall=center.filter(value=>value>.35);
 const averageStep=center.slice(1).reduce((sum,value,index)=>sum+Math.abs(value-center[index]),0)/(center.length-1);
 let longestDarkRun=0,darkRun=0;
 center.forEach(value=>{darkRun=value<.08?darkRun+1:0;longestDarkRun=Math.max(longestDarkRun,darkRun);});

 assert.equal(levels.length,121);
 assert.ok(center.reduce((sum,value)=>sum+value,0)/center.length>
  2*(edges.reduce((sum,value)=>sum+value,0)/edges.length));
 assert.ok(peaks.length>=9);
 assert.ok(tall.length>=16&&tall.length<=72);
 assert.ok(averageStep>.085);
 assert.ok(longestDarkRun<=2);
 assert.notDeepEqual(levels.slice(0,50),levels.slice(-50).reverse());
});

test('per-frame smoothing reacts quickly and releases gradually',()=>{
 const previous=[.2,.8],target=[.8,.2];
 const next=smoothVisualizerLevels(previous,target);
 assert.ok(next[0]>.6&&next[0]<.8);
 assert.ok(next[1]>.6&&next[1]<.8);
 assert.ok(next[0]-.2>.8-next[1]);
});

test('silence leaves only the visual baseline',()=>{
 assert.deepEqual(buildVisualizerLevels(new Uint8Array(128),5),[0,0,0,0,0]);
});
