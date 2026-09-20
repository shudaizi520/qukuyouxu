const byId=(document,id)=>document.getElementById(id);

export function createLibrarySearch({
 document,requestJson,getProfileId,getPlaylists,getCurrentPlaylist,
 onPlayQueue,onPlaylistChanged,onSearchStart,onBack,notify,
}){
 let requestGeneration=0;
 let results=[];
 let pendingTrack=null;

 function fillTargets(){
  const select=byId(document,'librarySearchTarget');
  select.replaceChildren();
  for(const item of getPlaylists().filter(row=>row.playlist_id)){
   const option=document.createElement('option');
   option.value=item.kind+'\t'+item.key;
   option.textContent=item.title;
   select.append(option);
  }
  const current=getCurrentPlaylist();
  if(current)select.value=current.kind+'\t'+current.key;
 }

 function openAddDialog(track){
  pendingTrack=track;
  fillTargets();
  byId(document,'librarySearchSelected').textContent=(track.title||'未知歌曲')+' · '+(track.artist||'未知歌手');
  byId(document,'librarySearchDialog').showModal();
 }

 function render(emptyText='没有找到歌曲'){
  const box=byId(document,'librarySearchResults');
  box.replaceChildren();
  results.forEach((track,index)=>{
   const row=document.createElement('div');
   row.className='playlist-search-result';row.tabIndex=0;row.setAttribute('role','button');
   const play=()=>onPlayQueue(results.slice(),index,{kind:'library',key:'all',profileId:getProfileId()});
   row.onclick=play;row.onkeydown=event=>{if(event.target===row&&(event.key==='Enter'||event.key===' ')){event.preventDefault();play();}};
   const number=document.createElement('span');number.className='playlist-track-number';number.textContent=String(index+1);
   const identity=document.createElement('span'),title=document.createElement('strong'),artist=document.createElement('small');
   title.textContent=track.title||'未知歌曲';artist.textContent=track.artist||'未知歌手';identity.append(title,artist);
   const album=document.createElement('span');album.className='playlist-search-album';album.textContent=track.album||'—';
   const add=document.createElement('button');add.type='button';add.className='secondary';add.textContent='添加到歌单';
   add.onclick=event=>{event.stopPropagation();openAddDialog(track);};
   row.append(number,identity,album,add);box.append(row);
  });
  if(!results.length&&emptyText){const empty=document.createElement('div');empty.className='playlist-empty';empty.textContent=emptyText;box.append(empty);}
 }

 async function run(query){
  query=String(query||'').trim();
  if(!query){onBack();return;}
  const requestId=++requestGeneration,profileId=getProfileId();
  onSearchStart(query);
  byId(document,'librarySearchTitle').textContent='“'+query+'”';
  byId(document,'librarySearchSummary').textContent='正在搜索当前曲库…';
  results=[];render('');
  try{
   const response=await requestJson('/api/playlists/search?q='+encodeURIComponent(query));
   if(requestId!==requestGeneration||profileId!==getProfileId())return;
   results=Array.isArray(response.items)?response.items:[];
   byId(document,'librarySearchSummary').textContent='找到 '+results.length+' 首歌曲';
   render();
  }catch(error){
   if(requestId!==requestGeneration||profileId!==getProfileId())return;
   results=[];byId(document,'librarySearchSummary').textContent='搜索失败，请重试';render('暂时无法搜索，请重试');throw error;
  }
 }

 async function confirmAdd(){
  if(!pendingTrack)return;
  const button=byId(document,'librarySearchConfirm');
  const [kind,key]=byId(document,'librarySearchTarget').value.split('\t');
  if(!kind||!key)throw Error('请选择目标歌单');
  button.disabled=true;
  try{
   const result=await requestJson('/api/playlists/tracks/edit','POST',{kind,key,track_id:String(pendingTrack.id),operation:'add',confirm:true});
   notify('已加入歌单');
   byId(document,'librarySearchDialog').close();
   pendingTrack=null;
   await onPlaylistChanged(kind,key,result.count);
  }finally{button.disabled=false;}
 }

 function reset(){
  requestGeneration+=1;results=[];pendingTrack=null;byId(document,'librarySearchInput').value='';
  const dialog=byId(document,'librarySearchDialog');if(dialog.open)dialog.close();
 }
 function focus(){byId(document,'librarySearchInput').focus();byId(document,'librarySearchInput').select();}
 function mount(){
  byId(document,'librarySearchForm').onsubmit=event=>{event.preventDefault();run(byId(document,'librarySearchInput').value).catch(error=>notify(error.message||'搜索失败',true));};
  byId(document,'librarySearchBack').onclick=onBack;
  byId(document,'librarySearchConfirm').onclick=()=>confirmAdd().catch(error=>notify(error.message||'添加失败',true));
 }

 return {mount,run,reset,focus,items:()=>results.slice()};
}
