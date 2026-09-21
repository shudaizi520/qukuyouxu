import {createPlaylistWorkspace} from './playlist-workspace.js';
import {createLibrarySearch} from './playlist-search.js';
import {createPlaylistPlayer} from './playlist-player.js';
import {createPlaylistSections} from './playlist-sections.js';

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
const WEB_PLAYER_KEY='pch-web-player-id';
function newWebPlayerId(){
 if(globalThis.crypto&&typeof crypto.randomUUID==='function')return crypto.randomUUID();
 return Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,14);
}
let webPlayerId=sessionStorage.getItem(WEB_PLAYER_KEY)||'';
if(!webPlayerId){webPlayerId=newWebPlayerId();sessionStorage.setItem(WEB_PLAYER_KEY,webPlayerId);}

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
const playlistSections=createPlaylistSections({
 document,
 onOpenPlaylist:item=>{setSidebarOpen(false);return navigate(()=>openPlaylist(item));},
 onOpenTool:(url,title,navigation)=>openWorkspacePage(url,title,'tool',navigation),
});
function reportWebPlayback(event,payload,{keepalive=false}={}){
 const profileId=String(payload.profileId||loadedProfileId||PCHAuth.profile()||'');if(!profileId)return Promise.resolve();
 const body={...payload,event,player_id:webPlayerId,event_id:newWebPlayerId()};delete body.profileId;
 return fetch('/api/playback/events',{
  method:'POST',credentials:'same-origin',cache:'no-store',keepalive,
  headers:{'Content-Type':'application/json','X-Plex-Profile':profileId},body:JSON.stringify(body),
 }).then(()=>{}).catch(()=>{});
}
function encoded(value){return encodeURIComponent(String(value||''));}
function formatTime(value){const seconds=Math.max(0,Math.floor(Number(value)||0));return Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0');}
function profileLabel(row){
 const account=row.account?.username||row.name||'Plex 用户';
 const library=row.library?.name||'音乐';
 return account+' · '+library;
}
function mediaUrl(type,track,context=current){
 const profile=context?.profileId||loadedProfileId||PCHAuth.profile();
 const params=new URLSearchParams();if(profile)params.set('profile_id',profile);
 const query=params.size?'?'+params.toString():'';
 return '/api/playlists/library/tracks/'+encoded(track.id)+'/'+type+query;
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

const playlistPlayer=createPlaylistPlayer({document,mediaUrl,formatTime,onStateChange:renderTracks,reportPlayback:reportWebPlayback});
function playingFrom(item){return playlistPlayer.isContext(playlistContext(item));}
function renderPlaylistList(){
 playlistSections.setItems(playlists);
 workspace.renderNavigation();
}

function setPlaylistLoading(value){
 playlistLoading=!!value;
 $('playlistView').classList.toggle('is-loading',value);$('playlistView').setAttribute('aria-busy',String(value));
 $('playlistTracks').inert=playlistLoading;
 $('playlistManage').disabled=playlistLoading;$('playlistRemove').disabled=playlistLoading;
 $('playlistPlayAll').disabled=playlistLoading||!tracks.length;
}
function restoreCurrentPlaylistSelection(){
 if(current){workspace.show({type:'playlist',kind:current.kind,key:current.key,panel:'playlist'});return;}
 workspace.show({type:'playlist',kind:'',key:'',panel:'playlist'});
}
async function openPlaylist(item){
 const requestId=++playlistRequest;
 librarySearch.reset();
 if(!item.can_play){
  setPlaylistLoading(false);
  if(item.manage_url){workspace.openPage(item.manage_url,item.title,{type:'tool',navigation:{type:'section',section:item.section}});return;}
  throw Error('这个歌单还不能播放');
 }
 notify('');setPlaylistLoading(true);workspace.show({type:'playlist',kind:item.kind,key:item.key,panel:'playlist'});
 try{
  const detail=await json('/api/playlists/'+encoded(item.kind)+'/'+encoded(item.key));if(requestId!==playlistRequest)return false;
  current=item;tracks=Array.isArray(detail.tracks)?detail.tracks:[];filtered=tracks.slice();unavailablePlaylists.delete(item.kind+'\t'+item.key);
  $('playlistKind').textContent=item.kind_label;$('playlistTitle').textContent=detail.title;$('playlistSummary').textContent=tracks.length+' 首歌曲';
  $('playlistManage').hidden=!item.manage_url;$('playlistManage').textContent=item.kind==='daily'?'更新与规则':'管理';
  $('playlistRemove').hidden=!item.can_delete;$('playlistPlayAll').disabled=!tracks.length;$('playlistEmpty').textContent='歌单里还没有歌曲';renderTracks();
 return true;
 }catch(error){
  if(requestId!==playlistRequest)return false;
  restoreCurrentPlaylistSelection();
  throw error;
 }finally{if(requestId===playlistRequest)setPlaylistLoading(false);}
}
function openSection(section='smart'){
 librarySearch.reset();setSidebarOpen(false);++playlistRequest;setPlaylistLoading(false);current=null;
 playlistSections.renderSection(section);workspace.show({type:'section',section,panel:'section'});
}
async function loadPlaylists(preferred,requestId=profileRequest){
 const data=await json('/api/playlists');if(requestId!==profileRequest)return;
 playlists=Array.isArray(data.items)?data.items:[];
 unavailablePlaylists=new Set();renderPlaylistList();
 const selected=preferred?.kind&&playlists.find(row=>row.kind===preferred.kind&&row.key===preferred.key);
 if(selected&&selected.can_play){await openPlaylist(selected);return;}
 openSection(preferred?.section||'smart');
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
 $('playlistManage').hidden=true;$('playlistRemove').hidden=true;$('playlistPlayAll').disabled=true;$('playlistEmpty').textContent='正在读取这个账户的歌单…';
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
 const view=workspace.current();openSection(view.type==='section'?view.section:'smart');
}
async function returnFromWorkspace(){
 const view=workspace.current();
 const preferred=view.type==='playlist'&&view.kind?{kind:view.kind,key:view.key}:view.type==='section'?{section:view.section}:current?{kind:current.kind,key:current.key}:null;
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
 $('workspaceHome').onclick=()=>openSection('smart');$('playlistPlayAll').onclick=()=>playlistPlayer.playAt(tracks,0,playlistContext());
 $('playlistManage').onclick=()=>{if(current?.manage_url)openWorkspacePage(current.manage_url,'管理“'+current.title+'”','tool',{type:'playlist',kind:current.kind,key:current.key});};
 $('playlistRemove').onclick=()=>action(async()=>{
  if(!current||!await PCHUI.confirm('确认删除 Plex 歌单“'+current.title+'”？',{confirmText:'删除歌单'}))return;
  const selected={kind:current.kind,key:current.key};if(playingFrom(selected))playlistPlayer.stop();
  const result=await json('/api/playlists/remove','POST',{kind:selected.kind,key:selected.key,title:current.title,confirm:true});notify(result.message);current=null;await loadPlaylists();
 });
 $('playlistToolBack').onclick=()=>action(returnFromWorkspace);
 $('smartHubButton').onclick=()=>openSection('smart');$('libraryHubButton').onclick=()=>openSection('library');
 $('customPlaylistRefresh').onclick=()=>action(()=>loadPlaylists(workspace.current(),profileRequest));
 document.querySelectorAll('[data-tool-url]').forEach(button=>button.onclick=()=>openTool(button.dataset.toolUrl,button.title||button.textContent.trim()));
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
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)return;const event=playlistPlayer.paused()?'pause':'progress';playlistPlayer.flush(event,true);});
 window.addEventListener('pagehide',()=>playlistPlayer.flush('stop',true));
}
async function boot(){const requestId=profileRequest,profileId=await loadProfiles();if(requestId!==profileRequest)return;await switchProfile(profileId,false);}

mount();
PCHAuth.ready.then(state=>{if(state.authenticated)action(boot);});
window.addEventListener('pch-auth-login',()=>action(boot));
