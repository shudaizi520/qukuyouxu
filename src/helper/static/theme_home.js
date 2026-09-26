/* Compact, type-aware song preview for library review groups. */
(()=>{
 'use strict';
 const get=id=>document.getElementById(id);
 let evidenceCategory='',evidenceKind='theme',nextOffset=null,onlyNew=true,requestGeneration=0,loadingEvidence='',totalEvidence=0,loadedEvidence=0;
 const rows=()=>get('themeEvidenceRows');
 const say=(parent,tag,text,cls)=>{const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;parent.append(e);return e;};
 function safeQQ(value){try{const u=new URL(value);if(u.protocol==='https:'&&['y.qq.com','i.y.qq.com'].includes(u.hostname)&&!u.username&&!u.password)return u.href;}catch{}return null;}
 function endpoint(offset){
  const base='/api/base/details?category_id='+encodeURIComponent(evidenceCategory)+'&offset='+offset+'&limit=100';
  const theme='/api/themes/evidence?category_id='+encodeURIComponent(evidenceCategory)+'&offset='+offset+'&limit=100&added_only='+String(onlyNew);
  return evidenceKind==='base'?base:theme;
 }
 function setState(text,kind=''){
  const state=get('themeEvidenceState');state.textContent=text||'';state.className='song-preview-state'+(kind?' '+kind:'');state.hidden=!text;
 }
 function clearPreview(message='正在读取歌曲…'){
  rows().replaceChildren();get('themeEvidenceTitle').textContent='歌曲预览';get('themeEvidenceNote').textContent='';
  get('themeEvidenceSources').hidden=true;get('themeEvidenceSources').open=false;get('themeEvidenceSourceList').replaceChildren();get('themeEvidenceColumns').hidden=true;
  get('themeEvidenceProgress').textContent='';get('moreThemeEvidence').hidden=true;totalEvidence=0;loadedEvidence=0;setState(message,'loading');
 }
 function normalized(row){
  return {
   id:String(row.id||row.track_id||''),title:String(row.title||'未命名歌曲'),artist:String(row.artist||'未知歌手'),
   album:String(row.album||row.album_title||''),reason:String(row.reason||((row.methods||[]).includes('verified_mid')?'已核对歌曲编号与版本信息':'歌名、歌手和版本信息匹配')),
   origins:Array.isArray(row.origins)?row.origins:[],inferred:!!row.inferred,
  };
 }
 function renderSources(origins){
  const unique=[];
  for(const origin of origins||[]){
   const key=String(origin.url||'')+'\u0000'+String(origin.title||'');
   if(!unique.some(row=>row.key===key))unique.push({key,origin});
  }
  if(!unique.length)return;
  const details=get('themeEvidenceSources'),list=get('themeEvidenceSourceList');list.replaceChildren();
  details.querySelector('summary').textContent='参考来源（'+unique.length+'）';details.hidden=false;
  for(const {origin} of unique){
   const line=document.createElement('p'),url=safeQQ(origin.url);
   if(url){const link=say(line,'a',origin.title||'QQ 参考歌单');link.href=url;link.target='_blank';link.rel='noopener noreferrer';}
   else say(line,'span',origin.title||'参考歌单');
   if(origin.basis)say(line,'small',origin.basis,'muted');list.append(line);
  }
 }
 function renderItem(raw,index){
  const item=normalized(raw),card=document.createElement('article');card.className='song-preview-row';
  say(card,'span',String(index),'song-preview-number');
  const identity=document.createElement('div');identity.className='song-preview-identity';say(identity,'strong',item.title);say(identity,'span',item.artist,'muted');
  const album=document.createElement('div');album.className='song-preview-album';say(album,'span',item.album||'—');
  const reason=document.createElement('div');reason.className='song-preview-reason';say(reason,'span',item.reason+(item.inferred?' · 推断补充':''));
  card.append(identity,album,reason);
  rows().append(card);
 }
 function updateProgress(){
  const progress=get('themeEvidenceProgress');
  progress.textContent=totalEvidence?(nextOffset==null?'已显示全部 '+loadedEvidence+' 首':'已显示 '+loadedEvidence+' / '+totalEvidence+' 首'):'';
 }
 async function loadEvidence(offset=0,generation=requestGeneration){
  const loadToken=generation+':'+offset;if(loadingEvidence===loadToken)return;loadingEvidence=loadToken;
  if(offset===0)clearPreview();
  else{get('moreThemeEvidence').hidden=true;get('themeEvidenceProgress').textContent='正在继续读取…';}
  try{
   const data=await (await request(endpoint(offset))).json();if(generation!==requestGeneration)return;
   if(offset===0)rows().replaceChildren();
   get('themeEvidenceTitle').textContent=String(data.title||'歌曲预览');
   totalEvidence=Number(data.total||0);get('themeEvidenceNote').textContent='共 '+totalEvidence+' 首 · 滚动即可查看全部歌曲';
   setState('');
   const items=data.items||[];if(offset===0){const sourceRow=items.find(row=>Array.isArray(row.origins)&&row.origins.length);if(sourceRow)renderSources(sourceRow.origins);}
   for(const row of items)renderItem(row,++loadedEvidence);
   if(!(data.items||[]).length&&offset===0)setState('这个歌单当前没有可预览的歌曲。','empty');
   nextOffset=data.next;get('themeEvidenceColumns').hidden=!loadedEvidence;updateProgress();
  }catch(error){
   if(generation!==requestGeneration)return;
   if(offset===0){rows().replaceChildren();get('themeEvidenceTitle').textContent='歌曲预览';get('themeEvidenceNote').textContent='';get('themeEvidenceColumns').hidden=true;setState(error?.message||'歌曲预览读取失败，请重新分析后再试。','error');}
   else{get('themeEvidenceProgress').textContent='后续歌曲读取失败';get('moreThemeEvidence').hidden=false;get('moreThemeEvidence').textContent='重试加载';}
  }finally{
   if(loadingEvidence===loadToken)loadingEvidence='';if(generation===requestGeneration)get('themeEvidence').removeAttribute('aria-busy');
  }
 }
 function closePreview(){
  requestGeneration++;get('themeEvidence').hidden=true;get('songPreviewBackdrop').hidden=true;document.body.classList.remove('song-preview-open');
 }
 window.openThemeEvidence=async(categoryId,added=true,kind='theme')=>{
  evidenceCategory=String(categoryId||'');evidenceKind=kind==='base'?'base':'theme';onlyNew=!!added;nextOffset=null;loadingEvidence='';requestGeneration++;
  get('themeEvidence').hidden=false;get('songPreviewBackdrop').hidden=false;get('themeEvidence').setAttribute('aria-busy','true');document.body.classList.add('song-preview-open');
  get('closeThemeEvidence').focus();await loadEvidence(0,requestGeneration);
 };
 rows().addEventListener('scroll',()=>{if(nextOffset!=null&&!loadingEvidence&&rows().scrollHeight-rows().scrollTop-rows().clientHeight<240)loadEvidence(nextOffset,requestGeneration);},{passive:true});
 get('moreThemeEvidence').onclick=()=>loadEvidence(nextOffset||0,requestGeneration);
 get('closeThemeEvidence').onclick=closePreview;get('songPreviewBackdrop').onclick=closePreview;
 document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!get('themeEvidence').hidden)closePreview();});
})();


