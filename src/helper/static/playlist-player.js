import {createPlaybackNavigator,normalizePlaybackMode} from './playlist-playback-mode.js';
import {artworkUrl,stageArtworkImage} from './playlist-artwork.js';

const byId=(document,id)=>document.getElementById(id);
const PROGRESS_INTERVAL=15;
const MODE_KEY='pch-playback-mode';
const MODE_LABELS={shuffle:'随机播放',sequential:'顺序播放',single:'单曲循环',list:'列表循环'};
const MODE_ICON_IDS={shuffle:'#icon-mode-shuffle',sequential:'#icon-mode-sequential',single:'#icon-mode-single',list:'#icon-mode-list'};

export function normalizePlaybackTrack(track={},profileId=''){
 const directDuration=Number(track.duration),plexDuration=Number(track.plex_duration),sourceDuration=Number(track.duration_ms)/1000;
 const duration=[directDuration,plexDuration,sourceDuration].find(value=>Number.isFinite(value)&&value>0)||0;
 const artists=Array.isArray(track.artists)?track.artists.filter(Boolean).join(' / '):'';
 return {
  id:String(track.id??track.plex_track_id??''),title:String(track.title||'未知歌曲'),
  artist:String(track.artist||artists||'未知歌手'),album:String(track.album||''),duration,
  thumb:String(track.thumb||(track.plex_track_id?'plex-track':'')),user_rating:Number(track.user_rating||0),liked:!!track.liked,
  profile_id:String(profileId||track.profile_id||''),source_track_key:String(track.source_track_key||''),
 };
}

export function compactArtworkUrl(track={},context={}){
 return artworkUrl(track.id,track.profile_id||context?.profileId||'');
}

export function createVolumePopover({audio,root,stateRoot=root,trigger,mute,slider,percent,documentTarget}){
 let lastAudibleVolume=Number(audio.volume)>0?Number(audio.volume):1;
 const open=value=>{
  root.dataset.open=String(!!value);
  trigger.setAttribute('aria-expanded',String(!!value));
 };
 const sync=()=>{
  const volume=Math.max(0,Math.min(1,Number(audio.volume)||0));
  if(volume>0)lastAudibleVolume=volume;
  slider.value=String(volume);
  percent.textContent=String(Math.round(volume*100))+'%';
  const muted=!!audio.muted||volume===0,label=muted?'取消静音':'静音';
  stateRoot.dataset.muted=String(muted);
  mute.setAttribute('aria-label',label);mute.title=label;
 };
 const togglePanel=event=>{event?.stopPropagation?.();open(root.dataset.open!=='true');};
 const toggleMute=event=>{
  event?.stopPropagation?.();
  if(audio.muted||Number(audio.volume)===0){
   if(Number(audio.volume)===0)audio.volume=lastAudibleVolume;
   audio.muted=false;
  }else audio.muted=true;
  sync();
 };
 const changeVolume=()=>{
  audio.volume=Math.max(0,Math.min(1,Number(slider.value)||0));
  if(audio.volume>0)lastAudibleVolume=audio.volume;
  audio.muted=audio.volume===0;sync();
 };
 const outside=event=>{if(!root.contains(event.target))open(false);};
 const keydown=event=>{if(event.key==='Escape'){open(false);trigger.focus?.();}};
 trigger.addEventListener('click',togglePanel);mute.addEventListener('click',toggleMute);
 slider.addEventListener('input',changeVolume);documentTarget.addEventListener('click',outside);
 documentTarget.addEventListener('keydown',keydown);audio.addEventListener?.('volumechange',sync);
 open(false);sync();
 return {sync,destroy(){
  trigger.removeEventListener('click',togglePanel);mute.removeEventListener('click',toggleMute);
  slider.removeEventListener('input',changeVolume);documentTarget.removeEventListener('click',outside);
  documentTarget.removeEventListener('keydown',keydown);audio.removeEventListener?.('volumechange',sync);
 }};
}

