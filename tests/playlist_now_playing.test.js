import {test} from 'node:test';
import assert from 'node:assert/strict';
import {
 activeLyricIndex,createBoundedCache,createLatestLyricsLoader,
} from '../src/helper/static/playlist-now-playing.js';

test('active lyric follows the latest line at or before playback time',()=>{
 const lines=[
  {start_ms:0,text:'第一句'},
  {start_ms:1500,text:'第二句'},
  {start_ms:3200,text:'第三句'},
 ];
 assert.equal(activeLyricIndex(lines,-1),-1);
 assert.equal(activeLyricIndex(lines,0),0);
 assert.equal(activeLyricIndex(lines,1499),0);
 assert.equal(activeLyricIndex(lines,1500),1);
 assert.equal(activeLyricIndex(lines,999999),2);
 assert.equal(activeLyricIndex([],1000),-1);
});

test('bounded lyric cache refreshes recency and evicts the oldest track',()=>{
 const cache=createBoundedCache(2);
 cache.set('a',{kind:'plain'});cache.set('b',{kind:'plain'});
 assert.equal(cache.get('a').kind,'plain');
 cache.set('c',{kind:'timed'});
 assert.equal(cache.get('b'),undefined);
 assert.equal(cache.get('a').kind,'plain');
 assert.equal(cache.get('c').kind,'timed');
});

test('late lyric responses cannot replace the current track',async()=>{
 const pending=new Map();
 const results=[];
 const loader=createLatestLyricsLoader({
  load:key=>new Promise(resolve=>pending.set(key,resolve)),
  onResult:(key,value)=>results.push([key,value.kind]),
  onError:()=>assert.fail('no request should fail'),
 });
 const first=loader.open('first');
 const second=loader.open('second');
 pending.get('second')({kind:'timed',lines:[]});
 await second;
 pending.get('first')({kind:'plain',lines:[]});
 await first;
 assert.deepEqual(results,[['second','timed']]);
});

test('invalid lyric payload becomes a component error without throwing',async()=>{
 const errors=[];
 const loader=createLatestLyricsLoader({
  load:async()=>({kind:'timed',lines:'not-an-array'}),
  onResult:()=>assert.fail('invalid data must not render'),
  onError:(_key,error)=>errors.push(error.message),
 });
 await loader.open('track');
 assert.deepEqual(errors,['歌词数据格式无效']);
});
