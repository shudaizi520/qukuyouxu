import {test} from 'node:test';
import assert from 'node:assert/strict';
import * as playerModule from '../src/helper/static/playlist-player.js';

test('imported tracks keep Plex artwork through both queue normalization passes',()=>{
 assert.equal(typeof playerModule.normalizePlaybackTrack,'function');
 const fromSource=playerModule.normalizePlaybackTrack({
  plex_track_id:'10',title:'歌曲',artists:['甲','乙'],album:'专辑',
  plex_duration:193,duration_ms:194000,thumb:'/library/metadata/10/thumb/1',
  user_rating:10,liked:true,source_track_key:'source-10',
 },'profile-1');
 assert.deepEqual(fromSource,{
  id:'10',title:'歌曲',artist:'甲 / 乙',album:'专辑',duration:193,
  thumb:'/library/metadata/10/thumb/1',user_rating:10,liked:true,
  profile_id:'profile-1',source_track_key:'source-10',
 });

 const fromMessage=playerModule.normalizePlaybackTrack(fromSource,'profile-1');
 assert.equal(fromMessage.thumb,'/library/metadata/10/thumb/1');
 assert.equal(fromMessage.id,'10');

 const legacyPayload=playerModule.normalizePlaybackTrack({plex_track_id:'11'},'profile-1');
 assert.equal(legacyPayload.thumb,'plex-track');
});

test('the compact player reuses the original stable artwork route',()=>{
 assert.equal(typeof playerModule.compactArtworkUrl,'function');
 assert.equal(
  playerModule.compactArtworkUrl({id:'10',profile_id:'profile-1'},{profileId:'profile-2'}),
  '/api/playlists/library/tracks/10/artwork?profile_id=profile-1',
 );
 assert.equal(
  playerModule.compactArtworkUrl({id:'11'},{profileId:'profile-2'}),
  '/api/playlists/library/tracks/11/artwork?profile_id=profile-2',
 );
});

class Control extends EventTarget{
 constructor(){super();this.dataset={};this.attributes={};this.value='1';this.textContent='';}
 setAttribute(name,value){this.attributes[name]=String(value);}
 contains(node){return node===this;}
 click(){this.dispatchEvent(new Event('click',{bubbles:true}));}
}

test('volume trigger pins the panel without muting and mute stays inside the panel',()=>{
 assert.equal(typeof playerModule.createVolumePopover,'function');
 const audio={volume:.48,muted:false};
 const root=new Control(),trigger=new Control(),panel=new Control(),mute=new Control(),slider=new Control(),percent=new Control();
 root.contains=node=>[root,trigger,panel,mute,slider,percent].includes(node);
 const documentTarget=new EventTarget();
 const control=playerModule.createVolumePopover({audio,root,trigger,panel,mute,slider,percent,documentTarget});

 trigger.click();
 assert.equal(root.dataset.open,'true');
 assert.equal(trigger.attributes['aria-expanded'],'true');
 assert.equal(audio.muted,false);
 assert.equal(percent.textContent,'48%');

 mute.click();
 assert.equal(audio.muted,true);
 assert.equal(root.dataset.open,'true');
 assert.equal(mute.attributes['aria-label'],'取消静音');

 slider.value='.25';
 slider.dispatchEvent(new Event('input'));
 assert.equal(audio.volume,.25);
 assert.equal(audio.muted,false);
 assert.equal(percent.textContent,'25%');

 documentTarget.dispatchEvent(new Event('click'));
 assert.equal(root.dataset.open,'false');
 assert.equal(trigger.attributes['aria-expanded'],'false');
 control.destroy();
});
