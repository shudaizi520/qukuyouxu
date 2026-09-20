const byId=(document,id)=>document.getElementById(id);

export function createPlaylistPlayer({document,mediaUrl,formatTime,onStateChange=()=>{}}){
 const audio=byId(document,'playerAudio');
 const artwork=byId(document,'playerArtwork');
 let queue=[];
 let context=null;
 let queueIndex=-1;
 let activeTrackId='';
 let activeSource='';
 let playbackGeneration=0;
 let retryCount=0;
 let recoveryPending=false;
 let resettingSource=false;
 let retryTimer=0;

 function sameContext(candidate){
  return !!candidate&&!!context&&candidate.kind===context.kind&&candidate.key===context.key&&candidate.profileId===context.profileId;
 }
 function currentSource(){return audio.currentSrc||audio.src||'';}
 function absoluteSource(value){
  try{return new URL(value,document.baseURI).href;}catch(_error){return value;}
 }
 function clearFeedback(){
  byId(document,'playerFeedback').hidden=true;
  byId(document,'playerFeedbackMessage').textContent='';
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
  image.onerror=()=>{artwork.replaceChildren(document.createTextNode('♫'));artwork.classList.remove('has-image');};
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
    recoveryPending=false;resettingSource=true;
    audio.pause();audio.removeAttribute('src');audio.load();audio.src=activeSource;
    setTimeout(()=>{
     if(generation!==playbackGeneration)return;
     resettingSource=false;attemptPlay(generation);
    },0);
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
  audio.load();attemptPlay(playbackGeneration);
 }
 function startQueueTrack(index,autoplay=true){
  const track=queue[index];if(!track||!context)return;
  const generation=++playbackGeneration;clearTimeout(retryTimer);retryCount=0;recoveryPending=false;resettingSource=false;
  queueIndex=index;activeTrackId=String(track.id);activeSource=mediaUrl('audio',track,context);
  clearFeedback();audio.pause();audio.onerror=()=>handleFailure(generation,audio.error,currentSource());audio.src=activeSource;
  byId(document,'playerTitle').textContent=track.title||'未知歌曲';
  byId(document,'playerArtist').textContent=track.artist||'未知歌手';
  byId(document,'playerQueue').textContent=(index+1)+' / '+queue.length;
  updateArtwork(track);onStateChange();
  if(autoplay)attemptPlay(playbackGeneration);
 }
 function startQueue(items,index,candidate){
  queue=Array.isArray(items)?items.slice():[];context={...candidate};startQueueTrack(index,true);
 }
 function playAt(items,index,candidate,autoplay=true){
  const track=items[index];if(!track)return;
  if(sameContext(candidate)&&String(track.id)===activeTrackId&&activeSource){
   if(audio.paused&&autoplay)attemptPlay();else if(!audio.paused)audio.pause();
   return;
  }
  queue=items.slice();context={...candidate};startQueueTrack(index,autoplay);
 }
 function playNext(){
  if(queueIndex+1<queue.length)startQueueTrack(queueIndex+1);
  else{audio.pause();audio.currentTime=0;}
 }
 function playPrevious(){
  if(audio.currentTime>5){audio.currentTime=0;return;}
  if(queueIndex>0)startQueueTrack(queueIndex-1);
 }
 function stop(){
  playbackGeneration+=1;clearTimeout(retryTimer);audio.onerror=null;audio.pause();audio.removeAttribute('src');audio.load();
  queue=[];context=null;queueIndex=-1;activeTrackId='';activeSource='';retryCount=0;recoveryPending=false;resettingSource=false;
  clearFeedback();byId(document,'playerTitle').textContent='未播放';byId(document,'playerArtist').textContent='请选择歌曲';byId(document,'playerQueue').textContent='0 / 0';onStateChange();
 }
 function mount(){
  byId(document,'playerToggle').onclick=()=>{if(audio.paused)attemptPlay();else audio.pause();};
  byId(document,'playerPrevious').onclick=playPrevious;byId(document,'playerNext').onclick=playNext;
  byId(document,'playerRetry').onclick=retryNow;
  byId(document,'playerErrorNext').onclick=()=>{clearFeedback();playNext();};
  byId(document,'playerSeek').oninput=()=>{if(Number.isFinite(audio.duration)&&audio.duration>0)audio.currentTime=audio.duration*Number(byId(document,'playerSeek').value)/1000;};
  byId(document,'playerVolume').oninput=()=>{audio.volume=Number(byId(document,'playerVolume').value);audio.muted=false;byId(document,'playerMute').textContent=audio.volume?'音量':'静音';};
  byId(document,'playerMute').onclick=()=>{audio.muted=!audio.muted;byId(document,'playerMute').textContent=audio.muted?'取消静音':'音量';};
  audio.addEventListener('ended',playNext);
  audio.addEventListener('timeupdate',()=>{const duration=Number.isFinite(audio.duration)?audio.duration:0;byId(document,'playerCurrent').textContent=formatTime(audio.currentTime);byId(document,'playerDuration').textContent=formatTime(duration);byId(document,'playerSeek').value=duration?String(Math.round(audio.currentTime/duration*1000)):'0';});
  audio.addEventListener('playing',()=>{clearFeedback();byId(document,'playerToggle').textContent='❚❚';onStateChange();});
  audio.addEventListener('pause',()=>{byId(document,'playerToggle').textContent='▶';onStateChange();});
 }

 return {
  mount,startQueue,playAt,playNext,playPrevious,stop,
  paused:()=>audio.paused,trackId:()=>activeTrackId,
  isContext:sameContext,
  isPlayingTrack:(track,candidate)=>sameContext(candidate)&&String(track?.id)===activeTrackId,
 };
}
