import {test} from 'node:test';
import assert from 'node:assert/strict';
import {buildVisualizerLevels,smoothVisualizerLevels,visualizerGeometry,createPlaybackVisualizer} from '../src/helper/static/playlist-visualizer.js';

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
 assert.ok(peaks.length>=12&&peaks.length<=28);
 assert.ok(averageStep>.006);
 assert.notDeepEqual(levels.slice(0,50),levels.slice(-50).reverse());
});

test('per-frame smoothing reacts quickly and releases gradually',()=>{
 const previous=[.2,.8],target=[.8,.2];
 const next=smoothVisualizerLevels(previous,target);
 assert.ok(next[0]>.64&&next[0]<.8);
 assert.ok(next[1]>.64&&next[1]<.68);
 assert.ok(next[0]-.2>.8-next[1]);
});

test('silence leaves only the visual baseline',()=>{
 assert.deepEqual(buildVisualizerLevels(new Uint8Array(512),5),[0,0,0,0,0]);
});

test('desktop visualizer uses a compact field of slender bars',()=>{
 const geometry=visualizerGeometry(900);
 assert.ok(geometry.barWidth>=3&&geometry.barWidth<=4);
 assert.ok(geometry.gap>=5.5&&geometry.gap<=7);
 assert.ok(geometry.count>=56&&geometry.count<=64);
 assert.ok(geometry.span>=540&&geometry.span<=680);
});

function animationHarness(){
 let id=0,layoutReads=0;
 const frames=new Map();
 const context={setTransform(){},clearRect(){},save(){},restore(){},fillRect(){},createLinearGradient(){return {addColorStop(){}};}};
 const canvas={width:0,height:0,getContext:()=>context,getBoundingClientRect(){layoutReads++;return {width:600,height:80};}};
 const view={devicePixelRatio:1,matchMedia:()=>({matches:false}),addEventListener(){},removeEventListener(){},
  requestAnimationFrame(callback){frames.set(++id,callback);return id;},cancelAnimationFrame(key){frames.delete(key);}};
 const visualizer=createPlaybackVisualizer({canvas,media:{},view});
 return {visualizer,frames,layoutReads:()=>layoutReads,tick(){const [key,callback]=frames.entries().next().value;frames.delete(key);callback();}};
}

test('hidden visualizer stops drawing and resumes without restarting playback',()=>{
 const harness=animationHarness(),{visualizer,frames}=harness;
 visualizer.mount();visualizer.setPlaying(true);
 assert.equal(frames.size,1);
 visualizer.setVisible(false);
 assert.equal(frames.size,0);
 visualizer.setPlaying(true);
 assert.equal(frames.size,0);
 visualizer.setVisible(true);
 assert.equal(frames.size,1);
 harness.tick();
 assert.equal(frames.size,1);
 visualizer.destroy();
 assert.equal(frames.size,0);
});

test('animation frames reuse measured canvas dimensions',()=>{
 const harness=animationHarness();
 harness.visualizer.mount();harness.visualizer.setPlaying(true);
 const reads=harness.layoutReads();
 for(let index=0;index<5;index++)harness.tick();
 assert.equal(harness.layoutReads(),reads);
 harness.visualizer.destroy();
});
