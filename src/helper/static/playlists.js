import {createPlaylistWorkspace} from './playlist-workspace.js';
import {createLibrarySearch} from './playlist-search.js';
import {createPlaylistPlayer} from './playlist-player.js';
import {createPlaylistSections} from './playlist-sections.js';

const $=id=>document.getElementById(id);
const workspace=createPlaylistWorkspace({document});
const compactSidebar=window.matchMedia('(max-width:700px)');

let profiles=[];
let playlists=[];
let current=null;
let tracks=[];
let filtered=[];
let trackPositions=new Map();
let renderedTrackCount=0;
const TRACK_BATCH_SIZE=80;
let unavailablePlaylists=new Set();
let loadedProfileId='';
let playlistRequest=0;
let profileRequest=0;
let embeddedProfileRequest=0;
const likedRequests=new Map();
const unseenFavoriteKey=()=>{
 const profileId=loadedProfileId||PCHAuth.profile()||'default';
 const profile=profiles.find(row=>String(row.id)===String(profileId));
 const account=String(profile?.account?.id||profileId);
 const server=String(profile?.server?.machine||profileId);
 const username=String(PCHAuth.status()?.username||'user');
 return `pch-favorite-unseen:${username}:${account}:${server}:${profileId}`;
};
function markFavoriteUnseen(value){
 try{if(value)localStorage.setItem(unseenFavoriteKey(),'1');else localStorage.removeItem(unseenFavoriteKey());}catch(_error){}
 playlistSections.setFavoriteUnseen(value);
}
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
 const profile=track?.profile_id||context?.profileId||loadedProfileId||PCHAuth.profile();
 const params=new URLSearchParams();if(profile)params.set('profile_id',profile);
 const query=params.size?'?'+params.toString():'';
 return '/api/playlists/library/tracks/'+encoded(track.id)+'/'+type+query;
}
function playlistContext(item=current){return item?{kind:item.kind,key:item.key,profileId:loadedProfileId}:null;}

function paintLiked(track,liked,rating){
 track.liked=liked;track.user_rating=rating;
 for(const row of tracks){if(String(row.id)===String(track.id)){row.liked=liked;row.user_rating=rating;}}
 refreshLikedRows(track);playlistPlayer.syncLiked(track);librarySearch.updateLiked(track.id,liked,rating);
 const frame=$('playlistToolFrame');
 if(frame.contentWindow&&new URL(frame.src,location.href).pathname==='/external')frame.contentWindow.postMessage({type:'pch-player-like-state',trackId:String(track.id),profileId:loadedProfileId,liked,user_rating:rating},location.origin);
}
async function setLiked(track,liked){
 const trackId=String(track.id),profileId=loadedProfileId,profileGeneration=profileRequest;
 const trackProfileId=String(track.profile_id||profileId);
 if(trackProfileId!==profileId)throw Error('账户已切换，请重新选择歌曲');
 let pending=likedRequests.get(trackId);
 if(pending&&pending.profileId===profileId&&pending.profileGeneration===profileGeneration){
  pending.desired=!!liked;paintLiked(track,pending.desired,pending.desired?10:0);return pending.promise;
 }
 pending={profileId,profileGeneration,desired:!!liked,confirmed:!!track.liked,rating:track.user_rating,track,promise:null};
 likedRequests.set(trackId,pending);
 const isCurrent=()=>profileId===loadedProfileId&&profileGeneration===profileRequest&&likedRequests.get(trackId)===pending;
 paintLiked(track,pending.desired,pending.desired?10:0);
 pending.promise=(async()=>{
  try{
   while(pending.confirmed!==pending.desired){
    const sent=pending.desired;
    const result=await json('/api/playlists/tracks/liked','POST',{track_id:trackId,profile_id:trackProfileId,liked:sent,confirm:true});
    if(!isCurrent())return;
    if(!!result.liked!==sent||String(result.profile_id||trackProfileId)!==trackProfileId)throw Error('喜欢状态更新失败，请重试');
    const previous=pending.confirmed;pending.confirmed=!!result.liked;pending.rating=result.user_rating;
    const favorite=playlists.find(row=>row.kind==='favorite'&&row.key==='liked');
    if(favorite&&previous!==pending.confirmed&&favorite.count!==null&&favorite.count!==undefined){favorite.count=Math.max(0,Number(favorite.count)+(pending.confirmed?1:-1));renderPlaylistList();}
    if(!previous&&pending.confirmed&&!(workspace.current().type==='playlist'&&current?.kind==='favorite'))markFavoriteUnseen(true);
   }
   if(!isCurrent())return;
   paintLiked(pending.track,pending.confirmed,pending.rating);
   if(current?.kind==='favorite'&&!pending.confirmed){tracks=tracks.filter(row=>String(row.id)!==trackId);filtered=tracks.slice();renderTracks();}
  }catch(error){
   if(!isCurrent())return;
   paintLiked(pending.track,pending.confirmed,pending.rating);
   throw error;
  }finally{if(isCurrent())likedRequests.delete(trackId);}
 })();
 return pending.promise;
}
function updateLiked(track,liked){return setLiked(track,liked).catch(error=>notify(error.message||'喜欢状态更新失败',true));}

