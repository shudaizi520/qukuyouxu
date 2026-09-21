function normalizedPath(value){
 return new URL(value,window.location.origin).pathname;
}

export function createPlaylistWorkspace({document}){
 let current={type:'playlist',kind:'',key:'',panel:'playlist'};
 const playlistView=document.getElementById('playlistView');
 const playlistSectionView=document.getElementById('playlistSectionView');
 const playlistSearchView=document.getElementById('playlistSearchView');
 const playlistToolView=document.getElementById('playlistToolView');

 function matches(button,view){
  if(button.dataset.section)return view.type==='section'&&button.dataset.section===view.section;
  if(button.dataset.kind){
   return view.type==='playlist'&&button.dataset.kind===view.kind&&button.dataset.key===view.key;
  }
  if(button.dataset.toolUrl){
   return view.type==='tool'&&normalizedPath(button.dataset.toolUrl)===view.url;
  }
  if(button.dataset.workspaceUrl){
   return view.type==='system'&&normalizedPath(button.dataset.workspaceUrl)===view.url;
  }
  return false;
 }

 function renderNavigation(){
  document.querySelectorAll('#smartHubButton,#libraryHubButton,#customPlaylistList button,[data-tool-url],[data-workspace-url]').forEach(button=>{
   button.classList.toggle('active',matches(button,current));
  });
 }

 function show(next){
  current={panel:next.type,...next};
  playlistSectionView.hidden=current.panel!=='section';
  playlistView.hidden=current.panel!=='playlist';
  playlistSearchView.hidden=current.panel!=='search';
  playlistToolView.hidden=!['tool','system'].includes(current.panel);
  renderNavigation();
 }

 function openPage(url,title,{type='tool',navigation=null}={}){
 const target=new URL(url,window.location.origin);
  target.searchParams.set('embedded','1');
  playlistToolView.dataset.page=target.pathname.slice(1);
  const frame=document.getElementById('playlistToolFrame');
  frame.title=title;
  frame.removeAttribute('style');
  frame.setAttribute('scrolling','auto');
  frame.src=target.pathname+target.search;
  show({...(navigation||{type,url:target.pathname}),panel:type==='system'?'system':'tool'});
 }

 function reset(){
  const frame=document.getElementById('playlistToolFrame');
  if(frame)frame.src='about:blank';
  show({type:'section',section:'smart',panel:'section'});
 }

 return {show,openPage,reset,renderNavigation,current:()=>({...current})};
}
