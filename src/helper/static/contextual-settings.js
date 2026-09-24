(()=>{
 'use strict';
 const $=id=>document.getElementById(id);
 const smart=document.body.dataset.view==='mixes';
 const library=document.body.dataset.view==='library';
 if(!smart&&!library)return;
 let automation=null,profileControls=null,dailyStatus=null,busy=false,pollTimer=0;
 async function api(path,method='GET',body){
  const response=await PCHAuth.request(path,method,body);
  const data=await response.json();
  if(!response.ok)throw Error(data.error||'操作失败');
  return data;
 }
 async function action(fn){
  if(busy)return;busy=true;
  try{await PCHUI.run(fn);}catch(error){PCHUI.notify(error.message||'操作失败',{error:true});}
  finally{busy=false;}
 }
 function hours(id){
  const select=$(id);if(!select)return;
  for(let hour=0;hour<24;hour++){const option=document.createElement('option');option.value=String(hour);option.textContent=String(hour).padStart(2,'0')+':00';select.append(option);}
 }
 function renderAutomation(){
  if(!automation)return;
  if(smart){
   $('dailyAutomationHour').value=String(automation.daily.hour);
   $('dailyAutomationEnabled').checked=profileControls?.daily!==false;
   $('smartAutomationHour').value=String(automation.smart.hour);
   $('smartIntervalDays').value=String(automation.smart.interval_days);
   $('smartAutomationEnabled').checked=profileControls?.smart!==false;
  }
  if(library){$('libraryAutomationHour').value=String(automation.library.hour);$('libraryAutomationEnabled').checked=!!automation.library.enabled;}
 }
 async function saveAutomation(){
  const next=await api('/api/automation');
  if(smart){
   next.daily={...next.daily,hour:Number($('dailyAutomationHour').value)};
   next.smart={...next.smart,hour:Number($('smartAutomationHour').value),interval_days:Number($('smartIntervalDays').value)};
  }
  if(library)next.library={enabled:$('libraryAutomationEnabled').checked,hour:Number($('libraryAutomationHour').value)};
  try{automation=await api('/api/automation','POST',next);}
  finally{automation=await api('/api/automation');renderAutomation();}
 }
 async function saveControl(key,input){
  const next=input.checked;input.disabled=true;
  try{const result=await api('/api/plex/profiles/control','POST',{profile_id:PCHAuth.profile(),key,enabled:next});profileControls=result.controls;}
  catch(error){input.checked=!next;PCHUI.notify(error.message||'保存失败',{error:true});}
  finally{input.disabled=false;}
 }
 function renderDaily(){
  const status=dailyStatus||{},plan=status.daily_plan||{},managed=status.daily_managed||{};
  const ready=!!(plan.id&&!plan.applied),blocked=Array.isArray(plan.blocked)?plan.blocked.length:!!plan.blocked;
  const preview=$('dailyPreview'),previewTracks=$('dailyPreviewTracks');
  preview.hidden=!ready||!Array.isArray(plan.items)||!plan.items.length;
  previewTracks.replaceChildren();
  if(!preview.hidden)for(const row of plan.items.slice(0,10)){
   const item=document.createElement('li'),title=document.createElement('strong'),artist=document.createElement('small');
   title.textContent=String(row?.title||'未命名歌曲');artist.textContent=String(row?.artist||'未知歌手');
   item.append(title,artist);previewTracks.append(item);
  }
  $('dailyContextState').textContent=status.job?.running?'后台处理中':status.job?.error?'需要处理':ready?'待发布':managed.title?'已发布':'未建立';
  $('dailyGenerate').disabled=!!status.job?.running;
  $('dailyPublish').hidden=!ready;$('dailyPublish').disabled=!!status.job?.running||blocked||!plan.items?.length;
  $('dailyRemove').hidden=!managed.title;$('dailyRemove').disabled=!!status.job?.running;
 }
 async function refreshDaily(){dailyStatus=await api('/api/status');renderDaily();}
 async function renderSharedLibrary(){
  const state=await api('/api/library-share/status');
  document.body.dataset.libraryShared=state.recipient?'true':'false';
  if(!state.recipient)return false;
  $('librarySharePanel').hidden=false;
  const rows=Array.isArray(state.items)?state.items:[];
  const linked=rows.filter(row=>row.status==='已同步').length;
  const result=state.last_result||{},attention=(result.skipped||0)+(result.errors?.length||0);
  $('libraryShareMessage').textContent=rows.length?`${linked} / ${rows.length} 已同步${attention?' · '+attention+' 张待核对':''}`:'暂无分类歌单';
  const box=$('libraryShareRows');box.replaceChildren();
  for(const row of rows){
   const item=document.createElement('div');item.className='library-share-row';
   const label=document.createElement('strong');label.textContent=String(row.title||'分类歌单');
   const status=document.createElement('span');status.className='muted';status.textContent=String(row.status||'等待同步');
   item.append(label,status);
   if(row.status==='已退出'){
    const restore=document.createElement('button');restore.type='button';restore.className='secondary';restore.textContent='恢复同步';
    restore.onclick=()=>action(async()=>{await api('/api/library-share/restore','POST',{category_id:row.id});await renderSharedLibrary();PCHUI.notify('已恢复，下次检查时会重新同步这张歌单。');});
    item.append(restore);
   }
   box.append(item);
  }
  return true;
 }
 async function watchDaily(){
  clearTimeout(pollTimer);
  if(!smart||!PCHAuth.status().authenticated)return;
  try{await refreshDaily();}catch(error){PCHUI.notify(error.message,{error:true});}
  pollTimer=setTimeout(watchDaily,dailyStatus?.job?.running?2500:30000);
 }
 async function boot(){
  try{
   if(library&&await renderSharedLibrary())return;
   automation=await api('/api/automation');renderAutomation();
   if(smart){
    const result=await api('/api/plex/profiles/controls?profile_id='+encodeURIComponent(PCHAuth.profile()));profileControls=result.controls;renderAutomation();
    const policy=await api('/api/daily/policy');
    $('dailySize').value=policy.size??50;$('rediscoveryDays').value=policy.rediscovery_days??90;
    $('artistCap').value=policy.artist_cap??2;$('favoritePercent').value=policy.favorite_percent??20;
    await watchDaily();
   }
  }catch(error){PCHUI.notify(error.message||'设置加载失败',{error:true});}
 }
 if(smart){
  hours('dailyAutomationHour');hours('smartAutomationHour');
  $('dailyPolicyForm').onsubmit=event=>{event.preventDefault();action(async()=>{
   await api('/api/daily/policy','POST',{size:Number($('dailySize').value),rediscovery_days:Number($('rediscoveryDays').value),artist_cap:Number($('artistCap').value),favorite_percent:Number($('favoritePercent').value)});
   PCHUI.notify('每日推荐规则已保存；下次生成时生效。');
  });};
  $('dailyGenerate').onclick=()=>action(async()=>{await api('/api/jobs/daily_preview','POST',{});PCHUI.notify('正在生成每日推荐');await watchDaily();});
  $('dailyPublish').onclick=()=>action(async()=>{
   const plan=dailyStatus?.daily_plan;if(!plan?.id)return;
   if(!await PCHUI.confirm('发布本次每日推荐？',{confirmText:'确认发布'}))return;
   await api('/api/jobs/daily_apply','POST',{confirm:true,plan_id:plan.id});PCHUI.notify('正在发布每日推荐');await watchDaily();
  });
  $('dailyRemove').onclick=()=>action(async()=>{
   const title=dailyStatus?.daily_managed?.title;if(!title)return;
   if(!await PCHUI.confirm('删除 Plex 歌单“'+title+'”？不会删除音乐文件。',{confirmText:'删除歌单'}))return;
   await api('/api/playlists/remove','POST',{kind:'daily',key:'daily',title,confirm:true});PCHUI.notify('每日推荐歌单已删除');await watchDaily();
  });
  for(const id of ['dailyAutomationHour','smartAutomationHour','smartIntervalDays'])$(id).onchange=()=>action(saveAutomation);
  $('dailyAutomationEnabled').onchange=()=>saveControl('daily',$('dailyAutomationEnabled'));
  $('smartAutomationEnabled').onchange=()=>saveControl('smart',$('smartAutomationEnabled'));
 }else{
  hours('libraryAutomationHour');
  for(const id of ['libraryAutomationHour','libraryAutomationEnabled'])$(id).onchange=()=>action(saveAutomation);
 }
 window.addEventListener('pch-auth-ready',boot);window.addEventListener('pch-auth-login',boot);
 window.addEventListener('pch-auth-logout',()=>{clearTimeout(pollTimer);pollTimer=0;});
 window.addEventListener('pagehide',()=>clearTimeout(pollTimer));
})();