/* v0.3.6: explicit lifecycle controls and evidence-based duplicate hints. */
(()=>{
 'use strict';
 const get=id=>document.getElementById(id);let loading=false,lastLoaded=0;
 const button=(text,cls,fn,disabled=false)=>{const b=document.createElement('button');b.type='button';b.className=cls;b.textContent=text;b.disabled=disabled;b.onclick=fn;return b;};
 async function loadManaged(force=false){
  if(loading||(!force&&Date.now()-lastLoaded<10000)||!get('managedPlaylists'))return;
  loading=true;try{
   const [data,overlap]=await Promise.all([(await request('/api/managed/playlists')).json(),(await request('/api/themes/overlaps')).json()]);
   renderManaged(data);renderOverlaps(overlap);lastLoaded=Date.now();
  }catch(e){note(e.message,true);}finally{loading=false;}
 }
 function renderManaged(data){
  const box=get('managedPlaylists');box.replaceChildren();
  const managedCard=box.closest('.managed-playlists-card'),retiredRows=data.retired||[];
  if(managedCard)managedCard.hidden=!(data.items||[]).length&&!retiredRows.length;
  for(const row of data.items||[]){
   const line=document.createElement('article');line.className='managed-playlist-row';
   const openPlaylist=()=>{if(window.parent!==window)window.parent.postMessage({type:'pch-open-playlist',kind:'category',key:String(row.category_id)},location.origin);else location.assign('/');};
   const info=document.createElement('div'),title=button(row.title,'managed-playlist-title',openPlaylist),meta=document.createElement('span');
   meta.textContent=(row.count==null?'曲目数待核对':row.count+' 首')+' · '+row.status+(row.enabled?'':' · 已停止维护');info.append(title,meta);
   const actions=document.createElement('div');actions.className='managed-playlist-actions managed-playlist-actions-inline';
   const open=button('打开','managed-playlist-action managed-playlist-open',openPlaylist);actions.append(open);
   if(row.safe_to_forget){
    actions.append(button('清除记录','managed-playlist-action danger-text',async()=>{if(!await PCHUI.confirm('Plex 中已经找不到“'+row.title+'”。只清除助手里的历史记录，不删除歌曲或音乐文件。确定继续？'))return;await action(async()=>{const r=await post('/api/managed/forget',{confirm:true,category_id:row.category_id,playlist_id:row.playlist_id,title:row.title});note(r.message);await loadManaged(true);await refresh();});}));
   }else{
    if(row.can_accept_changes){
     actions.append(button('接受改动','managed-playlist-action',async()=>{if(!await PCHUI.confirm('接受“'+row.title+'”当前在 Plex 里的删除结果？\n\n被删掉的歌曲以后也不会被自动加回；不会修改音乐文件。'))return;await action(async()=>{const r=await post('/api/managed/reconcile',{confirm:true,category_id:row.category_id,action:'accept'});note(r.message);await loadManaged(true);await refresh();});}));
    }
    if(row.can_restore_changes){
     actions.append(button('恢复原状','managed-playlist-action',async()=>{if(!await PCHUI.confirm('把“'+row.title+'”在 Plex 外部删除的歌曲加回来？不会重建歌单，也不会修改音乐文件。'))return;await action(async()=>{const r=await post('/api/managed/reconcile',{confirm:true,category_id:row.category_id,action:'restore'});note(r.message);await loadManaged(true);await refresh();});}));
    }
    actions.append(button(row.enabled?'停止维护':'恢复维护','managed-playlist-action',async()=>{const verb=row.enabled?'停止':'恢复';if(!await PCHUI.confirm(verb+'维护“'+row.title+'”？Plex 中现有歌单和歌曲都会保留。'))return;await action(async()=>{const r=await post(row.enabled?'/api/managed/disable':'/api/managed/enable',{confirm:true,category_id:row.category_id});note(r.message);await loadManaged(true);await refresh();});}));
    const remove=button('移除歌单','managed-playlist-action danger-text',async()=>{if(!await PCHUI.confirm('从 Plex 移除助手创建的“'+row.title+'”（'+row.count+' 首）？\\n\\n只删除这张歌单，不删除歌曲；操作前会保存恢复快照。'))return;await action(async()=>{const r=await post('/api/managed/remove',{confirm:true,category_id:row.category_id,title:row.title});note(r.message);await loadManaged(true);await refresh();});},!row.safe_to_remove);
    if(!row.safe_to_remove)remove.title='只有当前账户中带助手管理标记、身份一致且不受保护的歌单可以移除';actions.append(remove);
   }
   line.append(info,actions);box.append(line);
  }
  const retired=get('retiredPlaylists'),disclosure=get('retiredDisclosure');retired.replaceChildren();
  disclosure.hidden=!retiredRows.length;get('retiredCount').textContent=retiredRows.length?retiredRows.length+' 个':'';
  for(const row of retiredRows){const line=document.createElement('article');line.className='managed-playlist-row retired';const info=document.createElement('div'),title=document.createElement('strong'),meta=document.createElement('span');title.textContent=row.title;meta.textContent='已移除 · 可从快照恢复';info.append(title,meta);line.append(info,button('恢复歌单','secondary',async()=>{if(!await PCHUI.confirm('从安全快照恢复“'+row.title+'”？恢复后默认停止维护。'))return;await action(async()=>{const r=await post('/api/managed/restore',{confirm:true,snapshot_id:row.snapshot_id});note(r.message);await loadManaged(true);await refresh();});}));retired.append(line);}
 }
 function renderOverlaps(data){
  const box=get('overlapNotice');box.replaceChildren();const rows=data.items||[];box.hidden=!rows.length;
  if(!rows.length)return;const heading=document.createElement('strong');heading.textContent='发现内容高度重合的分类';box.append(heading);
  for(const row of rows.slice(0,5)){const p=document.createElement('p');p.className='overlap-row';p.textContent=row.left_title+' 与 '+row.right_title+' 共同 '+row.shared+' 首（重合 '+Math.round(row.containment*100)+'%），建议优先保留“'+row.recommend_keep_title+'”。';box.append(p);}
 }
 const prior=window.renderThemeExtras;
 window.renderThemeExtras=data=>{if(prior)prior(data);if(!data.workflow?.job?.running)loadManaged(false);};
 window.addEventListener('pch-auth-ready',()=>loadManaged(true));window.addEventListener('pch-auth-login',()=>loadManaged(true));
})();