function refreshLikedRows(track){
 for(const row of $('playlistTracks').children){
  if(row.dataset.trackId!==String(track.id))continue;
  const heart=row.querySelector('.playlist-heart');if(!heart)continue;
  heart.textContent=track.liked?'♥':'♡';heart.setAttribute('aria-pressed',String(!!track.liked));
  heart.setAttribute('aria-label',(track.liked?'取消喜欢 ':'喜欢 ')+(track.title||'歌曲'));
 }
}
function refreshPlayingRows(){
 for(const row of $('playlistTracks').children){
  const original=Number(row.dataset.trackIndex),track=tracks[original];if(!track)continue;
  const isPlaying=playlistPlayer.isPlayingTrack(track,playlistContext());
  row.classList.toggle('playing',isPlaying);
  row.querySelector('.playlist-track-number').textContent=isPlaying&&!playlistPlayer.paused()?'❚❚':String(original+1);
 }
 postPreviewState();
}
function postPreviewState(){
 const frame=$('playlistToolFrame');
 if(!frame.contentWindow||new URL(frame.src,location.href).pathname!=='/external')return;
 frame.contentWindow.postMessage({type:'pch-player-preview-state',key:playlistPlayer.previewKey(),playing:$('playlistPlayer').dataset.playing==='true'},location.origin);
}
function renderNextTrackBatch(){
 const box=$('playlistTracks'),end=Math.min(filtered.length,renderedTrackCount+TRACK_BATCH_SIZE);
 const fragment=document.createDocumentFragment();
 for(let index=renderedTrackCount;index<end;index++){
  const track=filtered[index],original=trackPositions.get(track);
  const isPlaying=playlistPlayer.isPlayingTrack(track,playlistContext());
  const row=document.createElement('div');row.className='playlist-track'+(isPlaying?' playing':'');row.tabIndex=0;row.setAttribute('role','button');
  row.dataset.trackIndex=String(original);row.dataset.trackId=String(track.id);
  row.onclick=()=>playlistPlayer.playAt(tracks,original,playlistContext());row.onkeydown=event=>{if(event.target===row&&(event.key==='Enter'||event.key===' ')){event.preventDefault();playlistPlayer.playAt(tracks,original,playlistContext());}};
  const number=document.createElement('span');number.className='playlist-track-number';number.textContent=isPlaying&&!playlistPlayer.paused()?'❚❚':String(original+1);
  const identity=document.createElement('span');identity.className='playlist-track-identity';
  const title=document.createElement('strong');title.textContent=track.title||'未知歌曲';
  const artist=document.createElement('span');artist.className='playlist-track-artist';artist.textContent=track.artist||'未知歌手';
  const album=document.createElement('span');album.className='playlist-track-album';album.textContent=track.album||'—';
  const duration=document.createElement('span');duration.className='playlist-track-duration';duration.textContent=formatTime(track.duration);
  const actions=document.createElement('span');actions.className='playlist-track-actions';
  const heart=document.createElement('button');heart.type='button';heart.className='playlist-heart';heart.textContent=track.liked?'♥':'♡';heart.setAttribute('aria-pressed',String(!!track.liked));heart.setAttribute('aria-label',(track.liked?'取消喜欢 ':'喜欢 ')+(track.title||'歌曲'));
  heart.onclick=event=>{event.stopPropagation();setLiked(track,!track.liked).catch(error=>notify(error.message||'喜欢状态更新失败',true));};identity.append(heart,title);
  if(current?.can_remove_tracks){const remove=document.createElement('button');remove.type='button';remove.className='playlist-track-remove';remove.textContent='移除';remove.setAttribute('aria-label','从歌单移除 '+(track.title||'歌曲'));remove.onclick=event=>{event.stopPropagation();action(()=>removeTrack(track));};actions.append(remove);}
  row.append(number,identity,artist,album,duration,actions);fragment.append(row);
 }
 box.append(fragment);renderedTrackCount=end;
 const more=$('playlistLoadMore');more.hidden=end>=filtered.length;
 more.textContent='加载更多歌曲（已显示 '+end+' / '+filtered.length+'）';
}
function renderTracks(){
 $('playlistTracks').replaceChildren();$('playlistEmpty').hidden=!!filtered.length;
 document.querySelector('.playlist-track-scroll').scrollTop=0;
 trackPositions=new Map(tracks.map((track,index)=>[track,index]));renderedTrackCount=0;
 renderNextTrackBatch();
}

