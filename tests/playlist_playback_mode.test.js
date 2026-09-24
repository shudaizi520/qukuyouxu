import {test} from 'node:test';
import assert from 'node:assert/strict';
import {
 createPlaybackNavigator,normalizePlaybackMode,PLAYBACK_MODES,
} from '../src/helper/static/playlist-playback-mode.js';

test('invalid playback modes fall back to list loop',()=>{
 assert.deepEqual(PLAYBACK_MODES,['shuffle','sequential','single','list']);
 assert.equal(normalizePlaybackMode('shuffle'),'shuffle');
 assert.equal(normalizePlaybackMode('unknown'),'list');
 assert.equal(normalizePlaybackMode(null),'list');
});

test('sequential playback stops at both queue boundaries',()=>{
 const navigator=createPlaybackNavigator();
 navigator.setQueue(['a','b','c'],'c');
 navigator.setMode('sequential');
 assert.equal(navigator.next('ended'),null);
 assert.equal(navigator.previous(),'b');
 navigator.setQueue(['a','b','c'],'a');
 assert.equal(navigator.previous(),null);
 assert.equal(navigator.next('manual'),'b');
});

test('list loop wraps while single repeat only repeats natural endings',()=>{
 const navigator=createPlaybackNavigator();
 navigator.setQueue(['a','b','c'],'c');
 navigator.setMode('list');
 assert.equal(navigator.next('ended'),'a');
 assert.equal(navigator.previous(),'c');
 navigator.setQueue(['a','b','c'],'a');
 navigator.setMode('single');
 assert.equal(navigator.next('ended'),'a');
 assert.equal(navigator.next('manual'),'b');
 assert.equal(navigator.previous(),'a');
 assert.equal(navigator.previous(),'c');
});

test('shuffle uses every other track before starting another round',()=>{
 const navigator=createPlaybackNavigator({random:()=>0});
 navigator.setQueue(['a','b','c','d'],'a');
 navigator.setMode('shuffle');
 const round=[navigator.next('ended'),navigator.next('ended'),navigator.next('ended')];
 assert.equal(new Set(round).size,3);
 assert.ok(round.every(id=>id!=='a'));
 const firstOfNextRound=navigator.next('ended');
 assert.notEqual(firstOfNextRound,round.at(-1));
});

test('shuffle previous and next walk existing history before drawing again',()=>{
 const navigator=createPlaybackNavigator({random:()=>0});
 navigator.setQueue(['a','b','c'],'a');
 navigator.setMode('shuffle');
 const second=navigator.next('manual');
 const third=navigator.next('manual');
 assert.equal(navigator.previous(),second);
 assert.equal(navigator.previous(),'a');
 assert.equal(navigator.next('manual'),second);
 assert.equal(navigator.next('manual'),third);
});

test('shuffle handles empty one-item and changed queues without invalid tracks',()=>{
 const navigator=createPlaybackNavigator({random:()=>0});
 navigator.setQueue([],null);
 navigator.setMode('shuffle');
 assert.equal(navigator.next('ended'),null);
 assert.equal(navigator.previous(),null);
 navigator.setQueue(['only'],'only');
 assert.equal(navigator.next('ended'),'only');
 assert.equal(navigator.previous(),'only');
 navigator.setQueue(['only','new'],'only');
 assert.equal(navigator.next('manual'),'new');
 navigator.setQueue(['only'],'only');
 assert.equal(navigator.next('manual'),'only');
});
