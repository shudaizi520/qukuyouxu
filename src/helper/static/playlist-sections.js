export function groupPlaylists(items){
 return {
  smart:items.filter(row=>row.section==='smart'),
  library:items.filter(row=>row.section==='library'),
  custom:items.filter(row=>row.section==='custom'),
 };
}

export function createPlaylistSections({document,onOpenPlaylist,onOpenTool}){
 let groups=groupPlaylists([]);
 let favoriteUnseen=false;
 const byId=id=>document.getElementById(id);

 function countLabel(item){
  if(!item.can_play)return '尚未生成';
  if(item.count===null||item.count===undefined)return '打开后同步歌曲数';
  const count=Number(item.count);
  return Number.isFinite(count)?count+' 首歌曲':'歌曲数未知';
 }
 function description(item){
  if(item.kind==='favorite')return '你在 Plex 中评为四星或五星的歌曲';
  if(item.kind==='daily')return '根据近期播放与偏好持续更新';
  if(item.source==='plex'&&item.smart)return 'Plex 动态智能歌单';
  if(item.source==='plex')return 'Plex / Plexamp 自建歌单';
  if(item.source==='external')return '从外部平台导入并匹配到本地曲库';
  return item.section==='library'?'由当前曲库整理生成':'智能算法生成';
 }
 function activate(item){
  if(item.can_play)return onOpenPlaylist(item);
  if(item.manage_url)return onOpenTool(item.manage_url,item.title,{type:'section',section:item.section});
 }
 function renderCustom(){
  const box=byId('customPlaylistList');box.replaceChildren();
  for(const item of groups.custom){
   const button=document.createElement('button');button.type='button';button.dataset.kind=item.kind;button.dataset.key=item.key;
   const text=document.createElement('span'),title=document.createElement('strong'),meta=document.createElement('small');
   title.textContent=item.title;meta.textContent=(item.smart?'智能 · ':'')+countLabel(item)+(item.kind==='favorite'&&item.status?' · '+item.status:'');text.append(title,meta);button.append(text);
   if(item.kind==='favorite'&&favoriteUnseen){button.classList.add('has-unseen');const dot=document.createElement('span');dot.className='playlist-favorite-dot';dot.setAttribute('aria-label','有新收藏歌曲');button.append(dot);}
   button.onclick=()=>onOpenPlaylist(item);box.append(button);
  }
  if(!groups.custom.length){const empty=document.createElement('span');empty.className='playlist-side-empty';empty.textContent='还没有自建歌单';box.append(empty);}
 }
 function renderSection(section){
  const rows=groups[section]||[],box=byId('playlistSectionCards');box.replaceChildren();
  byId('playlistSectionKind').textContent=section==='smart'?'为你整理':'曲库分类';
  byId('playlistSectionTitle').textContent=section==='smart'?'智能歌单':'曲库整理';
  byId('playlistSectionSummary').textContent=section==='smart'?'每天打开就能直接听':'按年代、风格与评分整理本地音乐';
  byId('playlistSectionSettings').textContent=section==='smart'?'设置智能歌单':'设置曲库整理';
  for(const item of rows){
   const card=document.createElement('button');card.type='button';card.className='playlist-section-card';
   card.dataset.kind=item.kind;card.dataset.key=item.key;card.setAttribute('aria-label','打开'+item.title);
   const art=document.createElement('span');art.className='playlist-section-art';art.textContent=item.kind==='favorite'?'♥':'♫';
   const body=document.createElement('span');body.className='playlist-section-card-body';
   const title=document.createElement('strong');title.textContent=item.title;
   const detail=document.createElement('span');detail.textContent=description(item);
   const count=document.createElement('small');count.textContent=countLabel(item);body.append(title,detail,count);card.append(art,body);
   card.onclick=()=>activate(item);box.append(card);
  }
  if(!rows.length){
   const empty=document.createElement('div');empty.className='playlist-section-empty';
   empty.textContent=section==='library'?'还没有曲库整理歌单。':'还没有智能歌单。';
   const action=document.createElement('button');action.type='button';action.className='secondary';action.textContent=section==='library'?'开始整理':'创建智能歌单';
   action.onclick=()=>onOpenTool(section==='library'?'/library':'/mixes',action.textContent,{type:'section',section});empty.append(action);box.append(empty);
  }
 }
 function setItems(items){groups=groupPlaylists(Array.isArray(items)?items:[]);renderCustom();return groups;}
 function setFavoriteUnseen(value){favoriteUnseen=!!value;renderCustom();}
 return {setItems,renderSection,setFavoriteUnseen,groups:()=>groups};
}
