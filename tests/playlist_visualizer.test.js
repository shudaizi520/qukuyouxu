import {test} from 'node:test';
import assert from 'node:assert/strict';
import {buildVisualizerLevels,smoothVisualizerLevels,visualizerGeometry} from '../src/helper/static/playlist-visualizer.js';

test('whitened frequency bands retain independent local detail',()=>{
 const samples=new Uint8Array(2048);
 let seed=17;
 for(let index=2;index<samples.length;index++){
  seed=(seed*48271)%2147483647;
  const jitter=(seed/2147483647-.5)*30;
  const tilt=196-31*Math.log10(index+1);
  const peak=70*Math.exp(-Math.pow((Math.log(index)-Math.log(180))/.12,2));
  samples[index]=Math.max(0,Math.min(255,Math.round(tilt+jitter+peak)));
 }
 const levels=buildVisualizerLevels(samples,121);
 const visible=levels.slice(4,-4);
 const peaks=visible.filter((value,index)=>index>1&&index<visible.length-2
  &&value>visible[index-1]&&value>=visible[index+1]
  &&value>(visible[index-2]+visible[index+2])*.5+.018);
 const averageStep=visible.slice(1).reduce((sum,value,index)=>sum+Math.abs(value-visible[index]),0)/(visible.length-1);

 assert.equal(levels.length,121);
 assert.ok(visible.filter(value=>value>.04).length>=100);
 assert.ok(peaks.length>=5&&peaks.length<=18);
 assert.ok(averageStep>.006);
 assert.notDeepEqual(levels.slice(0,50),levels.slice(-50).reverse());
});

test('per-frame smoothing reacts quickly and releases gradually',()=>{
 const previous=[.2,.8],target=[.8,.2];
 const next=smoothVisualizerLevels(previous,target);
 assert.ok(next[0]>.64&&next[0]<.8);
 assert.ok(next[1]>.55&&next[1]<.65);
 assert.ok(next[0]-.2>.8-next[1]);
});

test('silence leaves only the visual baseline',()=>{
 assert.deepEqual(buildVisualizerLevels(new Uint8Array(512),5),[0,0,0,0,0]);
});

test('desktop visualizer uses a dense field of slender bars',()=>{
 const geometry=visualizerGeometry(900);
 assert.ok(geometry.barWidth>=3&&geometry.barWidth<=4);
 assert.ok(geometry.gap>=5.5&&geometry.gap<=7);
 assert.ok(geometry.count>=76&&geometry.count<=92);
 assert.ok(geometry.span>=780&&geometry.span<=880);
});
