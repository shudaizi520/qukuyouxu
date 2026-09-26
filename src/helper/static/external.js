import {normalizePlaybackTrack} from './playlist-player.js';

(()=>{
'use strict';
const $=id=>document.getElementById(id);
const IMAGE_PAGE_ROWS=80;
const PAGE_SIZE=25;
let sources=[];
let current=null;
let activeStatus='matched';
let page=1;
let pollTimer=null;
let statusSwitching=false;
let sourceRequest=0;

function notify(message,error=false){
 if(window.PCHUI){PCHUI.notify(message,{error});return;}
 const node=$('notice');node.hidden=!message;node.textContent=message||'';node.className='notice'+(error?' error':'');
}
async function request(path,method='GET',payload){return PCHAuth.request(path,method,payload);}
async function json(path,method='GET',payload){const result=await(await request(path,method,payload)).json();if(method==='POST'){const tags=[];if(path.startsWith('/api/external/'))tags.push('external-sources');if(path==='/api/playlists/rename'||/\/publish$|\/remove$/.test(path))tags.push('playlists');if(tags.length)PCHAuth.invalidateCache(tags);}return result;}
async function action(fn){
 try{return await (window.PCHUI?PCHUI.run(fn):fn());}
 catch(error){notify(error.message||'操作失败',true);}
}
function addText(parent,tag,text,className=''){
 const node=document.createElement(tag);if(className)node.className=className;node.textContent=String(text??'');parent.append(node);return node;
}
function providerName(value){return value==='qq'?'QQ 音乐':value==='netease'?'网易云音乐':'本地文件';}
function count(name){return Number(current?.counts?.[name]||0);}
function stateText(source){
 if(source.needs_confirmation)return '来源变更待确认';
 if(source.managed?.order_attention)return '顺序待核对';
 if(source.managed)return '已同步 Plex';
 return '未发布';
}
function selectSourceId(){
 const requested=new URLSearchParams(location.search).get('source');
 if(requested&&sources.some(row=>row.id===requested))return requested;
 if(current&&sources.some(row=>row.id===current.id))return current.id;
 return sources[0]?.id||'';
}
function updatePublishLabel(){
 const matched=count('matched'),title=$('plexPlaylistTitle').value.trim();
 const renamed=!!current?.managed&&title!==String(current.managed.title||'');
 $('publishSource').textContent=(renamed?'重命名并更新':current?.managed?'更新歌单':'创建歌单')+'（'+matched+'首）';
 $('publishSource').disabled=!matched;
}
function renderSourceList(){
 const list=$('sourceSelector');list.replaceChildren();
 for(const source of sources){
  const option=document.createElement('option');option.value=source.id;option.textContent=(source.title||'未命名歌单')+' · '+providerName(source.provider);list.append(option);
 }
 list.value=current?.id||'';
}
function applySourceSummary(result,generation,profileId){
 if(generation!==sourceRequest||profileId!==PCHAuth.profile())return false;
 sources=Array.isArray(result?.items)?result.items:[];
 $('sourceWorkspace').hidden=!sources.length;
 if(!sources.length)current=null;else renderSourceList();
 return true;
}
async function loadSources(preferred=''){
 const generation=++sourceRequest,profileId=PCHAuth.profile();
 const result=await PCHAuth.cachedJson('/api/external/sources',{tag:'external-sources',freshMs:60_000,retainMs:900_000,onUpdate:value=>applySourceSummary(value,generation,profileId)});
 if(!applySourceSummary(result.value,generation,profileId)||!sources.length)return;
 const sourceId=preferred&&sources.some(row=>row.id===preferred)?preferred:selectSourceId();
 await openSource(sourceId,false);
}
async function openSource(sourceId,updateUrl=true){
 if(!current||current.id!==sourceId){page=1;$('detailCard').style.minHeight='';}
 current=await json('/api/external/sources/'+encodeURIComponent(sourceId)+'?status='+encodeURIComponent(activeStatus)+'&page='+page+'&limit='+PAGE_SIZE);
 const lastPage=Math.max(1,Math.ceil(current.total/PAGE_SIZE));
 if(page>lastPage){
  page=lastPage;
  current=await json('/api/external/sources/'+encodeURIComponent(sourceId)+'?status='+encodeURIComponent(activeStatus)+'&page='+page+'&limit='+PAGE_SIZE);
 }
 if(updateUrl){const url=new URL(location.href);url.searchParams.set('source',sourceId);url.searchParams.set('tab',activeStatus);history.replaceState(null,'',url);}
 renderSourceList();renderDetail();
}
async function switchMatchStatus(button){
 const next=button.dataset.matchStatus;if(statusSwitching||!current||next===activeStatus)return;
 const previous=activeStatus,detail=$('detailCard');detail.style.minHeight=Math.max(detail.offsetHeight,parseFloat(detail.style.minHeight)||0)+'px';
 activeStatus=next;page=1;statusSwitching=true;detail.setAttribute('aria-busy','true');
 document.querySelectorAll('.external-count-tab').forEach(tab=>tab.classList.toggle('active',tab.dataset.matchStatus===activeStatus));
 try{await openSource(current.id);}
 catch(error){activeStatus=previous;document.querySelectorAll('.external-count-tab').forEach(tab=>tab.classList.toggle('active',tab.dataset.matchStatus===activeStatus));notify(error.message||'切换失败',true);}
 finally{statusSwitching=false;detail.removeAttribute('aria-busy');}
}
function renderDetail(){
 $('detailCard').dataset.status=activeStatus;
 $('sourceState').textContent=stateText(current);
 $('matchedCount').textContent=String(count('matched'));
 $('reviewCount').textContent=String(count('review'));
 $('missingCount').textContent=String(count('missing'));
 $('plexPlaylistTitle').value=current.managed?.title||current.title||'';
 $('plexPlaylistTitle').disabled=false;
 $('followUpdates').checked=!!current.follow_updates;
 $('followUpdatesRow').hidden=!['qq','netease'].includes(current.provider);
 $('followUpdates').disabled=!['qq','netease'].includes(current.provider);
 $('refreshSource').textContent=current.needs_confirmation?'确认继续刷新':'重新读取';
 updatePublishLabel();
 $('replenishmentCard').hidden=!count('missing');
 const exportBase='/api/external/sources/'+encodeURIComponent(current.id)+'/export?format=';
 $('downloadText').dataset.url=exportBase+'text';$('downloadCsv').dataset.url=exportBase+'csv';
 document.querySelectorAll('.external-count-tab').forEach(button=>button.classList.toggle('active',button.dataset.matchStatus===activeStatus));
 $('reviewToolbar').hidden=activeStatus!=='review';
 $('trackColumnHead').hidden=activeStatus==='missing';
 $('trackReviewHeading').hidden=activeStatus!=='review';
 renderTracks();
}
function playImportedTrack(track){
 if(window.parent===window){notify('请从“我的歌单”中打开导入歌单后播放',true);return;}
 const profileId=String(PCHAuth.profile()||'');
 const sourceId=String(current?.id||'');
 const queue=(current?.tracks||[]).filter(item=>/^\d+$/.test(String(item.plex_track_id||''))).map(item=>normalizePlaybackTrack(item,profileId));
 const index=queue.findIndex(item=>item.source_track_key===String(track.source_track_key||''));
 if(!profileId||!sourceId||index<0){notify('这首歌暂时无法播放',true);return;}
 window.parent.postMessage({type:'pch-play-imported-queue',source_id:sourceId,profile_id:profileId,tracks:queue,index},location.origin);
}
function renderTracks(){
 const list=$('trackList');list.replaceChildren();
 for(const [index,track] of (current.tracks||[]).entries()){
  const row=document.createElement('article');row.className='external-track-row';row.dataset.trackKey=track.source_track_key;
  if(activeStatus==='missing')row.classList.add('external-track-missing');
  if(activeStatus==='matched'&&/^\d+$/.test(String(track.plex_track_id||''))){
   row.classList.add('external-track-playable');row.tabIndex=0;row.setAttribute('role','button');row.setAttribute('aria-label','播放 '+(track.title||'无歌名'));
   row.onclick=()=>playImportedTrack(track);
   row.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();playImportedTrack(track);}};
  }
  const position=document.createElement('span');position.className='external-track-number';
  if(activeStatus==='review'){
   const check=document.createElement('input');check.type='checkbox';check.className='external-review-check';check.setAttribute('aria-label','选择 '+(track.title||'无歌名'));check.onchange=updateReviewSelection;position.append(check);
  }else position.textContent=String((current.page-1)*current.limit+index+1);
  const identity=document.createElement('div');identity.className='external-track-identity';
  addText(identity,'strong',track.title||'无歌名');
  const artists=(track.artists||[]).filter(Boolean).join(' / ');
  if(activeStatus==='missing'){
   if(artists)addText(identity,'span',artists);
   row.append(position,identity);
  }else{
   row.append(position,identity);
   addText(row,'span',artists,'external-track-artist');
   addText(row,'span',track.album||'','external-track-album');
   addText(row,'span',track.duration_ms?audioTime(track.duration_ms/1000):'','external-track-duration');
   if(activeStatus==='review'){
    const actions=document.createElement('div');actions.className='external-track-actions';
    renderReviewActions(actions,track,position.querySelector('input'));row.append(actions);
   }
  }
  list.append(row);
 }
 if(!(current.tracks||[]).length)addText(list,'p',activeStatus==='matched'?'没有可靠匹配的歌曲':activeStatus==='review'?'没有需要确认的歌曲':'没有缺失歌曲','empty');
 if(activeStatus==='review')updateReviewSelection();
 const start=current.total?(current.page-1)*current.limit+1:0,end=Math.min(current.total,current.page*current.limit);
 $('trackRange').textContent=current.total?start+'–'+end+' / '+current.total:'0 首';
 $('previousTracks').disabled=current.page<=1;$('nextTracks').disabled=end>=current.total;
}
function reviewChoice(value){return value==='__missing__'?{status:'missing'}:{status:'matched',plex_track_id:value};}
function reviewCandidates(track){return (track.candidates||[]).length?track.candidates:(track.candidate_ids||[]).map(id=>({id,title:'Plex 曲目 '+id,artist:''}));}
function updateReviewSelection(){
 const checks=[...document.querySelectorAll('#trackList .external-review-check')];
 const selected=checks.filter(check=>check.checked);
 const pending=selected.filter(check=>!check.closest('.external-track-row').querySelector('.external-candidate-select').value).length;
 $('reviewSelectedCount').textContent='已选 '+selected.length+' 首'+(pending?' · '+pending+' 首待选候选':'');
 $('confirmSelected').disabled=!selected.length||!!pending;
 $('reviewSelectAll').disabled=!checks.length;
 $('reviewSelectAll').checked=!!checks.length&&selected.length===checks.length;
 $('reviewSelectAll').indeterminate=!!selected.length&&selected.length<checks.length;
}
function renderReviewActions(actions,track,check){
 const candidates=reviewCandidates(track);
 const select=document.createElement('select');select.className='external-candidate-select';select.setAttribute('aria-label','为 '+(track.title||'无歌名')+' 选择匹配歌曲');
 const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='选择候选歌曲';select.append(placeholder);
 for(const candidate of candidates){
  const option=document.createElement('option');option.value=String(candidate.id);option.textContent=[candidate.title,candidate.artist,candidate.album].filter(Boolean).join(' · ');select.append(option);
 }
 const missing=document.createElement('option');missing.value='__missing__';missing.textContent='标记为缺失';select.append(missing);
 select.onchange=()=>{if(select.value)check.checked=true;updateReviewSelection();};
 actions.append(select);
}
function audioTime(value){const seconds=Math.max(0,Math.floor(Number(value)||0));return Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0');}
function bytesToBase64(buffer){
 const bytes=new Uint8Array(buffer);let binary='';const block=0x8000;
 for(let offset=0;offset<bytes.length;offset+=block)binary+=String.fromCharCode(...bytes.subarray(offset,offset+block));
 return btoa(binary);
}
async function beginImport(){
 const url=$('sourceUrl').value.trim(),file=$('sourceFile').files[0];
 if(Boolean(url)===Boolean(file))throw Error('请粘贴一个公开歌单链接，或选择一个歌单文件');
 const previousIds=new Set(sources.map(row=>row.id));
 let payload;
 if(url)payload={url};
 else{
  if(file.size>1500*1024)throw Error('歌单文件过大，请控制在 1.5MB 以内');
  payload={filename:file.name,content_base64:bytesToBase64(await file.arrayBuffer())};
 }
 await json('/api/external/import','POST',payload);notify('正在读取并与 Plex 曲库匹配，可以留在本页等待。');await pollJob('导入完成');
 $('sourceUrl').value='';$('sourceFile').value='';$('selectedFile').textContent='';$('selectedFile').hidden=true;
 const added=sources.find(row=>!previousIds.has(row.id));if(added)await openSource(added.id);
}
async function pollJob(successMessage){
 clearTimeout(pollTimer);
 for(let attempts=0;attempts<900;attempts++){
  await new Promise(resolve=>{pollTimer=setTimeout(resolve,1000);});
  const status=await json('/api/status');
  if(!status.job?.running){if(status.job?.error)throw Error(status.job.error);await loadSources();notify(successMessage);if(['Plex 歌单已更新','已移除'].includes(successMessage)&&window.parent!==window)window.parent.postMessage({type:'pch-playlists-changed'},location.origin);return;}
 }
 throw Error('任务仍在后台运行，请稍后刷新页面查看');
}
async function downloadExport(format){
 const response=await request('/api/external/sources/'+encodeURIComponent(current.id)+'/export?format='+format);
 const blob=await response.blob(),url=URL.createObjectURL(blob),anchor=document.createElement('a');
 anchor.href=url;anchor.download=(current.title||'外部歌单')+'-缺失歌曲.'+(format==='csv'?'csv':'txt');document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),2000);
}
async function missingText(){return (await request('/api/external/sources/'+encodeURIComponent(current.id)+'/export?format=text')).text();}
async function copyText(value){
 if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(value);return;}
 const area=document.createElement('textarea');area.value=value;area.setAttribute('readonly','');area.style.position='fixed';area.style.opacity='0';document.body.append(area);area.select();const copied=document.execCommand('copy');area.remove();if(!copied)throw Error('浏览器不允许复制，请下载 TXT');
}
async function allMissingTracks(){
 const rows=[];let currentPage=1;
 while(true){
  const result=await json('/api/external/sources/'+encodeURIComponent(current.id)+'?status=missing&page='+currentPage+'&limit=200');rows.push(...(result.tracks||[]));
  if(rows.length>=result.total)break;currentPage+=1;
 }
 return rows;
}
function canvasPage(rows,index,total){
 const width=1400,rowHeight=54,top=150,height=top+rows.length*rowHeight+70,canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;
 const context=canvas.getContext('2d');context.fillStyle='#f5f8f7';context.fillRect(0,0,width,height);context.fillStyle='#17323a';context.font='700 38px sans-serif';context.fillText(current.title||'缺失歌曲',60,65);context.font='24px sans-serif';context.fillStyle='#58716f';context.fillText('补歌清单 · 第 '+(index+1)+' / '+total+' 页',60,108);
 rows.forEach((track,rowIndex)=>{const y=top+rowIndex*rowHeight;context.fillStyle=rowIndex%2?'#ffffff':'#edf7f5';context.fillRect(45,y-36,width-90,48);context.fillStyle='#17323a';context.font='22px sans-serif';context.fillText((index*IMAGE_PAGE_ROWS+rowIndex+1)+'. '+String(track.title||'').slice(0,38),65,y);context.fillStyle='#58716f';context.font='19px sans-serif';context.fillText((track.artists||[]).join(' / ').slice(0,42),760,y);});
 return canvas;
}
async function downloadLongImages(){
 const rows=await allMissingTracks();if(!rows.length)throw Error('目前没有缺失歌曲');const total=Math.ceil(rows.length/IMAGE_PAGE_ROWS);
 for(let index=0;index<total;index++){
  const canvas=canvasPage(rows.slice(index*IMAGE_PAGE_ROWS,(index+1)*IMAGE_PAGE_ROWS),index,total);
  await new Promise((resolve,reject)=>canvas.toBlob(blob=>{if(!blob){reject(Error('无法生成图片'));return;}const url=URL.createObjectURL(blob),anchor=document.createElement('a');anchor.href=url;anchor.download=(current.title||'补歌清单')+'-'+(index+1)+'.png';document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),2000);resolve();},'image/png'));
 }
}