const playlistPlayer=createPlaylistPlayer({document,mediaUrl,formatTime,onStateChange:refreshPlayingRows,reportPlayback:reportWebPlayback,onLikedChange:updateLiked});
function playingFrom(item){return playlistPlayer.isContext(playlistContext(item));}
function renderPlaylistList(){
 playlistSections.setItems(playlists);
 try{playlistSections.setFavoriteUnseen(localStorage.getItem(unseenFavoriteKey())==='1');}catch(_error){}
 const view=workspace.current();if(view.type==='section')playlistSections.renderSection(view.section);
 workspace.renderNavigation();
}
async function refreshPlaylistSidebar(){
 const requestId=profileRequest,data=await json('/api/playlists');
 if(requestId!==profileRequest)return;
 playlists=Array.isArray(data.items)?data.items:[];renderPlaylistList();
}

function setPlaylistLoading(value){
 playlistLoading=!!value;
 $('playlistView').classList.toggle('is-loading',value);$('playlistView').setAttribute('aria-busy',String(value));
 $('playlistTracks').inert=playlistLoading;
 $('playlistLoadMore').disabled=playlistLoading;
 $('playlistManage').disabled=playlistLoading;$('playlistRename').disabled=playlistLoading;$('playlistRemove').disabled=playlistLoading;
 $('playlistPlayAll').disabled=playlistLoading||!tracks.length;
}
function restoreCurrentPlaylistSelection(fallbackSection='smart'){
 if(current){workspace.show({type:'playlist',kind:current.kind,key:current.key,panel:'playlist'});return;}
 playlistSections.renderSection(fallbackSection);workspace.show({type:'section',section:fallbackSection,panel:'section'});
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
  if(item.kind==='favorite'){item.count=tracks.length;markFavoriteUnseen(false);renderPlaylistList();}
  $('playlistKind').textContent=item.kind_label;$('playlistTitle').textContent=detail.title;$('playlistSummary').textContent=tracks.length+' 首歌曲'+(item.smart?' · 歌曲由 Plex 规则生成':'');
  $('playlistManage').hidden=!item.manage_url;$('playlistManage').textContent=item.kind==='external'?'整理导入来源':'调整此歌单';
  $('playlistRename').hidden=!item.can_rename;$('playlistRemove').hidden=!item.can_delete;$('playlistPlayAll').disabled=!tracks.length;$('playlistEmpty').textContent=item.kind==='plex'?'已在 Plex 创建，歌单里还没有歌曲。可搜索歌曲后添加。':'歌单里还没有歌曲';renderTracks();
 return true;
 }catch(error){
  if(requestId!==playlistRequest)return false;
  restoreCurrentPlaylistSelection(item.section||'smart');
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
 const data=await json('/api/plex/profiles');profiles=Array.isArray(data.items)?data.items.filter(row=>row.enabled):[];
 const selected=PCHAuth.profile()||String(data.active_profile_id||profiles[0]?.id||'');
 const select=$('playlistProfile');select.replaceChildren();
 for(const row of profiles){const option=document.createElement('option');option.value=row.id;option.textContent=profileLabel(row);select.append(option);}
 if(profiles.some(row=>row.id===selected))select.value=selected;
 return select.value;
}
function resetPlaylistView(){
 workspace.reset();current=null;$('playlistKind').textContent='我的歌单';$('playlistTitle').textContent='正在载入歌单';$('playlistSummary').textContent='';
 $('playlistManage').hidden=true;$('playlistRename').hidden=true;$('playlistRemove').hidden=true;$('playlistPlayAll').disabled=true;$('playlistEmpty').textContent='正在读取这个账户的歌单…';
}
function resetSession(){
 ++profileRequest;++playlistRequest;++embeddedProfileRequest;likedRequests.clear();loadedProfileId='';profiles=[];playlists=[];current=null;tracks=[];filtered=[];unavailablePlaylists=new Set();
 const renameDialog=$('playlistRenameDialog');if(renameDialog.open)renameDialog.close();
 const createDialog=$('playlistCreateDialog');if(createDialog.open)createDialog.close();
 playlistPlayer.stop();librarySearch.reset();setPlaylistLoading(false);resetPlaylistView();renderTracks();renderPlaylistList();setSidebarOpen(false);
}
async function switchProfile(profileId,persist=true){
 profileId=String(profileId||'');if(!profileId||profileId===loadedProfileId)return;
 const requestId=++profileRequest;++playlistRequest;likedRequests.clear();loadedProfileId=profileId;$('playlistProfile').value=profileId;
 const renameDialog=$('playlistRenameDialog');if(renameDialog.open)renameDialog.close();
 const createDialog=$('playlistCreateDialog');if(createDialog.open)createDialog.close();
 playlistPlayer.stop();librarySearch.reset();tracks=[];filtered=[];playlists=[];unavailablePlaylists=new Set();setPlaylistLoading(false);resetPlaylistView();renderTracks();renderPlaylistList();
 if(persist&&PCHAuth.profile()!==profileId)PCHAuth.setProfile(profileId);
 try{
  const favorite=await json('/api/playlists/favorite/ensure','POST');
  if(requestId!==profileRequest)return;
  if(favorite.status==='needs_review')notify('Plex“我喜欢”待核对：'+favorite.message,true);
 }catch(error){
  if(requestId!==profileRequest)return;
  notify('Plex“我喜欢”暂未同步：'+(error.message||'请稍后重试'),true);
 }
 await loadPlaylists(undefined,requestId);
}
async function removeTrack(track){
 if(!current)return;
 if(!await PCHUI.confirm('从“'+current.title+'”移除“'+track.title+'”？',{confirmText:'移除歌曲'}))return;
 const profileId=loadedProfileId,requestId=profileRequest,selected={...current};if(playingFrom(selected)&&String(track.id)===playlistPlayer.trackId())playlistPlayer.stop();
 const result=await json('/api/playlists/tracks/edit','POST',{kind:selected.kind,key:selected.key,track_id:String(track.id),operation:'remove',confirm:true});
 if(profileId!==loadedProfileId||requestId!==profileRequest)return;
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
function openRenameDialog(){
 if(!current?.can_rename)return;
 $('playlistRenameInput').value=current.title||'';$('playlistRenameDialog').showModal();$('playlistRenameInput').focus();$('playlistRenameInput').select();
}
async function confirmRename(){
 if(!current?.can_rename)return;
 const title=$('playlistRenameInput').value.trim();if(!title||title.length>80)throw Error('歌单名称需为 1—80 个字');
 const selected=current,profileId=loadedProfileId,requestId=playlistRequest;
 $('playlistRenameConfirm').disabled=true;
 try{
  const result=await json('/api/playlists/rename','POST',{kind:selected.kind,key:selected.key,title,confirm:true});
  if(profileId!==loadedProfileId||requestId!==playlistRequest||current!==selected)return;
  selected.title=result.title||title;$('playlistTitle').textContent=selected.title;$('playlistRenameDialog').close();renderPlaylistList();notify('歌单已重命名');
 }finally{$('playlistRenameConfirm').disabled=false;}
}

function openCreateDialog(){
 $('playlistCreateInput').value='';$('playlistCreateDialog').showModal();$('playlistCreateInput').focus();
}
async function confirmCreate(){
 const button=$('playlistCreateConfirm');if(button.disabled)return;
 const title=$('playlistCreateInput').value.trim();
 if(!title||title.length>80)throw Error('歌单名称需为 1—80 个字');
 const profileId=loadedProfileId,requestId=profileRequest;
 button.disabled=true;
 try{
  const result=await json('/api/playlists/create','POST',{title,confirm:true});
  if(profileId!==loadedProfileId||requestId!==profileRequest)return;
  $('playlistCreateDialog').close();notify('已在当前 Plex 账户创建歌单，可直接添加歌曲，无需再次发布');
  await loadPlaylists({kind:result.kind,key:result.key},requestId);
 }finally{button.disabled=false;}
}

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
 onLikedChange:updateLiked,
 onPlaylistChanged:async(kind,key,count)=>{syncPlaylistCount(kind,key,count);if(current?.kind===kind&&current?.key===key)await openPlaylist(current);},
});

