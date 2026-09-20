function normalizedPath(value){
 return new URL(value,window.location.origin).pathname;
}

export function createPlaylistWorkspace({document}){
 let current={type:'playlist',kind:'',key:'',panel:'playlist'};
 const playlistView=document.getElementById('playlistView');
 const playlistSearchView=document.getElementById('playlistSearchView');
 const playlistToolView=document.getElementById('playlistToolView');

 function matches(button,view){
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
  document.querySelectorAll('#playlistList button,#playlistTools button,[data-workspace-url]').forEach(button=>{
   button.classList.toggle('active',matches(button,current));
  });
 }

 function show(next){
  current={panel:next.type,...next};
  playlistView.hidden=current.panel!=='playlist';
  playlistSearchView.hidden=current.panel!=='search';
  playlistToolView.hidden=!['tool','system'].includes(current.panel);
  renderNavigation();
 }

 function openPage(url,title,{type='tool',navigation=null}={}){
  const target=new URL(url,window.location.origin);
  target.searchParams.set('embedded','1');
  document.getElementById('playlistToolTitle').textContent=title;
  const frame=document.getElementById('playlistToolFrame');
  frame.removeAttribute('style');
  frame.setAttribute('scrolling','auto');
  frame.src=target.pathname+target.search;
  show({...(navigation||{type,url:target.pathname}),panel:type==='system'?'system':'tool'});
 }

 function reset(){
  const frame=document.getElementById('playlistToolFrame');
  if(frame)frame.src='about:blank';
  show({type:'playlist',kind:'',key:'',panel:'playlist'});
 }

 return {show,openPage,reset,renderNavigation,current:()=>({...current})};
}
