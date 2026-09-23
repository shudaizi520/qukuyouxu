const encoded=value=>encodeURIComponent(String(value||''));
const MAX_ACTIVE_IMAGES=6;

export function artworkUrl(trackId,profileId){
 return '/api/playlists/library/tracks/'+encoded(trackId)+'/artwork?profile_id='+encoded(profileId);
}

export function createPlaylistArtwork({document,request,profileId,cacheUser=()=>'',cacheScope=()=>''}){
 let selectedProfile='',generation=0,active=0;
 let queue=[],cache=new Map(),items=new WeakMap();
 let imageQueue=[],activeImages=0,drainingImages=false;
 const loadingImages=new Set();
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
 function pumpImages(){
  if(drainingImages)return;
  drainingImages=true;
  try{
   for(const task of [...loadingImages]){
    if(task.image.isConnected)continue;
    task.finish();task.image.removeAttribute('src');
   }
  while(activeImages<MAX_ACTIVE_IMAGES&&imageQueue.length){
   const task=imageQueue.shift();
   if(task.generation!==generation||!task.image.isConnected)continue;
   activeImages++;loadingImages.add(task);
   task.finish=()=>{
    if(task.done)return;
    task.done=true;task.image.onload=null;task.image.onerror=null;
    clearTimeout(task.timer);
    loadingImages.delete(task);activeImages--;pumpImages();
   };
   task.image.onload=task.finish;
   const failed=()=>{
    const node=task.image.parentNode;
    task.image.remove();
    if(node){delete node.dataset.coverIds;if(!node.children.length)placeholder(node);else style(node,node.children.length);}
    task.finish();
   };
   task.image.onerror=failed;
   task.timer=setTimeout(failed,12000);
   task.image.src=task.url;
  }
  }finally{drainingImages=false;}
 }
 function cancelNodeImages(node){
  imageQueue=imageQueue.filter(task=>task.image.parentNode!==node);
  for(const task of [...loadingImages]){
   if(task.image.parentNode!==node)continue;
   task.finish();task.image.removeAttribute('src');
  }
 }
 function placeholder(node){
  cancelNodeImages(node);
  delete node.dataset.coverIds;delete node.dataset.coverProfile;
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
  const signature=valid.join(',');
  const loaded=valid.every((id,index)=>node.children[index]?.getAttribute('src')===artworkUrl(id,profile));
  if(node.dataset.coverIds===signature&&node.dataset.coverProfile===profile&&node.children.length===valid.length&&loaded)return;
  cancelNodeImages(node);node.replaceChildren();style(node,valid.length);
  node.dataset.coverIds=signature;node.dataset.coverProfile=profile;
  for(const id of valid){
   const image=document.createElement('img');image.alt='';image.loading='eager';image.decoding='async';
   node.append(image);
   imageQueue.push({image,url:artworkUrl(id,profile),generation,done:false,finish:null});
  }
  pumpImages();
 }
 function savedKey(profile,item){
  const scope=String(cacheScope(profile)||'');
  if(!scope)return '';
  return 'pch-cover:v2:'+encoded(cacheUser())+':'+encoded(profile)+':'+encoded(scope)+':'+encoded(item.kind)+':'+encoded(item.key);
 }
 function savedIds(profile,item){
  try{
   const key=savedKey(profile,item);if(!key)return null;
   const record=JSON.parse(sessionStorage.getItem(key)||'null');
   return record&&Date.now()-record.saved<3600000&&Array.isArray(record.ids)?record.ids:null;
  }catch{return null;}
 }
 function saveIds(profile,item,ids){
  try{const key=savedKey(profile,item);if(key)sessionStorage.setItem(key,JSON.stringify({saved:Date.now(),ids}));}catch{}
 }
 globalThis.addEventListener?.('pch-auth-logout',()=>{
  try{for(let i=sessionStorage.length-1;i>=0;i--){const key=sessionStorage.key(i);if(key?.startsWith('pch-cover:v2:'))sessionStorage.removeItem(key);}}catch{}
 });
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
    if(task.generation===generation)saveIds(task.profile,task.item,ids);
    if(task.generation===generation&&task.profile===selectedProfile&&task.node.isConnected)paintIds(task.node,ids,task.profile);
   }).catch(()=>{
    cache.delete(cacheKey);
    if(task.generation===generation&&task.node.isConnected)placeholder(task.node);
   }).finally(()=>{active--;pump();});
  }
 }
 function enqueue(node,item){
  if(!item.can_play){placeholder(node);return;}
  const profile=selectedProfile||String(profileId()||'');
  const ids=savedIds(profile,item);
  if(ids)paintIds(node,ids,profile);
  queue.push({node,item,profile,generation});pump();
 }
 function create(item,variant='card',existingNode=null){
  const node=existingNode||document.createElement('span');node.dataset.variant=variant;
  node.setAttribute('aria-hidden','true');
  if(!existingNode)placeholder(node);
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
  generation++;selectedProfile=String(nextProfile||'');queue=[];imageQueue=[];cache.clear();items=new WeakMap();
  const interrupted=new Set();
  for(const task of [...loadingImages]){
   const node=task.image.parentNode;task.finish();task.image.removeAttribute('src');task.image.remove();
   if(node)interrupted.add(node);
  }
  for(const node of interrupted){delete node.dataset.coverIds;delete node.dataset.coverProfile;if(!node.children.length)placeholder(node);else style(node,node.children.length);}
  if(observer)observer.disconnect();
 }
 return {create,paintTracks,reset,placeholder};
}