$('importForm').addEventListener('submit',event=>{event.preventDefault();action(beginImport);});
$('sourceFile').onchange=()=>{$('sourceUrl').value='';$('selectedFile').textContent=$('sourceFile').files[0]?.name||'';$('selectedFile').hidden=!$('sourceFile').files[0];};
$('sourceUrl').oninput=()=>{if($('sourceUrl').value){$('sourceFile').value='';$('selectedFile').textContent='';$('selectedFile').hidden=true;}};
$('sourceSelector').onchange=()=>action(()=>openSource($('sourceSelector').value));
$('plexPlaylistTitle').oninput=updatePublishLabel;
$('refreshSource').onclick=()=>action(async()=>{const force=!!current.needs_confirmation;if(force&&!await PCHUI.confirm('来源歌曲比上次少很多。确认用最新公开歌单覆盖上次读取结果？Plex 歌单仍会先经过归属校验。'))return;await json('/api/external/sources/'+encodeURIComponent(current.id)+'/refresh','POST',{confirm_large_removal:force});notify('正在重新读取来源');await pollJob('刷新完成');});
$('publishSource').onclick=()=>action(async()=>{const title=$('plexPlaylistTitle').value.trim(),matched=count('matched'),renamed=!!current.managed&&title!==String(current.managed.title||'');if(!title||title.length>80)throw Error('请填写 1～80 个字符的 Plex 歌单名称');if(!await PCHUI.confirm('确认'+(renamed?'重命名并':'')+'更新“'+title+'”？\n只包含 '+matched+' 首可靠匹配的本地歌曲。'))return;if(renamed){await json('/api/playlists/rename','POST',{kind:'external',key:current.id,title,confirm:true});current.managed.title=title;}await json('/api/external/sources/'+encodeURIComponent(current.id)+'/publish','POST',{confirm:true,title,revision:current.revision});notify('正在写入经过验证的 Plex 歌单');await pollJob('Plex 歌单已更新');});
$('followUpdates').onchange=()=>action(async()=>{const enabled=$('followUpdates').checked;try{await json('/api/external/sources/'+encodeURIComponent(current.id)+'/settings','POST',{follow_updates:enabled});current.follow_updates=enabled;notify(enabled?'已开启自动刷新':'已关闭自动刷新');}catch(error){$('followUpdates').checked=!enabled;throw error;}});
$('removeSource').onclick=()=>action(async()=>{const title=current.managed?.title||current.title;if(!await PCHUI.confirm('确认移除“'+title+'”？\n如果它由本软件创建，会在归属与内容校验通过后删除对应 Plex 歌单；其他歌单不会改动。',{confirmText:'确认移除'}))return;await json('/api/external/sources/'+encodeURIComponent(current.id)+'/remove','POST',{confirm:true,title});notify('正在安全移除');current=null;await pollJob('已移除');});
document.querySelectorAll('.external-count-tab').forEach(button=>button.onclick=()=>switchMatchStatus(button));
$('reviewSelectAll').onchange=()=>{document.querySelectorAll('#trackList .external-review-check').forEach(check=>{check.checked=$('reviewSelectAll').checked;});updateReviewSelection();};
$('confirmSelected').onclick=()=>action(async()=>{
 const choices=[...document.querySelectorAll('#trackList .external-review-check:checked')].map(check=>{
  const row=check.closest('.external-track-row');return {track_key:row.dataset.trackKey,choice:reviewChoice(row.querySelector('.external-candidate-select').value)};
 });
 if(!choices.length)throw Error('请先为歌曲选择候选并勾选');
 if(!await PCHUI.confirm('确认所选 '+choices.length+' 首歌曲的匹配结果？'))return;
 await json('/api/external/sources/'+encodeURIComponent(current.id)+'/confirm-batch','POST',{choices});notify('已确认 '+choices.length+' 首');await openSource(current.id,false);
});
$('previousTracks').onclick=()=>action(async()=>{page=Math.max(1,page-1);await openSource(current.id);});
$('nextTracks').onclick=()=>action(async()=>{page+=1;await openSource(current.id);});
$('copyMissing').onclick=()=>action(async()=>{await copyText(await missingText());notify('补歌清单已复制，请粘贴到官方音乐客户端中使用。');});
$('downloadImage').onclick=()=>{closeDownloadMenu();action(async()=>{await downloadLongImages();notify('补歌清单图片已生成');});};
$('downloadText').onclick=event=>{event.preventDefault();closeDownloadMenu();action(()=>downloadExport('text'));};
$('downloadCsv').onclick=event=>{event.preventDefault();closeDownloadMenu();action(()=>downloadExport('csv'));};
function closeDownloadMenu(){$('downloadMenu').open=false;}
document.addEventListener('pointerdown',event=>{const menu=$('downloadMenu');if(menu.open&&!menu.contains(event.target))closeDownloadMenu();});
document.addEventListener('keydown',event=>{if(event.key==='Escape')closeDownloadMenu();});
window.addEventListener('pch-profile-change',()=>action(async()=>{current=null;page=1;await loadSources();}));
async function boot(){
 const requested=new URLSearchParams(location.search).get('tab');if(['matched','review','missing'].includes(requested))activeStatus=requested;
 await loadSources();
}
window.addEventListener('pch-auth-ready',event=>{if(event.detail?.authenticated)action(boot);});
window.addEventListener('pch-auth-login',()=>action(boot));
})();
