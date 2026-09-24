function playlistPriority(item){
 if(item.kind==='daily')return 0;
 if(item.kind==='favorite')return 1;
 if(item.kind==='plex'||item.kind==='external')return 2;
 if(item.kind==='smart'&&item.key==='weekly')return 3;
 if(item.kind==='smart'&&item.key==='time_capsule')return 4;
 if(item.kind==='category')return 6;
 return 5;
}

export function orderPlaylists(items){
 return (items||[]).map((item,index)=>({item,index})).sort((left,right)=>playlistPriority(left.item)-playlistPriority(right.item)||left.index-right.index).map(row=>row.item);
}

export function groupPlaylists(items){
 const ordered=orderPlaylists(items);
 const sidebarGroup=item=>item.sidebar_group||(item.kind==='favorite'?'favorite':item.kind==='external'?'personal':item.kind==='plex'?(item.smart?'plex_smart':'personal'):'');
 const sidebarPriority={favorite:0,personal:1,plex_smart:2};
 const sidebar=ordered.map((item,index)=>({item,index,group:sidebarGroup(item)})).filter(row=>row.group).sort((left,right)=>sidebarPriority[left.group]-sidebarPriority[right.group]||left.index-right.index).map(row=>row.item);
 return {
  all:ordered,
  sidebar,
  smart:ordered.filter(row=>row.section==='smart'),
  library:ordered.filter(row=>row.section==='library'),
  custom:ordered.filter(row=>row.section==='custom'),
 };
}

export function createPlaylistSections({document,onOpenPlaylist,onOpenTool,createArtwork}){
 let groups=groupPlaylists([]);
 let favoriteUnseen=false;
 let selectedScope='';
 let customViews=new Map(),cardViews=new Map();
 const byId=id=>document.getElementById(id);
 const itemKey=item=>item.kind+'\t'+item.key;

 function countLabel(item){
  if(!item.can_play)return '尚未生成';
  if(item.count===null||item.count===undefined)return '打开后同步歌曲数';
  const count=Number(item.count);
  return Number.isFinite(count)?count+' 首歌曲':'歌曲数未知';
 }
 function activate(item){
  if(item.can_play)return onOpenPlaylist(item);
  if(item.manage_url)return onOpenTool(item.manage_url,item.title,{type:'section',section:item.section});
 }
 function renderCustom(refreshArtwork=true){
  const box=byId('customPlaylistList'),nextViews=new Map(),buttons=[];
  for(const item of groups.sidebar){
   const key=itemKey(item);let view=customViews.get(key);
   if(!view){
    const button=document.createElement('button'),text=document.createElement('span'),title=document.createElement('strong'),meta=document.createElement('small');
    button.type='button';text.append(title,meta);
    view={button,text,title,meta,cover:createArtwork(item,'sidebar'),dot:null};
   }else if(refreshArtwork)view.cover=createArtwork(item,'sidebar',view.cover);
   const {button,text,title,meta}=view;button.dataset.kind=item.kind;button.dataset.key=item.key;
   title.textContent=item.title;meta.textContent=countLabel(item);button.replaceChildren(view.cover,text);
   if(item.kind==='favorite'&&favoriteUnseen){
    if(!view.dot){view.dot=document.createElement('span');view.dot.className='playlist-favorite-dot';view.dot.setAttribute('aria-label','有新收藏歌曲');}
    button.classList.add('has-unseen');button.append(view.dot);
   }else{button.classList.remove('has-unseen');view.dot=null;}
   button.onclick=()=>onOpenPlaylist(item);nextViews.set(key,view);buttons.push(button);
  }
  customViews=nextViews;
  if(!buttons.length){const empty=document.createElement('span');empty.className='playlist-side-empty';empty.textContent='还没有歌单';buttons.push(empty);}
  box.replaceChildren(...buttons);
 }
 function renderSection(section){
  const rows=groups[section]||[],box=byId('playlistSectionCards'),cards=[];
  byId('playlistSectionTitle').textContent=section==='smart'?'智能歌单':'曲库整理';
  for(const item of rows){
   const key=section+'\t'+itemKey(item);let view=cardViews.get(key);
   if(!view){
    const card=document.createElement('button'),body=document.createElement('span'),title=document.createElement('strong'),count=document.createElement('small');
    card.type='button';card.className='playlist-section-card';body.className='playlist-section-card-body';body.append(title,count);
    view={card,body,title,count,cover:createArtwork(item,'card')};cardViews.set(key,view);
   }else view.cover=createArtwork(item,'card',view.cover);
   const {card,body,title,count}=view;
   card.dataset.kind=item.kind;card.dataset.key=item.key;card.setAttribute('aria-label','打开'+item.title);
   title.textContent=item.title;count.textContent=countLabel(item);card.replaceChildren(view.cover,body);
   card.onclick=()=>activate(item);cards.push(card);
  }
  if(!rows.length){
   const empty=document.createElement('div');empty.className='playlist-section-empty';
   empty.textContent=section==='library'?'还没有曲库整理歌单。':'还没有智能歌单。';
   const action=document.createElement('button');action.type='button';action.className='secondary';action.textContent=section==='library'?'开始整理':'创建智能歌单';
   action.onclick=()=>onOpenTool(section==='library'?'/library':'/mixes',action.textContent,{type:'section',section});empty.append(action);cards.push(empty);
  }
  box.replaceChildren(...cards);
 }
 function setItems(items,nextScope=''){
  const scope=String(nextScope||'');
  if(!scope||scope!==selectedScope){selectedScope=scope;customViews=new Map();cardViews=new Map();}
  groups=groupPlaylists(Array.isArray(items)?items:[]);
  const validCards=new Set([...groups.smart,...groups.library].map(item=>item.section+'\t'+itemKey(item)));
  for(const key of cardViews.keys())if(!validCards.has(key))cardViews.delete(key);
  renderCustom();return groups;
 }
 function setFavoriteUnseen(value){favoriteUnseen=!!value;renderCustom(false);}
 return {setItems,renderSection,setFavoriteUnseen,groups:()=>groups};
}
