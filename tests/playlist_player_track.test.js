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
