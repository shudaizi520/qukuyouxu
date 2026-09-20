(()=>{
'use strict';
const $=id=>document.getElementById(id);
const player=$('playerAudio');
const playerArtwork=$('playerArtwork');
let profiles=[];
let playlists=[];
let current=null;
let tracks=[];
let filtered=[];
let playQueue=[];
let playContext=null;
let queueIndex=-1;
let playingTrackId='';
let unavailablePlaylists=new Set();
let loadedProfileId='';
let playlistRequest=0;
let profileRequest=0;
let playlistLoading=false;
let pendingPlaylist=null;
let toolOpen=false;

function notify(message,error=false){const node=$('playlistNotice');node.hidden=!message;node.textContent=message||'';node.className='notice'+(error?' error':'');}
function showPlayerError(message=''){const title=$('playerTitle').textContent||'这首歌';$('playerFeedbackMessage').textContent=message||'无法播放《'+title+'》，你可以重试或播放下一首。';$('playerFeedback').hidden=false;}
function clearPlayerError(){$('playerFeedback').hidden=true;$('playerFeedbackMessage').textContent='';}
function attemptPlay(){const source=player.currentSrc||player.src;return player.play().catch(error=>{if(error?.name!=='AbortError'&&(player.currentSrc||player.src)===source)showPlayerError();});}
async function json(path,method='GET',body){return (await PCHAuth.request(path,method,body)).json();}
async function action(fn){try{return await (window.PCHUI?PCHUI.run(fn):fn());}catch(error){notify(error.message||'操作失败',true);}}
function profileLabel(row){const account=row.account?.username||row.name||'Plex 用户',library=row.library?.name||'音乐';return account+' · '+library;}
function formatTime(value){const seconds=Math.max(0,Math.floor(Number(value)||0));return Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0');}
function encoded(value){return encodeURIComponent(String(value||''));}
function mediaUrl(type,track,context=current){const profile=context?.profileId||loadedProfileId||PCHAuth.profile(),query=profile?'?profile_id='+encoded(profile):'';return '/api/playlists/'+encoded(context?.kind)+'/'+encoded(context?.key)+'/tracks/'+encoded(track.id)+'/'+type+query;}

async function loadProfiles(){
 const data=await json('/api/plex/profiles');profiles=Array.isArray(data.items)?data.items:[];
 const selected=PCHAuth.profile()||String(data.active_profile_id||profiles[0]?.id||'');
 const select=$('playlistProfile');select.replaceChildren();
 for(const row of profiles){const option=document.createElement('option');option.value=row.id;option.textContent=profileLabel(row);select.append(option);}
 if(profiles.some(row=>row.id===selected))select.value=selected;
 return select.value;
}
function renderPlaylistList(){
 const box=$('playlistList');box.replaceChildren();$('playlistCount').textContent=String(playlists.length);
 for(const item of playlists){
  const selected=toolOpen?pendingPlaylist:(pendingPlaylist||current);const button=document.createElement('button');button.type='button';button.className=item.kind===selected?.kind&&item.key===selected?.key?'active':'';if(unavailablePlaylists.has(item.kind+'\t'+item.key))button.classList.add('unavailable');
  const icon=document.createElement('span');icon.className='playlist-side-icon';icon.textContent=item.kind==='daily'?'日':item.kind==='smart'?'智':item.kind==='external'?'外':'类';
  const text=document.createElement('span'),title=document.createElement('strong'),count=document.createElement('small');title.textContent=item.title;count.textContent=item.playlist_id?(item.count??'—')+' 首':'尚未创建';text.append(title,count);button.append(icon,text);button.onclick=()=>action(()=>openPlaylist(item));box.append(button);
 }
 if(!playlists.length){const empty=document.createElement('span');empty.className='playlist-side-empty';empty.textContent='还没有歌单';box.append(empty);}
}
async function loadPlaylists(preferred,requestId=profileRequest){
 const data=await json('/api/playlists');if(requestId!==profileRequest)return;playlists=Array.isArray(data.items)?data.items:[];
 const selected=preferred&&playlists.find(row=>row.kind===preferred.kind&&row.key===preferred.key);
 const ordered=selected?[selected,...playlists.filter(row=>row!==selected)]:playlists.slice(),playable=ordered.filter(row=>row.playlist_id);unavailablePlaylists=new Set();renderPlaylistList();
 if(!playable.length){if(ordered[0])await openPlaylist(ordered[0]);return;}
 const lastError=await openFirstAvailable(playable,0,null,requestId);if(requestId!==profileRequest)return;
 if(current){if(lastError)notify('一个旧歌单暂时不可用，已打开其他歌单。',true);return;}
 tracks=[];filtered=[];$('playlistEmpty').textContent='没有可显示的歌单';renderPlaylistList();renderTracks();if(lastError)throw lastError;
}
async function openFirstAvailable(candidates,index=0,lastError=null,requestId=profileRequest){
 if(requestId!==profileRequest)return lastError;
 if(index>=candidates.length){current=null;return lastError;}
 try{await openPlaylist(candidates[index]);return lastError;}
 catch(error){if(requestId!==profileRequest)return lastError;unavailablePlaylists.add(candidates[index].kind+'\t'+candidates[index].key);renderPlaylistList();return openFirstAvailable(candidates,index+1,error,requestId);}
}
function setPlaylistLoading(value,item=null){playlistLoading=value;pendingPlaylist=value?item:null;$('playlistView').classList.toggle('is-loading',value);$('playlistView').setAttribute('aria-busy',String(value));renderPlaylistList();}
async function openPlaylist(item){
 const requestId=++playlistRequest;
 if(!item.playlist_id){openTool(item.manage_url,item.title);pendingPlaylist=item;renderPlaylistList();return;}
 toolOpen=false;
 $('playlistToolView').hidden=true;$('playlistView').hidden=false;notify('');setPlaylistLoading(true,item);
 try{
  const detail=await json('/api/playlists/'+encoded(item.kind)+'/'+encoded(item.key));if(requestId!==playlistRequest)return;
  current=item;tracks=Array.isArray(detail.tracks)?detail.tracks:[];filtered=tracks.slice();unavailablePlaylists.delete(item.kind+'\t'+item.key);
  $('playlistKind').textContent=item.kind_label;$('playlistTitle').textContent=detail.title;$('playlistSummary').textContent=tracks.length+' 首歌曲';$('playlistAddTrack').hidden=false;$('playlistManage').hidden=!item.manage_url;$('playlistManage').textContent=item.kind==='daily'?'更新与规则':'管理';$('playlistRemove').hidden=false;$('playlistPlayAll').disabled=!tracks.length;$('playlistSearch').value='';$('playlistEmpty').textContent='歌单里还没有歌曲';renderTracks();
 }finally{if(requestId===playlistRequest)setPlaylistLoading(false);}
}
function renderTracks(){
 const box=$('playlistTracks');box.replaceChildren();$('playlistEmpty').hidden=!!filtered.length;
 filtered.forEach((track,index)=>{
  const original=tracks.indexOf(track),isPlaying=track.id===playingTrackId&&playContext?.kind===current?.kind&&playContext?.key===current?.key,row=document.createElement('div');row.className='playlist-track'+(isPlaying?' playing':'');row.tabIndex=0;row.setAttribute('role','button');row.onclick=()=>playAt(original);row.onkeydown=event=>{if(event.target===row&&(event.key==='Enter'||event.key===' ')){event.preventDefault();playAt(original);}};
  const number=document.createElement('span');number.className='playlist-track-number';number.textContent=isPlaying&&!player.paused?'❚❚':String(original+1);
  const identity=document.createElement('span');identity.className='playlist-track-identity';const title=document.createElement('strong');title.textContent=track.title||'未知歌曲';const artist=document.createElement('small');artist.textContent=track.artist||'未知歌手';identity.append(title,artist);
  const album=document.createElement('span');album.className='playlist-track-album';album.textContent=track.album||'—';const duration=document.createElement('span');duration.className='playlist-track-duration';duration.textContent=formatTime(track.duration);const remove=document.createElement('button');remove.type='button';remove.className='playlist-track-remove';remove.textContent='移除';remove.setAttribute('aria-label','从歌单移除 '+(track.title||'歌曲'));remove.onclick=event=>{event.stopPropagation();action(()=>removeTrack(track));};row.append(number,identity,album,duration,remove);box.append(row);
 });
}
function updateArtwork(track,context=playContext){
 playerArtwork.replaceChildren();playerArtwork.classList.toggle('has-image',!!track?.thumb);
 if(track?.thumb){const image=document.createElement('img');image.alt='';image.src=mediaUrl('artwork',track,context);image.onerror=()=>{playerArtwork.replaceChildren(document.createTextNode('♫'));playerArtwork.classList.remove('has-image');};playerArtwork.append(image);}else playerArtwork.textContent='♫';
}
function playAt(index,autoplay=true){
 if(!current||index<0||index>=tracks.length)return;
 const track=tracks[index],sameContext=playContext?.kind===current.kind&&playContext?.key===current.key;
 if(sameContext&&track.id===playingTrackId&&player.src){if(player.paused&&autoplay)attemptPlay();else if(!player.paused)player.pause();return;}
 playQueue=tracks.slice();playContext={kind:current.kind,key:current.key,profileId:loadedProfileId};startQueueTrack(index,autoplay);
}
function startQueueTrack(index,autoplay=true){const track=playQueue[index];if(!track||!playContext)return;clearPlayerError();queueIndex=index;playingTrackId=track.id;player.src=mediaUrl('audio',track,playContext);$('playerTitle').textContent=track.title||'未知歌曲';$('playerArtist').textContent=track.artist||'未知歌手';$('playerQueue').textContent=(index+1)+' / '+playQueue.length;updateArtwork(track,playContext);$('playlistPlayer').hidden=false;renderTracks();if(autoplay)attemptPlay();}
function playNext(){if(queueIndex+1<playQueue.length)startQueueTrack(queueIndex+1);else{player.pause();player.currentTime=0;}}
function playPrevious(){if(player.currentTime>5){player.currentTime=0;return;}if(queueIndex>0)startQueueTrack(queueIndex-1);}
function stopPlayback(){player.pause();player.removeAttribute('src');player.load();clearPlayerError();playQueue=[];playContext=null;queueIndex=-1;playingTrackId='';$('playlistPlayer').hidden=true;}
function playingFrom(item){return !!item&&playContext?.kind===item.kind&&playContext?.key===item.key&&playContext?.profileId===loadedProfileId;}
function openTool(url,title='创建与整理'){
 ++playlistRequest;toolOpen=true;pendingPlaylist=null;setPlaylistLoading(false);const target=new URL(url,location.origin),frame=$('playlistToolFrame'),autoHeight=target.pathname==='/external';target.searchParams.set('embedded','1');$('playlistView').hidden=true;$('playlistToolView').hidden=false;$('playlistToolTitle').textContent=title;frame.classList.toggle('is-auto-height',autoHeight);frame.style.height='';frame.setAttribute('scrolling',autoHeight?'no':'auto');frame.src=target.pathname+target.search;document.querySelectorAll('#playlistTools button').forEach(button=>button.classList.toggle('active',button.dataset.toolUrl===target.pathname));
}
function fillSearchTargets(){const select=$('librarySearchTarget');select.replaceChildren();for(const item of playlists.filter(row=>row.playlist_id)){const option=document.createElement('option');option.value=item.kind+'\t'+item.key;option.textContent=item.title;select.append(option);}if(current)select.value=current.kind+'\t'+current.key;}
async function searchLibrary(){const query=$('librarySearchInput').value.trim();if(!query)throw Error('请输入歌名或歌手');const result=await json('/api/playlists/search?q='+encoded(query));const box=$('librarySearchResults');box.replaceChildren();for(const track of result.items||[]){const row=document.createElement('div');row.className='playlist-search-result';const identity=document.createElement('span'),title=document.createElement('strong'),artist=document.createElement('small');title.textContent=track.title||'未知歌曲';artist.textContent=[track.artist,track.album].filter(Boolean).join(' · ')||'未知歌手';identity.append(title,artist);const add=document.createElement('button');add.type='button';add.className='primary';add.textContent='添加';add.onclick=()=>action(()=>addTrack(track,add));row.append(identity,add);box.append(row);}if(!box.children.length){const empty=document.createElement('div');empty.className='playlist-empty';empty.textContent='没有找到歌曲';box.append(empty);}}
async function addTrack(track,button){const [kind,key]=$('librarySearchTarget').value.split('\t');button.disabled=true;try{await json('/api/playlists/tracks/edit','POST',{kind,key,track_id:String(track.id),operation:'add',confirm:true});notify('已加入歌单');if(current?.kind===kind&&current?.key===key)await openPlaylist(current,false);}finally{button.disabled=false;}}
async function removeTrack(track){if(!current)return;if(!await PCHUI.confirm('从“'+current.title+'”移除“'+track.title+'”？',{confirmText:'移除歌曲'}))return;const selected={...current};if(playingFrom(selected)&&track.id===playingTrackId)stopPlayback();await json('/api/playlists/tracks/edit','POST',{kind:selected.kind,key:selected.key,track_id:String(track.id),operation:'remove',confirm:true});notify('已从歌单移除');await openPlaylist(selected,false);}

function resetPlaylistView(){const frame=$('playlistToolFrame');frame.src='about:blank';$('playlistToolView').hidden=true;$('playlistView').hidden=false;$('playlistKind').textContent='我的歌单';$('playlistTitle').textContent='正在载入歌单';$('playlistSummary').textContent='';$('playlistAddTrack').hidden=true;$('playlistManage').hidden=true;$('playlistRemove').hidden=true;$('playlistPlayAll').disabled=true;$('playlistSearch').value='';$('playlistEmpty').textContent='正在读取这个账户的歌单…';}
async function switchProfile(profileId,persist=true){
 profileId=String(profileId||'');if(!profileId||profileId===loadedProfileId)return;
 const requestId=++profileRequest;++playlistRequest;loadedProfileId=profileId;$('playlistProfile').value=profileId;stopPlayback();current=null;tracks=[];filtered=[];playlists=[];unavailablePlaylists=new Set();toolOpen=false;setPlaylistLoading(false);resetPlaylistView();renderTracks();
 if(persist&&PCHAuth.profile()!==profileId)PCHAuth.setProfile(profileId);
 await loadPlaylists(undefined,requestId);
}
$('playlistProfile').onchange=()=>action(()=>switchProfile($('playlistProfile').value,true));
$('playlistSearch').oninput=()=>{const query=$('playlistSearch').value.trim().toLocaleLowerCase();filtered=query?tracks.filter(track=>[track.title,track.artist,track.album].join(' ').toLocaleLowerCase().includes(query)):tracks.slice();renderTracks();};
$('playlistPlayAll').onclick=()=>playAt(0);
$('playlistAddTrack').onclick=()=>{fillSearchTargets();$('librarySearchInput').value='';$('librarySearchResults').replaceChildren();$('librarySearchDialog').showModal();setTimeout(()=>$('librarySearchInput').focus(),0);};
$('librarySearchButton').onclick=()=>action(searchLibrary);$('librarySearchInput').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();action(searchLibrary);}};
$('playlistManage').onclick=()=>{if(current?.manage_url)openTool(current.manage_url,'管理“'+current.title+'”');};
$('playlistRemove').onclick=()=>action(async()=>{if(!current)return;if(!await PCHUI.confirm('确认删除 Plex 歌单“'+current.title+'”？',{confirmText:'删除歌单'}))return;const selected={kind:current.kind,key:current.key};if(playingFrom(selected))stopPlayback();const result=await json('/api/playlists/remove','POST',{kind:selected.kind,key:selected.key,title:current.title,confirm:true});notify(result.message);await loadPlaylists();});
$('playlistToolBack').onclick=()=>{const selected=current;toolOpen=false;pendingPlaylist=null;$('playlistToolFrame').src='about:blank';document.querySelectorAll('#playlistTools button').forEach(button=>button.classList.remove('active'));if(selected)action(()=>openPlaylist(selected,false));else{$('playlistToolView').hidden=true;$('playlistView').hidden=false;renderPlaylistList();}};
document.querySelectorAll('#playlistTools button').forEach(button=>button.onclick=()=>openTool(button.dataset.toolUrl,button.textContent.trim()));
$('playerToggle').onclick=()=>{if(!player.src&&tracks.length){playAt(Math.max(0,queueIndex));return;}if(player.paused)attemptPlay();else player.pause();};
$('playerPrevious').onclick=playPrevious;$('playerNext').onclick=playNext;
$('playerRetry').onclick=()=>{clearPlayerError();player.load();attemptPlay();};$('playerErrorNext').onclick=()=>{clearPlayerError();playNext();};
$('playerSeek').oninput=()=>{if(Number.isFinite(player.duration)&&player.duration>0)player.currentTime=player.duration*Number($('playerSeek').value)/1000;};
$('playerVolume').oninput=()=>{player.volume=Number($('playerVolume').value);player.muted=false;$('playerMute').textContent=player.volume?'音量':'静音';};$('playerMute').onclick=()=>{player.muted=!player.muted;$('playerMute').textContent=player.muted?'取消静音':'音量';};
player.addEventListener('ended',playNext);
player.addEventListener('timeupdate',()=>{const duration=Number.isFinite(player.duration)?player.duration:0;$('playerCurrent').textContent=formatTime(player.currentTime);$('playerDuration').textContent=formatTime(duration);$('playerSeek').value=duration?String(Math.round(player.currentTime/duration*1000)):'0';});
player.addEventListener('playing',()=>{clearPlayerError();$('playerToggle').textContent='❚❚';renderTracks();});player.addEventListener('pause',()=>{$('playerToggle').textContent='▶';renderTracks();});player.addEventListener('error',()=>showPlayerError());
window.addEventListener('pch-profile-change',event=>{const profileId=String(event.detail?.profile_id||'');if(profileId&&profileId!==loadedProfileId)action(()=>switchProfile(profileId,false));});
window.addEventListener('message',event=>{if(event.origin!==location.origin||event.source!==$('playlistToolFrame').contentWindow||event.data?.type!=='pch-tool-height')return;const height=Math.max(620,Math.min(12000,Math.ceil(Number(event.data.height)||0)));$('playlistToolFrame').style.height=height+'px';});
async function boot(){const profileId=await loadProfiles();await switchProfile(profileId,false);}
window.addEventListener('pch-auth-ready',event=>{if(event.detail?.authenticated)action(boot);});window.addEventListener('pch-auth-login',()=>action(boot));
})();