export function createPlaylistPlayer({document,mediaUrl,formatTime,onStateChange=()=>{},reportPlayback=()=>Promise.resolve(),onLikedChange=()=>Promise.resolve(),storage=null}){
 const audio=byId(document,'playerAudio');
 const artwork=byId(document,'playerArtwork');
 const player=byId(document,'playlistPlayer');
 const playerToggle=byId(document,'playerToggle');
 const playerLiked=byId(document,'playerLiked');
 let queue=[];
 let context=null;
 let queueIndex=-1;
 let activeTrackId='';
 let activeSource='';
 let playbackGeneration=0;
 let sourceOffset=0;
 let trackDuration=0;
 let retryCount=0;
 let recoveryPending=false;
 let resettingSource=false;
 let retryTimer=0;
 let hasReportedPlay=false;
 let terminalReported=false;
 let lastProgressReport=0;
 let isScrubbing=false;
 const navigator=createPlaybackNavigator();
 let playbackMode='list';
 let modeStorage=storage;
 if(!modeStorage){try{modeStorage=document.defaultView?.localStorage||globalThis.localStorage;}catch(_error){modeStorage=null;}}
 try{playbackMode=normalizePlaybackMode(modeStorage?.getItem(MODE_KEY));}catch(_error){playbackMode='list';}
 navigator.setMode(playbackMode);

 function sameContext(candidate){
  return !!candidate&&!!context&&candidate.kind===context.kind&&candidate.key===context.key&&candidate.profileId===context.profileId;
 }
 function currentSource(){return audio.currentSrc||audio.src||'';}
 function absoluteSource(value){
  try{return new URL(value,document.baseURI).href;}catch(_error){return value;}
 }
 function sourceMatches(value){return absoluteSource(value)===absoluteSource(activeSource);}
 function boundedSeconds(value,maximum=86400){
  const number=Number(value);return Number.isFinite(number)&&number>=0?Math.min(number,maximum):0;
 }
 function logicalPosition(){
  const position=sourceOffset+boundedSeconds(audio.currentTime);
  return trackDuration?Math.min(position,trackDuration):position;
 }
 function availableDuration(){
  return trackDuration||(Number.isFinite(audio.duration)&&audio.duration>0?sourceOffset+audio.duration:0);
 }
 function snapshot(){
  const track=queue[queueIndex]||null;
  return Object.freeze({
   track:track?Object.freeze({...track}):null,
   currentTime:logicalPosition(),duration:availableDuration(),playing:!audio.paused,
   queueIndex,queueLength:queue.length,mode:playbackMode,
   artworkUrl:track?.thumb?compactArtworkUrl(track,context):'',
  });
 }
 function emitState(timeline=false){onStateChange(snapshot(),{timeline});}
 function paintTimeline(seek){seek.parentElement?.style.setProperty('--playlist-played',String(Number(seek.value)/10)+'%');}
 function updateTimeline(){
  const position=logicalPosition(),duration=availableDuration();
  byId(document,'playerCurrent').textContent=formatTime(position);
  byId(document,'playerDuration').textContent=formatTime(duration);
  const seek=byId(document,'playerSeek');
  if(!isScrubbing){
   seek.value=duration?String(Math.round(Math.min(1,position/duration)*1000)):'0';
   paintTimeline(seek);
  }
  emitState(true);
 }
 function report(event,{keepalive=false}={}){
  const track=queue[queueIndex];
  if(!track||!context||!String(track.id||'').match(/^\d+$/))return Promise.resolve();
  const position=logicalPosition();
  if(event==='play')hasReportedPlay=true;
  if(['play','resume','pause','progress'].includes(event))lastProgressReport=position;
  if(event==='stop'||event==='scrobble')terminalReported=true;
  const payload={
   track_id:String(track.id),position,duration:trackDuration,
   profileId:String(track.profile_id||context.profileId||''),
  };
  return Promise.resolve(reportPlayback(event,payload,{keepalive})).catch(()=>{});
 }
 function finishCurrent(event='stop',keepalive=false){
  if(!activeTrackId||terminalReported)return Promise.resolve();
  return report(event,{keepalive});
 }
 function clearFeedback(){
  byId(document,'playerFeedback').hidden=true;
  byId(document,'playerFeedbackMessage').textContent='';
 }
 function setPlaying(value){
  player.dataset.playing=String(value);
  if(value)playerToggle.dataset.state='playing';
  else playerToggle.dataset.state='paused';
  playerToggle.setAttribute('aria-label',value?'暂停':'播放');
 }
 function syncLiked(track){
  const trackId=String(track?.id||'');
  if(trackId){for(const row of queue){if(String(row?.id||'')===trackId){row.liked=!!track.liked;row.user_rating=track.user_rating;}}}
  const active=queue[queueIndex],liked=!!active?.liked,label=liked?'取消喜欢':'喜欢';
  playerLiked.textContent=liked?'♥':'♡';playerLiked.setAttribute('aria-pressed',String(liked));playerLiked.setAttribute('aria-label',label);playerLiked.title=label;
  playerLiked.disabled=!active||!String(active.id||'').match(/^\d+$/);
 }
 function showFeedback(message=''){
  const title=byId(document,'playerTitle').textContent||'这首歌';
  byId(document,'playerFeedbackMessage').textContent=message||'无法播放《'+title+'》，你可以重试或播放下一首。';
  byId(document,'playerFeedback').hidden=false;
  setPlaying(false);emitState();
 }
 function updateArtwork(track){
  if(!track?.thumb){artwork.replaceChildren(document.createTextNode('♫'));artwork.classList.remove('has-image');return;}
  artwork.classList.remove('has-image');
  const image=document.createElement('img');image.alt='';
  stageArtworkImage(artwork,image,compactArtworkUrl(track,context),{
   onReady:()=>artwork.classList.add('has-image'),
   onFailure:()=>artwork.classList.remove('has-image'),
  });
 }
 function handleFailure(generation,error,failedSource=activeSource){
  if(generation!==playbackGeneration||resettingSource)return;
  if(failedSource&&absoluteSource(failedSource)!==absoluteSource(activeSource))return;
  if(error?.name==='AbortError')return;
  if(error?.name==='NotAllowedError'){
   showFeedback('浏览器需要你再点一次播放。');return;
  }
  if(recoveryPending)return;
  if(retryCount<1){
   retryCount+=1;recoveryPending=true;
   retryTimer=setTimeout(()=>{
    if(generation!==playbackGeneration)return;
    recoveryPending=false;replaceSource(true,false,sourceOffset);
   },260);
   return;
  }
  showFeedback();
 }
 function attemptPlay(generation=playbackGeneration){
  if(generation!==playbackGeneration||!activeSource)return Promise.resolve();
  let result;
  try{result=audio.play();}catch(error){handleFailure(generation,error);return Promise.resolve();}
  return result?.catch(error=>handleFailure(generation,error))||Promise.resolve();
 }
 function togglePlayback(){
  if(audio.paused)return attemptPlay();
  audio.pause();return Promise.resolve();
 }
 function retryNow(){
  if(!activeSource)return;
  clearTimeout(retryTimer);recoveryPending=false;retryCount=1;clearFeedback();
  replaceSource(true,false,sourceOffset);
 }
 function sourceFor(track,offset=0){
  const source=mediaUrl('audio',track,context);
  if(!offset)return source;
  const url=new URL(source,document.baseURI);url.searchParams.set('offset',String(offset));
  return url.pathname+url.search;
 }
 function bindSourceHandlers(generation,source){
  audio.onerror=()=>{if(generation!==playbackGeneration||!sourceMatches(source))return;handleFailure(generation,audio.error,currentSource());};
  audio.ontimeupdate=()=>{if(generation!==playbackGeneration||!sourceMatches(source))return;updateTimeline();const position=logicalPosition();if(hasReportedPlay&&!audio.paused&&(position-lastProgressReport>=PROGRESS_INTERVAL||position<lastProgressReport-5))report('progress');};
  audio.onplaying=()=>{if(generation!==playbackGeneration||!sourceMatches(source))return;clearFeedback();setPlaying(true);report(hasReportedPlay?'resume':'play');emitState();};
  audio.onpause=()=>{if(generation!==playbackGeneration||!sourceMatches(source))return;setPlaying(false);if(hasReportedPlay&&!terminalReported)report('pause');emitState();};
  audio.onended=()=>{if(generation!==playbackGeneration||!sourceMatches(source))return;if(trackDuration&&logicalPosition()+3<trackDuration){handleFailure(generation,new Error('音频流提前结束'),source);return;}finishCurrent('scrobble');advanceNext('ended');};
 }
 function replaceSource(autoplay=true,resetRetry=true,offsetSeconds=0){
  const track=queue[queueIndex];if(!track||!context)return;
  const generation=++playbackGeneration;
  clearTimeout(retryTimer);recoveryPending=false;if(resetRetry)retryCount=0;
  resettingSource=true;audio.pause();audio.removeAttribute('src');audio.load();
  sourceOffset=boundedSeconds(offsetSeconds,trackDuration||86400);activeSource=sourceFor(track,sourceOffset);bindSourceHandlers(generation,activeSource);
  audio.src=activeSource;resettingSource=false;updateTimeline();
  if(autoplay)attemptPlay(generation);
 }
 function startQueueTrack(index,autoplay=true,finishPrevious=true){
  const track=queue[index];if(!track||!context)return;
  if(finishPrevious)finishCurrent('stop');
  queueIndex=index;activeTrackId=String(track.id);trackDuration=boundedSeconds(track.duration);sourceOffset=0;
  isScrubbing=false;
  hasReportedPlay=false;terminalReported=false;lastProgressReport=0;
  clearFeedback();
  byId(document,'playerTitle').textContent=track.title||'未知歌曲';
  byId(document,'playerArtist').textContent=track.artist||'未知歌手';
  byId(document,'playerQueue').textContent=(index+1)+' / '+queue.length;
  setPlaying(false);updateArtwork(track);syncLiked(track);emitState();replaceSource(autoplay,true);
 }
 function startQueue(items,index,candidate){
  finishCurrent('stop');queue=Array.isArray(items)?items.slice():[];context={...candidate};
  navigator.setQueue(queue.map((_track,position)=>String(position)),String(index));startQueueTrack(index,true,false);
 }
 function playAt(items,index,candidate,autoplay=true){
  const track=items[index];if(!track)return;
  if(sameContext(candidate)&&String(track.id)===activeTrackId&&activeSource){
   if(audio.paused&&autoplay)attemptPlay();else if(!audio.paused)audio.pause();
   return;
  }
  finishCurrent('stop');queue=items.slice();context={...candidate};
  navigator.setQueue(queue.map((_item,position)=>String(position)),String(index));startQueueTrack(index,autoplay,false);
 }
 function seekTo(seconds){
  const target=boundedSeconds(seconds,trackDuration||86400);
  const localTarget=target-sourceOffset;
  if(Number.isFinite(audio.duration)&&audio.duration>0&&localTarget>=0&&localTarget<=audio.duration){
   for(let index=0;index<(audio.seekable?.length||0);index++){
    if(localTarget<audio.seekable.start(index)||localTarget>audio.seekable.end(index))continue;
    try{audio.currentTime=localTarget;updateTimeline();return;}catch(_error){break;}
   }
  }
  if(availableDuration()&&activeSource){replaceSource(!audio.paused,true,target);return;}
  showFeedback('当前格式暂不支持拖动。');updateTimeline();
 }
 function advanceNext(reason){
  const target=navigator.next(reason),index=target===null?-1:Number(target);
  if(Number.isInteger(index)&&index>=0&&index<queue.length){startQueueTrack(index);return;}
  if(reason==='ended'){setPlaying(false);updateTimeline();emitState();}
 }
 function playNext(){advanceNext('manual');}
 function playPrevious(){
  if(logicalPosition()>5){seekTo(0);return;}
  const target=navigator.previous(),index=target===null?-1:Number(target);
  if(Number.isInteger(index)&&index>=0&&index<queue.length)startQueueTrack(index);
 }
 function paintMode(){
  const label=MODE_LABELS[playbackMode],button=byId(document,'playerMode');
  button.setAttribute('aria-label',label);button.title=label;
  byId(document,'playerModeUse').setAttribute('href',MODE_ICON_IDS[playbackMode]);
  for(const option of byId(document,'playerModeMenu').querySelectorAll('[data-player-mode]'))option.setAttribute('aria-checked',String(option.dataset.playerMode===playbackMode));
 }
 function setMode(value){
  playbackMode=navigator.setMode(value);
  try{modeStorage?.setItem(MODE_KEY,playbackMode);}catch(_error){}
  paintMode();emitState();return playbackMode;
 }
 function stop(){
  finishCurrent('stop');playbackGeneration+=1;clearTimeout(retryTimer);audio.onerror=null;audio.ontimeupdate=null;audio.onplaying=null;audio.onpause=null;audio.onended=null;audio.pause();audio.removeAttribute('src');audio.load();
  queue=[];context=null;queueIndex=-1;navigator.setQueue([],null);activeTrackId='';activeSource='';sourceOffset=0;trackDuration=0;retryCount=0;recoveryPending=false;resettingSource=false;
  isScrubbing=false;
  hasReportedPlay=false;terminalReported=false;lastProgressReport=0;
  clearFeedback();updateArtwork(null);byId(document,'playerTitle').textContent='未播放';byId(document,'playerArtist').textContent='请选择歌曲';byId(document,'playerQueue').textContent='0 / 0';
  byId(document,'playerCurrent').textContent='0:00';byId(document,'playerDuration').textContent='0:00';byId(document,'playerSeek').value='0';updateTimeline();syncLiked(null);setPlaying(false);emitState();
 }
 function flush(event='stop',keepalive=false){
  if(event==='stop'||event==='scrobble')return finishCurrent(event,keepalive);
  if(!hasReportedPlay||terminalReported)return Promise.resolve();
  return report(event,{keepalive});
 }
 function mount(){
  byId(document,'playerToggle').onclick=togglePlayback;
  byId(document,'playerPrevious').onclick=playPrevious;byId(document,'playerNext').onclick=playNext;
  const modeButton=byId(document,'playerMode'),modeMenu=byId(document,'playerModeMenu');
  modeButton.onclick=()=>{const open=modeMenu.hidden;modeMenu.hidden=!open;modeButton.setAttribute('aria-expanded',String(open));};
  for(const option of modeMenu.querySelectorAll('[data-player-mode]'))option.onclick=()=>{setMode(option.dataset.playerMode);modeMenu.hidden=true;modeButton.setAttribute('aria-expanded','false');modeButton.focus();};
  document.addEventListener('click',event=>{if(modeMenu.hidden||event.target.closest('.playlist-mode-control'))return;modeMenu.hidden=true;modeButton.setAttribute('aria-expanded','false');});
  byId(document,'playerRetry').onclick=retryNow;
  playerLiked.onclick=()=>{const track=queue[queueIndex];if(track&&!playerLiked.disabled)onLikedChange(track,!track.liked);};
  byId(document,'playerErrorNext').onclick=()=>{clearFeedback();playNext();};
  byId(document,'playerSeek').oninput=()=>{const seek=byId(document,'playerSeek'),duration=availableDuration();isScrubbing=true;paintTimeline(seek);if(duration)byId(document,'playerCurrent').textContent=formatTime(duration*Number(seek.value)/1000);};
  byId(document,'playerSeek').onchange=()=>{const seek=byId(document,'playerSeek'),duration=availableDuration();isScrubbing=false;if(!trackDuration&&duration)trackDuration=duration;if(duration)seekTo(duration*Number(seek.value)/1000);else updateTimeline();};
  createVolumePopover({
   audio,root:byId(document,'playerVolumeControl'),stateRoot:player,
   trigger:byId(document,'playerVolumeToggle'),mute:byId(document,'playerMute'),
   slider:byId(document,'playerVolume'),percent:byId(document,'playerVolumePercent'),
   documentTarget:document,
  });
  syncLiked(null);paintMode();emitState();
 }

 return {
  mount,startQueue,playAt,playNext,playPrevious,togglePlayback,stop,flush,syncLiked,setMode,snapshot,
  paused:()=>audio.paused,trackId:()=>activeTrackId,
  isContext:sameContext,
  isPlayingTrack:(track,candidate)=>sameContext(candidate)&&String(track?.id)===activeTrackId,
 };
}
