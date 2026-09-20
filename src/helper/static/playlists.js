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
let queueIndex=-1;
let playingTrackId='';
let unavailablePlaylists=new Set();

function notify(message,error=false){const node=$('playlistNotice');node.hidden=!message;node.textContent=message||'';node.className='notice'+(error?' error':'');}
async function json(path,method='GET',body){return (await PCHAuth.request(path,method,body)).json();}
async function action(fn){try{return await (window.PCHUI?PCHUI.run(fn):fn());}catch(error){notify(error.message||'操作失败',true);}}
function profileLabel(row){const account=row.account?.username||row.name||'Plex 用户',library=row.library?.name||'音乐';return account+' · '+library;}
function formatTime(value){const seconds=Math.max(0,Math.floor(Number(value)||0));return Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0');}
function encoded(value){return encodeURIComponent(String(value||''));}
function mediaUrl(type,track){const profile=PCHAuth.profile(),query=profile?'?profile_id='+encoded(profile):'';return '/api/playlists/'+encoded(current.kind)+'/'+encoded(current.key)+'/tracks/'+encoded(track.id)+'/'+type+query;}

async function loadProfiles(){
 const data=await json('/api/plex/profiles');profiles=Array.isArray(data.items)?data.items:[];
 const selected=PCHAuth.profile()||String(data.active_profile_id||profiles[0]?.id||'');
 const select=$('playlistProfile');select.replaceChildren();
 for(const row of profiles){const option=document.createElement('option');option.value=row.id;option.textContent=profileLabel(row);select.append(option);}
 if(profiles.some(row=>row.id===selected))select.value=selected;
}
function renderPlaylistList(){
 const box=$('playlistList');box.replaceChildren();$('playlistCount').textContent=String(playlists.length);
 for(const item of playlists){
  const button=document.createElement('button');button.type='button';button.className=item.kind===current?.kind&&item.key===current?.key?'active':'';if(unavailablePlaylists.has(item.kind+'\t'+item.key))button.classList.add('unavailable');
  const icon=document.createElement('span');icon.className='playlist-side-icon';icon.textContent=item.kind==='daily'?'日':item.kind==='smart'?'智':item.kind==='external'?'外':'类';
  const text=document.createElement('span'),title=document.createElement('strong'),count=document.createElement('small');title.textContent=item.title;count.textContent=(item.count??'—')+' 首';text.append(title,count);button.append(icon,text);button.onclick=()=>action(()=>openPlaylist(item));box.append(button);
 }
 if(!playlists.length){const empty=document.createElement('span');empty.className='playlist-side-empty';empty.textContent='还没有歌单';box.append(empty);}
}
async function loadPlaylists(preferred){
 const data=await json('/api/playlists');playlists=Array.isArray(data.items)?data.items:[];
 const selected=preferred&&playlists.find(row=>row.kind===preferred.kind&&row.key===preferred.key);
 const candidates=selected?[selected,...playlists.filter(row=>row!==selected)]:playlists.slice();unavailablePlaylists=new Set();renderPlaylistList();
 let lastError=null,opened=false;
 for(const candidate of candidates){try{if(!opened){await openPlaylist(candidate,false);opened=true;}else await json('/api/playlists/'+encoded(candidate.kind)+'/'+encoded(candidate.key));}catch(error){lastError=error;unavailablePlaylists.add(candidate.kind+'\t'+candidate.key);renderPlaylistList();}}
 if(opened){if(lastError)notify('部分旧歌单需要核对，已先打开可用歌单。',true);return;}
 current=null;tracks=[];filtered=[];renderPlaylistList();renderTracks();if(lastError)throw lastError;
}
async function openPlaylist(item,stop=true){
 const keepPlayingTrack=!stop&&current?.kind===item.kind&&current?.key===item.key&&player.src?playingTrackId:'';
 if(stop)stopPlayback();const detail=await json('/api/playlists/'+encoded(item.kind)+'/'+encoded(item.key));current=item;tracks=Array.isArray(detail.tracks)?detail.tracks:[];filtered=tracks.slice();
 if(keepPlayingTrack){queueIndex=tracks.findIndex(track=>track.id===keepPlayingTrack);if(queueIndex>=0){playingTrackId=keepPlayingTrack;$('playerQueue').textContent=(queueIndex+1)+' / '+tracks.length;}else stopPlayback();}else{queueIndex=-1;playingTrackId='';}
 $('playlistToolView').hidden=true;$('playlistView').hidden=false;$('playlistKind').textContent=item.kind_label;$('playlistTitle').textContent=detail.title;$('playlistSummary').textContent=tracks.length+' 首歌曲';$('playlistAddTrack').hidden=false;$('playlistManage').hidden=!item.manage_url;$('playlistRemove').hidden=false;$('playlistPlayAll').disabled=!tracks.length;$('playlistSearch').value='';notify('');renderPlaylistList();renderTracks();
}
function renderTracks(){
 const box=$('playlistTracks');box.replaceChildren();$('playlistEmpty').hidden=!!filtered.length;
 filtered.forEach((track,index)=>{
  const original=tracks.indexOf(track),row=document.createElement('div');row.className='playlist-track'+(track.id===playingTrackId?' playing':'');row.tabIndex=0;row.setAttribute('role','button');row.onclick=()=>playAt(original);row.onkeydown=event=>{if(event.target===row&&(event.key==='Enter'||event.key===' ')){event.preventDefault();playAt(original);}};
  const number=document.createElement('span');number.className='playlist-track-number';number.textContent=track.id===playingTrackId&&!player.paused?'❚❚':String(original+1);
  const identity=document.createElement('span');identity.className='playlist-track-identity';const title=document.createElement('strong');title.textContent=track.title||'未知歌曲';const artist=document.createElement('small');artist.textContent=track.artist||'未知歌手';identity.append(title,artist);
  const album=document.createElement('span');album.className='playlist-track-album';album.textContent=track.album||'—';const duration=document.createElement('span');duration.className='playlist-track-duration';duration.textContent=formatTime(track.duration);const remove=document.createElement('button');remove.type='button';remove.className='playlist-track-remove';remove.textContent='移除';remove.setAttribute('aria-label','从歌单移除 '+(track.title||'歌曲'));remove.onclick=event=>{event.stopPropagation();action(()=>removeTrack(track));};row.append(number,identity,album,duration,remove);box.append(row);
 });
}
function updateArtwork(track){
 playerArtwork.replaceChildren();playerArtwork.classList.toggle('has-image',!!track?.thumb);
 if(track?.thumb){const image=document.createElement('img');image.alt='';image.src=mediaUrl('artwork',track);image.onerror=()=>{playerArtwork.replaceChildren(document.createTextNode('♫'));playerArtwork.classList.remove('has-image');};playerArtwork.append(image);}else playerArtwork.textContent='♫';
}
function playAt(index,autoplay=true){
 if(!current||index<0||index>=tracks.length)return;
 const track=tracks[index];
 if(track.id===playingTrackId&&player.src){if(player.paused&&autoplay)player.play().catch(()=>notify('浏览器暂时无法播放这个音频格式。',true));else if(!player.paused)player.pause();return;}
 queueIndex=index;playingTrackId=track.id;player.src=mediaUrl('audio',track);$('playerTitle').textContent=track.title||'未知歌曲';$('playerArtist').textContent=track.artist||'未知歌手';$('playerQueue').textContent=(index+1)+' / '+tracks.length;updateArtwork(track);$('playlistPlayer').hidden=false;renderTracks();if(autoplay)player.play().catch(()=>notify('浏览器暂时无法播放这个音频格式。',true));
}
function playNext(){if(queueIndex+1<tracks.length)playAt(queueIndex+1);else{player.pause();player.currentTime=0;}}
function playPrevious(){if(player.currentTime>5){player.currentTime=0;return;}if(queueIndex>0)playAt(queueIndex-1);}
function stopPlayback(){player.pause();player.removeAttribute('src');player.load();queueIndex=-1;playingTrackId='';$('playlistPlayer').hidden=true;}
function openTool(url,title='创建与整理'){
 const target=new URL(url,location.origin);target.searchParams.set('embedded','1');$('playlistView').hidden=true;$('playlistToolView').hidden=false;$('playlistToolTitle').textContent=title;$('playlistToolFrame').src=target.pathname+target.search;document.querySelectorAll('#playlistTools button').forEach(button=>button.classList.toggle('active',button.dataset.toolUrl===target.pathname));
}
function fillSearchTargets(){const select=$('librarySearchTarget');select.replaceChildren();for(const item of playlists){const option=document.createElement('option');option.value=item.kind+'\t'+item.key;option.textContent=item.title;select.append(option);}if(current)select.value=current.kind+'\t'+current.key;}
async function searchLibrary(){const query=$('librarySearchInput').value.trim();if(!query)throw Error('请输入歌名或歌手');const result=await json('/api/playlists/search?q='+encoded(query));const box=$('librarySearchResults');box.replaceChildren();for(const track of result.items||[]){const row=document.createElement('div');row.className='playlist-search-result';const identity=document.createElement('span'),title=document.createElement('strong'),artist=document.createElement('small');title.textContent=track.title||'未知歌曲';artist.textContent=[track.artist,track.album].filter(Boolean).join(' · ')||'未知歌手';identity.append(title,artist);const add=document.createElement('button');add.type='button';add.className='primary';add.textContent='添加';add.onclick=()=>action(()=>addTrack(track,add));row.append(identity,add);box.append(row);}if(!box.children.length){const empty=document.createElement('div');empty.className='playlist-empty';empty.textContent='没有找到歌曲';box.append(empty);}}
async function addTrack(track,button){const [kind,key]=$('librarySearchTarget').value.split('\t');button.disabled=true;try{await json('/api/playlists/tracks/edit','POST',{kind,key,track_id:String(track.id),operation:'add',confirm:true});notify('已加入歌单');if(current?.kind===kind&&current?.key===key)await openPlaylist(current,false);}finally{button.disabled=false;}}
async function removeTrack(track){if(!current)return;if(!await PCHUI.confirm('从“'+current.title+'”移除“'+track.title+'”？',{confirmText:'移除歌曲'}))return;const selected={...current};if(track.id===playingTrackId)stopPlayback();await json('/api/playlists/tracks/edit','POST',{kind:selected.kind,key:selected.key,track_id:String(track.id),operation:'remove',confirm:true});notify('已从歌单移除');await openPlaylist(selected,false);}

$('playlistProfile').onchange=()=>action(async()=>{stopPlayback();current=null;PCHAuth.setProfile($('playlistProfile').value);await loadPlaylists();});
$('playlistSearch').oninput=()=>{const query=$('playlistSearch').value.trim().toLocaleLowerCase();filtered=query?tracks.filter(track=>[track.title,track.artist,track.album].join(' ').toLocaleLowerCase().includes(query)):tracks.slice();renderTracks();};
$('playlistPlayAll').onclick=()=>playAt(0);
$('playlistAddTrack').onclick=()=>{fillSearchTargets();$('librarySearchInput').value='';$('librarySearchResults').replaceChildren();$('librarySearchDialog').showModal();setTimeout(()=>$('librarySearchInput').focus(),0);};
$('librarySearchButton').onclick=()=>action(searchLibrary);$('librarySearchInput').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();action(searchLibrary);}};
$('playlistManage').onclick=()=>{if(current?.manage_url)openTool(current.manage_url,'管理“'+current.title+'”');};
$('playlistRemove').onclick=()=>action(async()=>{if(!current)return;if(!await PCHUI.confirm('确认删除 Plex 歌单“'+current.title+'”？',{confirmText:'删除歌单'}))return;const selected={kind:current.kind,key:current.key};stopPlayback();const result=await json('/api/playlists/remove','POST',{kind:selected.kind,key:selected.key,title:current.title,confirm:true});notify(result.message);await loadPlaylists();});
$('playlistToolBack').onclick=()=>{const selected=current;$('playlistToolFrame').src='about:blank';document.querySelectorAll('#playlistTools button').forEach(button=>button.classList.remove('active'));if(selected)action(()=>openPlaylist(selected,false));else{$('playlistToolView').hidden=true;$('playlistView').hidden=false;}};
document.querySelectorAll('#playlistTools button').forEach(button=>button.onclick=()=>openTool(button.dataset.toolUrl,button.textContent.trim()));
$('playerToggle').onclick=()=>{if(!player.src&&tracks.length){playAt(Math.max(0,queueIndex));return;}if(player.paused)player.play().catch(()=>notify('浏览器暂时无法播放这个音频格式。',true));else player.pause();};
$('playerPrevious').onclick=playPrevious;$('playerNext').onclick=playNext;
$('playerSeek').oninput=()=>{if(Number.isFinite(player.duration)&&player.duration>0)player.currentTime=player.duration*Number($('playerSeek').value)/1000;};
player.addEventListener('ended',playNext);
player.addEventListener('timeupdate',()=>{const duration=Number.isFinite(player.duration)?player.duration:0;$('playerCurrent').textContent=formatTime(player.currentTime);$('playerDuration').textContent=formatTime(duration);$('playerSeek').value=duration?String(Math.round(player.currentTime/duration*1000)):'0';});
player.addEventListener('playing',()=>{$('playerToggle').textContent='❚❚';renderTracks();});player.addEventListener('pause',()=>{$('playerToggle').textContent='▶';renderTracks();});player.addEventListener('error',()=>notify('这首歌暂时无法播放，可以试试下一首。',true));
window.addEventListener('pch-profile-change',event=>{if(event.detail?.profile_id&&event.detail.profile_id!==$('playlistProfile').value){$('playlistProfile').value=event.detail.profile_id;action(async()=>{stopPlayback();await loadPlaylists();});}});
async function boot(){await loadProfiles();await loadPlaylists();}
window.addEventListener('pch-auth-ready',event=>{if(event.detail?.authenticated)action(boot);});window.addEventListener('pch-auth-login',()=>action(boot));
})();
