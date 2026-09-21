const byId=(document,id)=>document.getElementById(id);
const PROGRESS_INTERVAL=15;

export function createPlaylistPlayer({document,mediaUrl,formatTime,onStateChange=()=>{},reportPlayback=()=>Promise.resolve()}){
 const audio=byId(document,'playerAudio');
 const artwork=byId(document,'playerArtwork');
 const player=byId(document,'playlistPlayer');
 const playerToggle=byId(document,'playerToggle');
 const playerMute=byId(document,'playerMute');
 const playerVolume=byId(document,'playerVolume');
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
 let lastAudibleVolume=audio.volume||1;
 let hasReportedPlay=false;
 let terminalReported=false;
 let lastProgressReport=0;

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
 function updateTimeline(){
  const position=logicalPosition(),duration=trackDuration||(
   Number.isFinite(audio.duration)&&audio.duration>0?sourceOffset+audio.duration:0
  );
  byId(document,'playerCurrent').textContent=formatTime(position);
  byId(document,'playerDuration').textContent=formatTime(duration);
  byId(document,'playerSeek').value=duration?String(Math.round(Math.min(1,position/duration)*1000)):'0';
 }
 function report(event,{keepalive=false}={}){
  const track=queue[queueIndex];
  if(!track||!context||context.kind==='preview'||!String(track.id||'').match(/^\d+$/))return Promise.resolve();
  const position=logicalPosition();
  if(event==='play')hasReportedPlay=true;
  if(['play','resume','pause','progress'].includes(event))lastProgressReport=position;
  if(event==='stop'||event==='scrobble')terminalReported=true;
  const payload={
   track_id:String(track.id),position,duration:trackDuration,
   profileId:String(context.profileId||''),
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
 function setMuted(value){
  const label=value?'取消静音':'静音';player.dataset.muted=String(value);
  playerMute.setAttribute('aria-label',label);playerMute.title=label;
 }
 function syncVolumeState(){
  const muted=audio.muted||audio.volume===0;setMuted(muted);
 }
 function showFeedback(message=''){
  const title=byId(document,'playerTitle').textContent||'这首歌';
  byId(document,'playerFeedbackMessage').textContent=message||'无法播放《'+title+'》，你可以重试或播放下一首。';
  byId(document,'playerFeedback').hidden=false;
 }
 function updateArtwork(track){
  artwork.replaceChildren();artwork.classList.toggle('has-image',!!track?.thumb);
  if(!track?.thumb){artwork.textContent='♫';return;}
  const image=document.createElement('img');image.alt='';image.src=mediaUrl('artwork',track,context);
  image.onerror=()=>{if(!image.isConnected)return;artwork.replaceChildren(document.createTextNode('♫'));artwork.classList.remove('has-image');};
  artwork.append(image);
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
    recoveryPending=false;replaceSource(logicalPosition(),true,false);
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
 function retryNow(){
  if(!activeSource)return;
  clearTimeout(retryTimer);recoveryPending=false;retryCount=1;clearFeedback();
  replaceSource(logicalPosition(),true,false);
 }
 function sourceFor(track,offsetSeconds){
  if(context.kind!=='preview'||!track.source){
   return mediaUrl('audio',track,context,{offsetSeconds});
  }
  if(!offsetSeconds)return track.source;
  try{
   const url=new URL(track.source,document.baseURI);url.searchParams.set('offset',String(offsetSeconds));
   return url.origin===new URL(document.baseURI).origin?url.pathname+url.search+url.hash:url.href;
  }catch(_error){return track.source;}
 }
 function bindSourceHandlers(generation,source){
  audio.onerror=()=>{if(generation!==playbackGeneration||!sourceMatches(source))return;handleFailure(generation,audio.error,currentSource());};
  audio.ontimeupdate=()=>{if(generation!==playbackGeneration||!sourceMatches(source))return;updateTimeline();const position=logicalPosition();if(hasReportedPlay&&!audio.paused&&(position-lastProgressReport>=PROGRESS_INTERVAL||position<lastProgressReport-5))report('progress');};
  audio.onplaying=()=>{if(generation!==playbackGeneration||!sourceMatches(source))return;clearFeedback();setPlaying(true);report(hasReportedPlay?'resume':'play');onStateChange();};
  audio.onpause=()=>{if(generation!==playbackGeneration||!sourceMatches(source))return;setPlaying(false);if(hasReportedPlay&&!terminalReported)report('pause');onStateChange();};
  audio.onended=()=>{if(generation!==playbackGeneration||!sourceMatches(source))return;if(trackDuration&&logicalPosition()+3<trackDuration){handleFailure(generation,new Error('音频流提前结束'),source);return;}finishCurrent('scrobble');playNext();};
 }
 function replaceSource(offsetSeconds=0,autoplay=true,resetRetry=true){
  const track=queue[queueIndex];if(!track||!context)return;
  const limit=trackDuration||86400,target=boundedSeconds(offsetSeconds,limit);
  const generation=++playbackGeneration;
  clearTimeout(retryTimer);recoveryPending=false;if(resetRetry)retryCount=0;
  resettingSource=true;audio.pause();audio.removeAttribute('src');audio.load();
  sourceOffset=target;activeSource=sourceFor(track,target);bindSourceHandlers(generation,activeSource);
  audio.src=activeSource;resettingSource=false;updateTimeline();
  if(autoplay)attemptPlay(generation);
 }
 function startQueueTrack(index,autoplay=true,finishPrevious=true){
  const track=queue[index];if(!track||!context)return;
  if(finishPrevious)finishCurrent('stop');
  queueIndex=index;activeTrackId=String(track.id);trackDuration=boundedSeconds(track.duration);sourceOffset=0;
  hasReportedPlay=false;terminalReported=false;lastProgressReport=0;
  clearFeedback();
  byId(document,'playerTitle').textContent=track.title||'未知歌曲';
  byId(document,'playerArtist').textContent=track.artist||'未知歌手';
  byId(document,'playerQueue').textContent=(index+1)+' / '+queue.length;
  updateArtwork(track);onStateChange();replaceSource(0,autoplay,true);
 }
 function startQueue(items,index,candidate){
  finishCurrent('stop');queue=Array.isArray(items)?items.slice():[];context={...candidate};startQueueTrack(index,true,false);
 }
 function playAt(items,index,candidate,autoplay=true){
  const track=items[index];if(!track)return;
  if(sameContext(candidate)&&String(track.id)===activeTrackId&&activeSource){
   if(audio.paused&&autoplay)attemptPlay();else if(!audio.paused)audio.pause();
   return;
  }
  finishCurrent('stop');queue=items.slice();context={...candidate};startQueueTrack(index,autoplay,false);
 }
 function seekTo(seconds){
  const target=boundedSeconds(seconds,trackDuration||86400);
  if(Number.isFinite(audio.duration)&&audio.duration>0&&sourceOffset===0){
   audio.currentTime=Math.min(target,audio.duration);updateTimeline();return;
  }
  replaceSource(target,true,false);
 }
 function playNext(){
  if(queueIndex+1<queue.length)startQueueTrack(queueIndex+1);
  else{audio.pause();sourceOffset=0;audio.currentTime=0;updateTimeline();}
 }
 function playPrevious(){
  if(logicalPosition()>5){seekTo(0);return;}
  if(queueIndex>0)startQueueTrack(queueIndex-1);
 }
 function stop(){
  finishCurrent('stop');playbackGeneration+=1;clearTimeout(retryTimer);audio.onerror=null;audio.ontimeupdate=null;audio.onplaying=null;audio.onpause=null;audio.onended=null;audio.pause();audio.removeAttribute('src');audio.load();
  queue=[];context=null;queueIndex=-1;activeTrackId='';activeSource='';sourceOffset=0;trackDuration=0;retryCount=0;recoveryPending=false;resettingSource=false;
  hasReportedPlay=false;terminalReported=false;lastProgressReport=0;
  clearFeedback();updateArtwork(null);byId(document,'playerTitle').textContent='未播放';byId(document,'playerArtist').textContent='请选择歌曲';byId(document,'playerQueue').textContent='0 / 0';
  byId(document,'playerCurrent').textContent='0:00';byId(document,'playerDuration').textContent='0:00';byId(document,'playerSeek').value='0';setPlaying(false);onStateChange();
 }
 function playPreview(track){
  if(!track?.source)return;
  playAt([track],0,{kind:'preview',key:'external',profileId:String(track.profileId||'')});
 }
 function flush(event='stop',keepalive=false){
  if(event==='stop'||event==='scrobble')return finishCurrent(event,keepalive);
  if(!hasReportedPlay||terminalReported)return Promise.resolve();
  return report(event,{keepalive});
 }
 function mount(){
  byId(document,'playerToggle').onclick=()=>{if(audio.paused)attemptPlay();else audio.pause();};
  byId(document,'playerPrevious').onclick=playPrevious;byId(document,'playerNext').onclick=playNext;
  byId(document,'playerRetry').onclick=retryNow;
  byId(document,'playerErrorNext').onclick=()=>{clearFeedback();playNext();};
  byId(document,'playerSeek').oninput=()=>{if(trackDuration)seekTo(trackDuration*Number(byId(document,'playerSeek').value)/1000);};
  playerVolume.oninput=()=>{audio.volume=Number(playerVolume.value);if(audio.volume>0)lastAudibleVolume=audio.volume;audio.muted=audio.volume===0;syncVolumeState();};
  playerMute.onclick=()=>{if(audio.muted||audio.volume===0){if(audio.volume===0){audio.volume=lastAudibleVolume;playerVolume.value=String(lastAudibleVolume);}audio.muted=false;}else audio.muted=true;syncVolumeState();};
  syncVolumeState();
 }

 return {
  mount,startQueue,playAt,playPreview,playNext,playPrevious,stop,flush,
  paused:()=>audio.paused,trackId:()=>activeTrackId,
  isContext:sameContext,
  isPlayingTrack:(track,candidate)=>sameContext(candidate)&&String(track?.id)===activeTrackId,
 };
}
