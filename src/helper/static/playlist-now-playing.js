import {createPlaybackVisualizer} from './playlist-visualizer.js';
import {stageArtworkImage} from './playlist-artwork.js';

export function activeLyricIndex(lines,timeMs){
 if(!Array.isArray(lines)||!lines.length||!Number.isFinite(Number(timeMs))||Number(timeMs)<0)return -1;
 let low=0,high=lines.length-1,result=-1;
 while(low<=high){
  const middle=Math.floor((low+high)/2),start=Number(lines[middle]?.start_ms);
  if(Number.isFinite(start)&&start<=Number(timeMs)){result=middle;low=middle+1;}
  else high=middle-1;
 }
 return result;
}

export function createBoundedCache(limit=20){
 const maximum=Math.max(1,Number(limit)||20),values=new Map();
 return {
  get(key){
   if(!values.has(key))return undefined;
   const value=values.get(key);values.delete(key);values.set(key,value);return value;
  },
  set(key,value){
   values.delete(key);values.set(key,value);
   while(values.size>maximum)values.delete(values.keys().next().value);
  },
  clear(){values.clear();},
 };
}

function validLyrics(value){
 if(!value||!['timed','plain','none'].includes(value.kind)||!Array.isArray(value.lines))throw Error('歌词数据格式无效');
 return value;
}

export function createLatestLyricsLoader({load,onResult,onError}){
 let generation=0,currentKey='';
 async function open(key){
  const request=++generation;currentKey=String(key||'');
  try{
   const value=validLyrics(await load(currentKey));
   if(request===generation&&currentKey===String(key||''))onResult(currentKey,value);
   return value;
  }catch(error){
   if(request===generation&&currentKey===String(key||''))onError(currentKey,error instanceof Error?error:Error('歌词暂时无法读取'));
   return null;
  }
 }
 function cancel(){generation+=1;currentKey='';}
 return {open,cancel};
}

export function createNowPlaying({document,requestJson,getProfileId,lyricsUrl,artworkUrl,togglePlayback=()=>{}}){
 const byId=id=>document.getElementById(id);
 const root=byId('nowPlaying'),trigger=byId('playerDetail'),closeButton=byId('nowPlayingClose');
 const artwork=byId('nowPlayingArtwork'),title=byId('nowPlayingTitle'),meta=byId('nowPlayingMeta');
 const lyrics=byId('nowPlayingLyrics'),status=byId('nowPlayingLyricsStatus'),visualizer=byId('nowPlayingVisualizer');
 const waveform=createPlaybackVisualizer({canvas:visualizer,media:byId('playerAudio'),view:document.defaultView||globalThis});
 const cache=createBoundedCache(20);
 let snapshot=null,currentKey='',currentLyrics={kind:'none',lines:[]},lineNodes=[];
 let activeIndex=-1,manualScrollUntil=0,returnFocus=null;

 function setStatus(message){status.textContent=message;status.hidden=!message;}
 function renderLyrics(value){
  currentLyrics=value;activeIndex=-1;lineNodes=[];lyrics.replaceChildren();
  if(value.kind==='none'||!value.lines.length){setStatus('暂无歌词');return;}
  setStatus('');
  const fragment=document.createDocumentFragment();
  for(const row of value.lines){
   const line=document.createElement('p');line.className='now-playing-lyric-line';line.textContent=row.text||'';
   fragment.append(line);lineNodes.push(line);
  }
  lyrics.append(fragment);syncLyric();
 }
 function syncLyric(){
  if(currentLyrics.kind!=='timed')return;
  const next=activeLyricIndex(currentLyrics.lines,Number(snapshot?.currentTime||0)*1000);
  if(next===activeIndex)return;
  if(activeIndex>=0)lineNodes[activeIndex]?.classList.remove('active');
  activeIndex=next;
  if(next<0)return;
  const node=lineNodes[next];node?.classList.add('active');
  if(node&&Date.now()>=manualScrollUntil)node.scrollIntoView({block:'center',behavior:'smooth'});
 }
 const loader=createLatestLyricsLoader({
  load:key=>requestJson(lyricsUrl(key.split('\t')[1],key.split('\t')[0])),
  onResult:(key,value)=>{cache.set(key,value);renderLyrics(value);},
  onError:()=>{currentLyrics={kind:'none',lines:[]};lyrics.replaceChildren();setStatus('歌词暂时无法读取');},
 });
 function loadLyrics(track){
  const profile=String(track?.profile_id||getProfileId()||''),trackId=String(track?.id||'');
  const key=profile+'\t'+trackId;currentKey=key;
  if(!/^\d+$/.test(trackId)){loader.cancel();renderLyrics({kind:'none',lines:[]});return;}
  const saved=cache.get(key);
  if(saved){loader.cancel();renderLyrics(saved);return;}
  lyrics.replaceChildren();currentLyrics={kind:'none',lines:[]};lineNodes=[];activeIndex=-1;setStatus('正在读取歌词…');
  loader.open(key);
 }
 function paintTrack(track,nextArtworkUrl){
  title.textContent=track?.title||(track?'未知歌曲':'未播放');
  meta.textContent=track?[track.artist||'未知歌手',track.album||''].filter(Boolean).join(' · '):'请选择歌曲';
  if(nextArtworkUrl){
   root.style.removeProperty('--now-playing-image');
   const image=document.createElement('img');image.alt='';
   stageArtworkImage(artwork,image,nextArtworkUrl,{
    onReady:()=>root.style.setProperty('--now-playing-image','url('+JSON.stringify(image.src)+')'),
    onFailure:()=>root.style.removeProperty('--now-playing-image'),
   });
  }else{
   artwork.replaceChildren();artwork.textContent='♫';root.style.removeProperty('--now-playing-image');
  }
 }
 function update(next){
  snapshot=next||null;
  root.dataset.playing=String(!!snapshot?.playing);
  waveform.setPlaying(!!snapshot?.playing);
  const track=snapshot?.track;
  if(!track){currentKey='';loader.cancel();renderLyrics({kind:'none',lines:[]});paintTrack(null,'');return;}
  const profile=String(track.profile_id||getProfileId()||''),key=profile+'\t'+String(track.id||'');
  if(key!==currentKey){paintTrack(track,artworkUrl(track,profile));loadLyrics(track);}
  else syncLyric();
 }
 function open(){
  if(!snapshot?.track)return;
  returnFocus=document.activeElement;root.hidden=false;document.body.classList.add('now-playing-open');root.focus({preventScroll:true});
 }
 function close(){
  if(root.hidden)return;
  root.hidden=true;document.body.classList.remove('now-playing-open');
  if(returnFocus&&returnFocus.isConnected)returnFocus.focus();
 }
 function reset(){
  close();snapshot=null;root.dataset.playing='false';waveform.setPlaying(false);currentKey='';loader.cancel();renderLyrics({kind:'none',lines:[]});paintTrack(null,'');
 }
 function mount(){
  waveform.mount();root.dataset.playing='false';trigger.onclick=open;
  closeButton.onclick=close;
  const hold=()=>{manualScrollUntil=Date.now()+4000;};lyrics.onwheel=hold;lyrics.onpointerdown=hold;lyrics.ontouchstart=hold;
  (document.defaultView||globalThis).addEventListener('keydown',event=>{
   if(root.hidden)return;
   if(event.code==='Space'&&!event.target?.closest?.('input,textarea,select,[contenteditable=true]')){
    event.preventDefault();event.stopPropagation();if(!event.repeat)togglePlayback();return;
   }
   if(event.key==='Escape'){event.preventDefault();close();}
  });
 }
 return {mount,update,open,close,reset};
}
