const encoded=value=>encodeURIComponent(String(value||''));

export function artworkUrl(trackId,profileId){
 return '/api/playlists/library/tracks/'+encoded(trackId)+'/artwork?profile_id='+encoded(profileId);
}

export function createPlaylistArtwork({document,request,profileId}){
 let selectedProfile='',generation=0,active=0;
 let queue=[],cache=new Map(),items=new WeakMap();
 const observer=typeof IntersectionObserver==='function'?new IntersectionObserver(entries=>{
  for(const entry of entries){
   if(!entry.isIntersecting)continue;
   observer.unobserve(entry.target);
   const item=items.get(entry.target);
   if(item)enqueue(entry.target,item);
  }
 },{rootMargin:'180px'}):null;

 function style(node,count){
  node.className='playlist-cover playlist-cover-'+(node.dataset.variant||'card')+' is-'+(count||'placeholder');
 }
 function placeholder(node){
  node.replaceChildren();node.textContent='♫';style(node,0);
 }
 function paintIds(node,ids,profile){
  const seen=new Set(),valid=[];
  for(const value of ids||[]){
   const id=String(value||'');
   if(!/^\d+$/.test(id)||seen.has(id))continue;
   seen.add(id);valid.push(id);
   if(valid.length===4)break;
  }
  if(!valid.length){placeholder(node);return;}
  node.replaceChildren();style(node,valid.length);
  for(const id of valid){
   const image=document.createElement('img');image.alt='';image.loading='lazy';image.decoding='async';
   image.src=artworkUrl(id,profile);
   image.onerror=()=>{
    if(image.parentNode!==node)return;
    image.remove();
    if(!node.children.length)placeholder(node);
    else style(node,node.children.length);
   };
   node.append(image);
  }
 }
 function pump(){
  while(active<3&&queue.length){
   const task=queue.shift();
   if(task.generation!==generation||!task.node.isConnected)continue;
   active++;
   const cacheKey=task.profile+'\t'+task.item.kind+'\t'+task.item.key;
   let pending=cache.get(cacheKey);
   if(!pending){
    pending=Promise.resolve().then(()=>request('/api/playlists/'+encoded(task.item.kind)+'/'+encoded(task.item.key)+'/cover'))
     .then(result=>Array.isArray(result.track_ids)?result.track_ids:[]);
    cache.set(cacheKey,pending);
   }
   pending.then(ids=>{
    if(task.generation===generation&&task.profile===selectedProfile&&task.node.isConnected)paintIds(task.node,ids,task.profile);
   }).catch(()=>{
    cache.delete(cacheKey);
    if(task.generation===generation&&task.node.isConnected)placeholder(task.node);
   }).finally(()=>{active--;pump();});
  }
 }
 function enqueue(node,item){
  if(!item.can_play){placeholder(node);return;}
  queue.push({node,item,profile:selectedProfile||String(profileId()||''),generation});pump();
 }
 function create(item,variant='card'){
  const node=document.createElement('span');node.dataset.variant=variant;
  node.setAttribute('aria-hidden','true');placeholder(node);
  if(observer){items.set(node,item);observer.observe(node);}
  else{
   const createdGeneration=generation;
   Promise.resolve().then(()=>{
    if(createdGeneration===generation&&node.isConnected)enqueue(node,item);
   });
  }
  return node;
 }
 function paintTracks(node,tracks){
  const ids=(tracks||[]).filter(row=>row&&row.thumb).map(row=>row.id);
  paintIds(node,ids,selectedProfile||String(profileId()||''));
 }
 function reset(nextProfile){
  generation++;selectedProfile=String(nextProfile||'');queue=[];cache.clear();items=new WeakMap();
  if(observer)observer.disconnect();
 }
 return {create,paintTracks,reset,placeholder};
}