function mount(){
 librarySearch.mount();playlistPlayer.mount();
 $('playlistLoadMore').onclick=renderNextTrackBatch;
 document.querySelector('.playlist-track-scroll').onscroll=event=>{
  const box=event.currentTarget;
  if(!playlistLoading&&renderedTrackCount<filtered.length&&box.scrollHeight-box.scrollTop-box.clientHeight<320)renderNextTrackBatch();
 };
 $('playlistSidebarToggle').onclick=()=>setSidebarOpen(!document.body.classList.contains('playlist-sidebar-open'));
 $('openSettings').onclick=()=>openWorkspacePage('/settings','设置','system');
 $('playlistSidebarBackdrop').onclick=()=>setSidebarOpen(false);
 compactSidebar.addEventListener('change',()=>setSidebarOpen(false));setSidebarOpen(false);
 $('playlistProfile').onchange=()=>action(()=>switchProfile($('playlistProfile').value,true));
 $('workspaceHome').onclick=()=>openSection('smart');$('playlistPlayAll').onclick=()=>playlistPlayer.playAt(tracks,0,playlistContext());
  $('playlistManage').onclick=()=>{if(current?.manage_url)openWorkspacePage(current.manage_url,'调整“'+current.title+'”','tool',{type:'playlist',kind:current.kind,key:current.key});};
 $('playlistRename').onclick=openRenameDialog;$('playlistRenameConfirm').onclick=()=>action(confirmRename);
 $('playlistRemove').onclick=()=>action(async()=>{
  if(!current?.can_delete||!await PCHUI.confirm('确认删除歌单“'+current.title+'”？\n只删除歌单，不删除音乐文件。',{confirmText:'删除歌单'}))return;
  const profileId=loadedProfileId,requestId=profileRequest,selected={kind:current.kind,key:current.key,title:current.title};if(playingFrom(selected))playlistPlayer.stop();
  const result=await json('/api/playlists/remove','POST',{kind:selected.kind,key:selected.key,title:selected.title,confirm:true});if(profileId!==loadedProfileId||requestId!==profileRequest)return;notify(result.message);current=null;await loadPlaylists({section:'smart'},requestId);
 });
 $('smartHubButton').onclick=()=>openSection('smart');$('libraryHubButton').onclick=()=>openSection('library');
 $('playlistSectionSettings').onclick=()=>{const section=workspace.current().section==='library'?'library':'smart';openWorkspacePage(section==='library'?'/library':'/mixes',section==='library'?'曲库整理设置':'智能歌单设置','tool',{type:'section',section});};
 $('customPlaylistRefresh').onclick=()=>action(()=>loadPlaylists(workspace.current(),profileRequest));
 $('customPlaylistCreate').onclick=openCreateDialog;
 $('mobileSettings').onclick=()=>openWorkspacePage('/settings','设置','system');
 $('playlistCreateConfirm').onclick=()=>action(confirmCreate);
 $('playlistCreateInput').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();action(confirmCreate);}};
 document.querySelectorAll('[data-tool-url]').forEach(button=>button.onclick=()=>openTool(button.dataset.toolUrl,button.title||button.textContent.trim()));
 document.querySelectorAll('[data-workspace-url]').forEach(button=>button.onclick=()=>openWorkspacePage(button.dataset.workspaceUrl,button.textContent.trim(),'system'));
 window.addEventListener('pch-profile-change',event=>{++embeddedProfileRequest;const profileId=String(event.detail?.profile_id||'');if(profileId&&profileId!==loadedProfileId)action(()=>switchProfile(profileId,false));});
 window.addEventListener('pch-auth-logout',resetSession);
 window.addEventListener('message',event=>{
  if(event.origin!==location.origin||event.source!==$('playlistToolFrame').contentWindow)return;
  if(event.data?.type==='pch-auth-logout'){PCHAuth.expire();return;}
  if(event.data?.type==='pch-profile-selected'){
   const sequence=++embeddedProfileRequest,profileId=String(event.data.profile_id||'');
   if(profileId&&profileId!==loadedProfileId)action(async()=>{
    if(!profiles.some(row=>row.id===profileId))await loadProfiles();
    if(sequence!==embeddedProfileRequest||PCHAuth.profile()!==profileId)return;
    if(profiles.some(row=>row.id===profileId))await switchProfile(profileId,false);
   });
   return;
  }
  if(event.data?.type==='pch-playlists-changed'){action(refreshPlaylistSidebar);return;}
  if(event.data?.type==='pch-open-playlist'){
   const item=playlists.find(row=>row.kind===event.data.kind&&String(row.key)===String(event.data.key));
   if(item)action(()=>openPlaylist(item));else action(async()=>{await refreshPlaylistSidebar();const fresh=playlists.find(row=>row.kind===event.data.kind&&String(row.key)===String(event.data.key));if(fresh)await openPlaylist(fresh);});
   return;
  }
  if(event.data?.type==='pch-workspace-navigate'){
   const target=new URL(String(event.data.path||''),location.origin);
   if(target.origin!==location.origin)return;
   if(target.pathname==='/'){action(returnFromWorkspace);return;}
   const destinations={'/settings':['设置','system'],'/status':['运行状态','system'],'/library':['曲库整理设置','tool'],'/mixes':['智能歌单设置','tool'],'/daily':['每日推荐','tool'],'/external':['外部歌单','tool']};
   const destination=destinations[target.pathname];if(destination)openWorkspacePage(target.pathname+target.search,...destination);
   return;
  }
  if(event.data?.type==='pch-player-preview-query'){postPreviewState();return;}
  if(event.data?.type!=='pch-player-preview')return;
  const track=event.data.track;if(!track||typeof track.source!=='string'||!track.source.startsWith('/api/external/'))return;
  if(!loadedProfileId||String(track.profile_id||'')!==loadedProfileId||String(track.profileId||'')!==loadedProfileId)return;
  const source=new URL(track.source,location.origin);
  if(source.origin!==location.origin||source.searchParams.get('profile_id')!==loadedProfileId)return;
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
