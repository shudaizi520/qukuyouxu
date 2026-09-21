import {createPlaylistWorkspace} from './playlist-workspace.js';
import {createLibrarySearch} from './playlist-search.js';
import {createPlaylistPlayer} from './playlist-player.js';

const $=id=>document.getElementById(id);
const workspace=createPlaylistWorkspace({document});
const compactSidebar=window.matchMedia('(max-width:620px)');

let profiles=[];
let playlists=[];
let current=null;
let tracks=[];
let filtered=[];
let unavailablePlaylists=new Set();
let loadedProfileId='';
let playlistRequest=0;
let profileRequest=0;
let playlistLoading=false;
let noticeTimer=0;

function notify(message,error=false,timeout=0){
 clearTimeout(noticeTimer);
 const node=$('playlistNotice');
 node.hidden=!message;node.textContent=message||'';node.className='notice'+(error?' error':'');
 if(message&&timeout>0)noticeTimer=setTimeout(()=>{if(node.textContent===message){node.hidden=true;node.textContent='';}},timeout);
}
async function action(fn){
 try{return await (window.PCHUI?PCHUI.run(fn):fn());}
 catch(error){notify(error.message||'操作失败',true);}
}
async function navigate(fn){
 try{return await fn();}
 catch(error){notify(error.message||'操作失败',true);}
}
async function json(path,method='GET',body){return (await PCHAuth.request(path,method,body)).json();}
function encoded(value){return encodeURIComponent(String(value||''));}
function formatTime(value){const seconds=Math.max(0,Math.floor(Number(value)||0));return Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0');}
function profileLabel(row){
 const account=row.account?.username||row.name||'Plex 用户';
 const library=row.library?.name||'音乐';
 return account+' · '+library;
}
function mediaUrl(type,track,context=current,options={}){
 const profile=context?.profileId||loadedProfileId||PCHAuth.profile();
 const params=new URLSearchParams();if(profile)params.set('profile_id',profile);
 const offset=Number(options.offsetSeconds);if(type==='audio'&&Number.isFinite(offset)&&offset>0)params.set('offset',String(offset));
 const query=params.size?'?'+params.toString():'';
 if(context?.kind==='library')return '/api/playlists/library/tracks/'+encoded(track.id)+'/'+type+query;
 return '/api/playlists/'+encoded(context?.kind)+'/'+encoded(context?.key)+'/tracks/'+encoded(track.id)+'/'+type+query;
}
function playlistContext(item=current){return item?{kind:item.kind,key:item.key,profileId:loadedProfileId}:null;}

function renderTracks(){
 const box=$('playlistTracks');box.replaceChildren();$('playlistEmpty').hidden=!!filtered.length;
 filtered.forEach(track=>{
  const original=tracks.indexOf(track);
  const isPlaying=playlistPlayer.isPlayingTrack(track,playlistContext());
  const row=document.createElement('div');row.className='playlist-track'+(isPlaying?' playing':'');row.tabIndex=0;row.setAttribute('role','button');
  row.onclick=()=>playlistPlayer.playAt(tracks,original,playlistContext());row.onkeydown=event=>{if(event.target===row&&(event.key==='Enter'||event.key===' ')){event.preventDefault();playlistPlayer.playAt(tracks,original,playlistContext());}};
  const number=document.createElement('span');number.className='playlist-track-number';number.textContent=isPlaying&&!playlistPlayer.paused()?'❚❚':String(original+1);
  const identity=document.createElement('span');identity.className='playlist-track-identity';
  const title=document.createElement('strong');title.textContent=track.title||'未知歌曲';identity.append(title);
  const artist=document.createElement('span');artist.className='playlist-track-artist';artist.textContent=track.artist||'未知歌手';
  const album=document.createElement('span');album.className='playlist-track-album';album.textContent=track.album||'—';
  const duration=document.createElement('span');duration.className='playlist-track-duration';duration.textContent=formatTime(track.duration);
  const remove=document.createElement('button');remove.type='button';remove.className='playlist-track-remove';remove.textContent='移除';remove.setAttribute('aria-label','从歌单移除 '+(track.title||'歌曲'));
  remove.onclick=event=>{event.stopPropagation();action(()=>removeTrack(track));};
  row.append(number,identity,artist,album,duration,remove);box.append(row);
 });
}

const playlistPlayer=createPlaylistPlayer({document,mediaUrl,formatTime,onStateChange:renderTracks});
function playingFrom(item){return playlistPlayer.isContext(playlistContext(item));}
function renderPlaylistList(){
 const box=$('playlistList');box.replaceChildren();$('playlistCount').textContent=String(playlists.length);
 for(const item of playlists){
  const button=document.createElement('button');button.type='button';button.dataset.kind=item.kind;button.dataset.key=item.key;
  if(unavailablePlaylists.has(item.kind+'\t'+item.key))button.classList.add('unavailable');
  const icon=document.createElement('span');icon.className='playlist-side-icon';icon.textContent=item.kind==='daily'?'日':item.kind==='smart'?'智':item.kind==='external'?'外':'类';
  const text=document.createElement('span'),title=document.createElement('strong'),count=document.createElement('small');
  title.textContent=item.title;count.textContent=item.playlist_id?(item.count??'—')+' 首':'尚未创建';text.append(title,count);button.append(icon,text);
  button.onclick=()=>{setSidebarOpen(false);navigate(()=>openPlaylist(item));};box.append(button);
 }
 if(!playlists.length){const empty=document.createElement('span');empty.className='playlist-side-empty';empty.textContent='还没有歌单';box.append(empty);}
 workspace.renderNavigation();
}

function setPlaylistLoading(value){
 playlistLoading=!!value;
 $('playlistView').classList.toggle('is-loading',value);$('playlistView').setAttribute('aria-busy',String(value));
 $('playlistTracks').inert=playlistLoading;
 $('playlistAddTrack').disabled=playlistLoading;$('playlistManage').disabled=playlistLoading;$('playlistRemove').disabled=playlistLoading;
 $('playlistPlayAll').disabled=playlistLoading||!tracks.length;
}
function restoreCurrentPlaylistSelection(){
 if(current){workspace.show({type:'playlist',kind:current.kind,key:current.key,panel:'playlist'});return;}
 workspace.show({type:'playlist',kind:'',key:'',panel:'playlist'});
}
async function openPlaylist(item){
 const requestId=++playlistRequest;
 librarySearch.reset();
 if(!item.playlist_id){
  setPlaylistLoading(false);workspace.openPage(item.manage_url,item.title,{type:'tool',navigation:{type:'playlist',kind:item.kind,key:item.key}});return;
 }
 notify('');setPlaylistLoading(true);workspace.show({type:'playlist',kind:item.kind,key:item.key,panel:'playlist'});
 try{
  const detail=await json('/api/playlists/'+encoded(item.kind)+'/'+encoded(item.key));if(requestId!==playlistRequest)return false;
  current=item;tracks=Array.isArray(detail.tracks)?detail.tracks:[];filtered=tracks.slice();unavailablePlaylists.delete(item.kind+'\t'+item.key);
  $('playlistKind').textContent=item.kind_label;$('playlistTitle').textContent=detail.title;$('playlistSummary').textContent=tracks.length+' 首歌曲';
  $('playlistAddTrack').hidden=false;$('playlistManage').hidden=!item.manage_url;$('playlistManage').textContent=item.kind==='daily'?'更新与规则':'管理';
  $('playlistRemove').hidden=false;$('playlistPlayAll').disabled=!tracks.length;$('playlistEmpty').textContent='歌单里还没有歌曲';renderTracks();
 return true;
 }catch(error){
  if(requestId!==playlistRequest)return false;
  restoreCurrentPlaylistSelection();
  throw error;
 }finally{if(requestId===playlistRequest)setPlaylistLoading(false);}
}
async function openFirstAvailable(candidates,index=0,lastError=null,requestId=profileRequest){
 if(requestId!==profileRequest)return lastError;
 if(index>=candidates.length){current=null;return lastError;}
 try{const opened=await openPlaylist(candidates[index]);if(!opened)return lastError;return lastError;}
 catch(error){
  if(requestId!==profileRequest)return lastError;
  unavailablePlaylists.add(candidates[index].kind+'\t'+candidates[index].key);renderPlaylistList();
  return openFirstAvailable(candidates,index+1,error,requestId);
 }
}
async function loadPlaylists(preferred,requestId=profileRequest){
 const data=await json('/api/playlists');if(requestId!==profileRequest)return;
 playlists=Array.isArray(data.items)?data.items:[];
 const selected=preferred&&playlists.find(row=>row.kind===preferred.kind&&row.key===preferred.key);
 const ordered=selected?[selected,...playlists.filter(row=>row!==selected)]:playlists.slice();
 const playable=ordered.filter(row=>row.playlist_id);unavailablePlaylists=new Set();renderPlaylistList();
 if(!playable.length){if(ordered[0])await openPlaylist(ordered[0]);return;}
 const lastError=await openFirstAvailable(playable,0,null,requestId);if(requestId!==profileRequest)return;
 if(current){if(lastError)notify('一个旧歌单暂时不可用，已打开其他歌单。',true);return;}
 tracks=[];filtered=[];$('playlistEmpty').textContent='没有可显示的歌单';renderPlaylistList();renderTracks();if(lastError)throw lastError;
}
async function loadProfiles(){
 const data=await json('/api/plex/profiles');profiles=Array.isArray(data.items)?data.items:[];
 const selected=PCHAuth.profile()||String(data.active_profile_id||profiles[0]?.id||'');
 const select=$('playlistProfile');select.replaceChildren();
 for(const row of profiles){const option=document.createElement('option');option.value=row.id;option.textContent=profileLabel(row);select.append(option);}
 if(profiles.some(row=>row.id===selected))select.value=selected;
 return select.value;
}
function resetPlaylistView(){
 workspace.reset();current=null;$('playlistKind').textContent='我的歌单';$('playlistTitle').textContent='正在载入歌单';$('playlistSummary').textContent='';
 $('playlistAddTrack').hidden=true;$('playlistManage').hidden=true;$('playlistRemove').hidden=true;$('playlistPlayAll').disabled=true;$('playlistEmpty').textContent='正在读取这个账户的歌单…';
}
function resetSession(){
 ++profileRequest;++playlistRequest;loadedProfileId='';profiles=[];playlists=[];current=null;tracks=[];filtered=[];unavailablePlaylists=new Set();
 playlistPlayer.stop();librarySearch.reset();setPlaylistLoading(false);resetPlaylistView();renderTracks();renderPlaylistList();setSidebarOpen(false);
}
async function switchProfile(profileId,persist=true){
 profileId=String(profileId||'');if(!profileId||profileId===loadedProfileId)return;
 const requestId=++profileRequest;++playlistRequest;loadedProfileId=profileId;$('playlistProfile').value=profileId;
 playlistPlayer.stop();librarySearch.reset();tracks=[];filtered=[];playlists=[];unavailablePlaylists=new Set();setPlaylistLoading(false);resetPlaylistView();renderTracks();renderPlaylistList();
 if(persist&&PCHAuth.profile()!==profileId)PCHAuth.setProfile(profileId);
 await loadPlaylists(undefined,requestId);
}
async function removeTrack(track){
 if(!current)return;
 if(!await PCHUI.confirm('从“'+current.title+'”移除“'+track.title+'”？',{confirmText:'移除歌曲'}))return;
 const selected={...current};if(playingFrom(selected)&&String(track.id)===playlistPlayer.trackId())playlistPlayer.stop();
 const result=await json('/api/playlists/tracks/edit','POST',{kind:selected.kind,key:selected.key,track_id:String(track.id),operation:'remove',confirm:true});
 syncPlaylistCount(selected.kind,selected.key,result.count);
 notify('已从歌单移除');await openPlaylist(selected);
}
function syncPlaylistCount(kind,key,count){
 const item=playlists.find(row=>row.kind===kind&&row.key===key);
 if(item&&Number.isFinite(Number(count))){item.count=Math.max(0,Number(count));renderPlaylistList();}
}
function restorePlaylistView(){
 librarySearch.reset();setSidebarOpen(false);++playlistRequest;setPlaylistLoading(false);
 if(current){workspace.show({type:'playlist',kind:current.kind,key:current.key,panel:'playlist'});return;}
 const first=playlists.find(row=>row.playlist_id)||playlists[0];if(first)action(()=>openPlaylist(first));
}
async function returnFromWorkspace(){
 const view=workspace.current();
 const preferred=view.type==='playlist'&&view.kind?{kind:view.kind,key:view.key}:current?{kind:current.kind,key:current.key}:null;
 const requestId=profileRequest;
 librarySearch.reset();setSidebarOpen(false);++playlistRequest;setPlaylistLoading(false);
 await loadPlaylists(preferred,requestId);
}
function openWorkspacePage(url,title,type='tool',navigation=null){
 librarySearch.reset();setSidebarOpen(false);++playlistRequest;setPlaylistLoading(false);workspace.openPage(url,title,{type,navigation});
}
function openTool(url,title='创建与整理'){openWorkspacePage(url,title,'tool');}

function setSidebarOpen(value){
 const compact=compactSidebar.matches,open=compact&&!!value;
 const sidebar=$('playlistSidebar'),backdrop=$('playlistSidebarBackdrop');
 document.body.classList.toggle('playlist-sidebar-open',open);
 sidebar.inert=compact&&!open;backdrop.hidden=!open;
 $('playlistSidebarToggle').setAttribute('aria-expanded',String(open));
 $('playlistSidebarToggle').setAttribute('aria-label',open?'关闭歌单导航':'打开歌单导航');
}
function openSearchWorkspace(query){
 notify('');setSidebarOpen(false);++playlistRequest;setPlaylistLoading(false);
 workspace.show({type:'search',query,panel:'search'});
}

const librarySearch=createLibrarySearch({
 document,requestJson:json,getProfileId:()=>loadedProfileId,getPlaylists:()=>playlists,getCurrentPlaylist:()=>current,
 onPlayQueue:playlistPlayer.startQueue,onSearchStart:openSearchWorkspace,onBack:restorePlaylistView,notify,
 onPlaylistChanged:async(kind,key,count)=>{syncPlaylistCount(kind,key,count);if(current?.kind===kind&&current?.key===key)await openPlaylist(current);},
});

function mount(){
 librarySearch.mount();playlistPlayer.mount();
 $('playlistSidebarToggle').onclick=()=>setSidebarOpen(!document.body.classList.contains('playlist-sidebar-open'));
 $('playlistSidebarBackdrop').onclick=()=>setSidebarOpen(false);
 compactSidebar.addEventListener('change',()=>setSidebarOpen(false));setSidebarOpen(false);
 $('playlistProfile').onchange=()=>action(()=>switchProfile($('playlistProfile').value,true));
 $('workspaceHome').onclick=restorePlaylistView;$('playlistPlayAll').onclick=()=>playlistPlayer.playAt(tracks,0,playlistContext());$('playlistAddTrack').onclick=librarySearch.focus;
 $('playlistManage').onclick=()=>{if(current?.manage_url)openWorkspacePage(current.manage_url,'管理“'+current.title+'”','tool',{type:'playlist',kind:current.kind,key:current.key});};
 $('playlistRemove').onclick=()=>action(async()=>{
  if(!current||!await PCHUI.confirm('确认删除 Plex 歌单“'+current.title+'”？',{confirmText:'删除歌单'}))return;
  const selected={kind:current.kind,key:current.key};if(playingFrom(selected))playlistPlayer.stop();
  const result=await json('/api/playlists/remove','POST',{kind:selected.kind,key:selected.key,title:current.title,confirm:true});notify(result.message);current=null;await loadPlaylists();
 });
 $('playlistToolBack').onclick=()=>action(returnFromWorkspace);
 document.querySelectorAll('#playlistTools button').forEach(button=>button.onclick=()=>openTool(button.dataset.toolUrl,button.textContent.trim()));
 document.querySelectorAll('[data-workspace-url]').forEach(button=>button.onclick=()=>openWorkspacePage(button.dataset.workspaceUrl,button.textContent.trim(),'system'));
 window.addEventListener('pch-profile-change',event=>{const profileId=String(event.detail?.profile_id||'');if(profileId&&profileId!==loadedProfileId)action(()=>switchProfile(profileId,false));});
 window.addEventListener('pch-auth-logout',resetSession);
 window.addEventListener('message',event=>{
  if(event.origin!==location.origin||event.source!==$('playlistToolFrame').contentWindow)return;
  if(event.data?.type==='pch-auth-logout'){PCHAuth.expire();return;}
  if(event.data?.type!=='pch-player-preview')return;
  const track=event.data.track;if(!track||typeof track.source!=='string'||!track.source.startsWith('/api/external/'))return;
  playlistPlayer.playPreview(track);
 });
 window.addEventListener('keydown',event=>{if(event.key==='Escape')setSidebarOpen(false);});
}
async function boot(){const requestId=profileRequest,profileId=await loadProfiles();if(requestId!==profileRequest)return;await switchProfile(profileId,false);}

mount();
PCHAuth.ready.then(state=>{if(state.authenticated)action(boot);});
window.addEventListener('pch-auth-login',()=>action(boot));
