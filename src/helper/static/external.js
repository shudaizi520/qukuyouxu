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
let previewButton=null;
let previewProgress=null;
let previewKey='';

function notify(message,error=false){
 if(window.PCHUI){PCHUI.notify(message,{error});return;}
 const node=$('notice');node.hidden=!message;node.textContent=message||'';node.className='notice'+(error?' error':'');
}
async function request(path,method='GET',payload){return PCHAuth.request(path,method,payload);}
async function json(path,method='GET',payload){return (await request(path,method,payload)).json();}
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
 if(source.needs_confirmation)return '来源歌曲减少较多，需要确认后才能覆盖原结果';
 if(source.managed?.order_attention)return 'Plex 已包含正确歌曲，但顺序需要人工查看';
 if(source.managed)return '已在 Plex 托管，曲库整理时可继续维护';
 if(source.last_run?.status==='completed')return source.last_run.message||'分析完成';
 return '已读取，尚未在 Plex 创建歌单';
}
function selectSourceId(){
 const requested=new URLSearchParams(location.search).get('source');
 if(requested&&sources.some(row=>row.id===requested))return requested;
 if(current&&sources.some(row=>row.id===current.id))return current.id;
 return sources[0]?.id||'';
}
function renderSourceList(){
 const list=$('sourceSelector');list.replaceChildren();
 for(const source of sources){
  const option=document.createElement('option');option.value=source.id;option.textContent=(source.title||'未命名歌单')+' · '+providerName(source.provider);list.append(option);
 }
 list.value=current?.id||'';
}
async function loadSources(preferred=''){
 const result=await json('/api/external/sources');sources=Array.isArray(result.items)?result.items:[];
 $('sourceWorkspace').hidden=!sources.length;
 if(!sources.length){current=null;return;}
 const sourceId=preferred&&sources.some(row=>row.id===preferred)?preferred:selectSourceId();
 await openSource(sourceId,false);
}
async function openSource(sourceId,updateUrl=true){
 if(!current||current.id!==sourceId)page=1;
 if(current)stopAudition();
 current=await json('/api/external/sources/'+encodeURIComponent(sourceId)+'?status='+encodeURIComponent(activeStatus)+'&page='+page+'&limit='+PAGE_SIZE);
 if(updateUrl){const url=new URL(location.href);url.searchParams.set('source',sourceId);url.searchParams.set('tab',activeStatus);history.replaceState(null,'',url);}
 renderSourceList();renderDetail();
}
function renderDetail(){
 $('sourceProvider').textContent=providerName(current.provider);
 $('sourceTitle').textContent=current.title||'未命名歌单';
 $('sourceState').textContent=stateText(current);
 $('matchedCount').textContent=String(count('matched'));
 $('reviewCount').textContent=String(count('review'));
 $('missingCount').textContent=String(count('missing'));
 $('plexPlaylistTitle').value=current.managed?.title||current.title||'';
 $('plexPlaylistTitle').disabled=!!current.managed;
 $('followUpdates').checked=!!current.follow_updates;
 $('followUpdatesRow').hidden=!['qq','netease'].includes(current.provider);
 $('followUpdates').disabled=!['qq','netease'].includes(current.provider);
 $('refreshSource').textContent=current.needs_confirmation?'确认继续刷新':'重新读取';
 const matched=count('matched');
 $('publishSource').textContent=(current.managed?'更新歌单':'创建歌单')+'（'+matched+'首）';
 $('publishSource').disabled=!matched;
 $('replenishmentCard').hidden=!count('missing');
 $('missingSummary').textContent=count('missing')+' 首未找到';
 const exportBase='/api/external/sources/'+encodeURIComponent(current.id)+'/export?format=';
 $('downloadText').dataset.url=exportBase+'text';$('downloadCsv').dataset.url=exportBase+'csv';
 document.querySelectorAll('.external-count-tab').forEach(button=>button.classList.toggle('active',button.dataset.matchStatus===activeStatus));
 renderTracks();
}
function renderTracks(){
 const list=$('trackList');list.replaceChildren();
 for(const track of current.tracks||[]){
  const row=document.createElement('article');row.className='external-track-row';
  const identity=document.createElement('div');identity.className='external-track-identity';
  addText(identity,'strong',track.title||'无歌名');addText(identity,'span',(track.artists||[]).join(' / ')||'未知歌手');row.append(identity);
  const status=addText(row,'span',activeStatus==='matched'?'已在曲库':activeStatus==='review'?'需要你确认':'曲库未找到','bucket'+(activeStatus==='review'?' warn':''));
  status.setAttribute('aria-label','匹配状态');
  const actions=document.createElement('div');actions.className='song-actions';
  if(activeStatus==='matched')actions.append(auditionButton(track));
  if(activeStatus==='review')renderReviewActions(actions,track);
  if(activeStatus==='missing')renderSearchActions(actions,track);
  row.append(actions);list.append(row);
 }
 if(!(current.tracks||[]).length)addText(list,'p',activeStatus==='matched'?'没有可靠匹配的歌曲':activeStatus==='review'?'没有需要确认的歌曲':'没有缺失歌曲','empty');
 const start=current.total?(current.page-1)*current.limit+1:0,end=Math.min(current.total,current.page*current.limit);
 $('trackRange').textContent=current.total?start+'–'+end+' / '+current.total:'0 首';
 $('previousTracks').disabled=current.page<=1;$('nextTracks').disabled=end>=current.total;
}
function renderReviewActions(actions,track){
 const candidates=(track.candidates||[]).length?track.candidates:(track.candidate_ids||[]).map(id=>({id,title:'Plex 曲目 '+id,artist:''}));
 for(const candidate of candidates){
 const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent='选 '+[candidate.title,candidate.artist,candidate.album].filter(Boolean).join(' · ');
  button.onclick=()=>action(async()=>{await json('/api/external/sources/'+encodeURIComponent(current.id)+'/confirm','POST',{track_key:track.source_track_key,choice:{status:'matched',plex_track_id:String(candidate.id)}});notify('已确认匹配');await openSource(current.id,false);});
  actions.append(auditionButton(track,candidate.id,'试听候选'),button);
 }
 const missing=document.createElement('button');missing.type='button';missing.className='secondary';missing.textContent='标为缺失';
 missing.onclick=()=>action(async()=>{await json('/api/external/sources/'+encodeURIComponent(current.id)+'/confirm','POST',{track_key:track.source_track_key,choice:{status:'missing'}});notify('已放入缺失清单');await openSource(current.id,false);});actions.append(missing);
}
function auditionButton(track,candidate='',label='试听'){
 const control=document.createElement('span');control.className='external-preview-control';
 const button=document.createElement('button');button.type='button';button.className='secondary external-preview-button';button.textContent=label;
 const progress=document.createElement('span');progress.className='external-preview-progress';progress.textContent='0:00';
 button.onclick=()=>playTrack(track,candidate,button,progress);control.append(button,progress);return control;
}
function stopAudition(){
 const player=$('auditionPlayer');player.pause();player.removeAttribute('src');player.load();if(previewButton)previewButton.textContent='试听';if(previewProgress){previewProgress.textContent='0:00';previewProgress.classList.remove('is-error');}previewButton=null;previewProgress=null;previewKey='';
}
function showPreviewError(){if(previewButton)previewButton.textContent='重试';if(previewProgress){previewProgress.textContent='无法播放';previewProgress.classList.add('is-error');}}
function playTrack(track,candidate='',button,progress){
  const key=String(track.source_track_key||'')+'\0'+String(candidate||''),player=$('auditionPlayer');
  const params=new URLSearchParams();const profile=PCHAuth.profile();if(profile)params.set('profile_id',profile);if(candidate)params.set('candidate',String(candidate));
 const source='/api/external/sources/'+encodeURIComponent(current.id)+'/tracks/'+encodeURIComponent(track.source_track_key)+'/audio?'+params.toString();
 if(window.parent!==window){
  window.parent.postMessage({type:'pch-player-preview',track:{id:key,title:track.title||'未知歌曲',artist:(track.artists||[]).join(' / ')||'未知歌手',source,profileId:profile}},location.origin);
  button.textContent='底部播放';progress.textContent='';return;
 }
 if(previewKey===key&&player.src){if(player.paused)player.play().catch(showPreviewError);else player.pause();return;}
 stopAudition();previewButton=button;previewProgress=progress;previewKey=key;player.src=source;
 player.play().catch(showPreviewError);
}
function audioTime(value){const seconds=Math.max(0,Math.floor(Number(value)||0));return Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0');}
function renderSearchActions(actions,track){
 const query=encodeURIComponent([track.title,...(track.artists||[])].filter(Boolean).join(' '));
 const qq=document.createElement('a');qq.className='secondary button';qq.target='_blank';qq.rel='noopener noreferrer';qq.href='https://y.qq.com/n/ryqq/search?w='+query;qq.textContent='去 QQ 音乐搜索';
 const netease=document.createElement('a');netease.className='secondary button';netease.target='_blank';netease.rel='noopener noreferrer';netease.href='https://music.163.com/#/search/m/?s='+query;netease.textContent='去网易云音乐搜索';actions.append(qq,netease);
}
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
$('sourceUrl').oninput=()=>{if($('sourceUrl').value)$('sourceFile').value='';};
$('sourceSelector').onchange=()=>action(()=>openSource($('sourceSelector').value));
$('reloadSources').onclick=()=>action(()=>loadSources(current?.id||''));
$('refreshSource').onclick=()=>action(async()=>{const force=!!current.needs_confirmation;if(force&&!await PCHUI.confirm('来源歌曲比上次少很多。确认用最新公开歌单覆盖上次读取结果？Plex 歌单仍会先经过归属校验。'))return;await json('/api/external/sources/'+encodeURIComponent(current.id)+'/refresh','POST',{confirm_large_removal:force});notify('正在重新读取来源');await pollJob('刷新完成');});
$('publishSource').onclick=()=>action(async()=>{const title=$('plexPlaylistTitle').value.trim(),matched=count('matched');if(!title)throw Error('请填写 Plex 歌单名称');if(!await PCHUI.confirm('确认在 Plex 创建或更新“'+title+'”？\n只包含 '+matched+' 首可靠匹配的本地歌曲。'))return;await json('/api/external/sources/'+encodeURIComponent(current.id)+'/publish','POST',{confirm:true,title,revision:current.revision});notify('正在写入经过验证的 Plex 歌单');await pollJob('Plex 歌单已更新');});
$('followUpdates').onchange=()=>action(async()=>{const enabled=$('followUpdates').checked;try{await json('/api/external/sources/'+encodeURIComponent(current.id)+'/settings','POST',{follow_updates:enabled});current.follow_updates=enabled;notify(enabled?'已开启自动刷新':'已关闭自动刷新');}catch(error){$('followUpdates').checked=!enabled;throw error;}});
$('removeSource').onclick=()=>action(async()=>{const title=current.managed?.title||current.title;if(!await PCHUI.confirm('确认移除“'+title+'”？\n如果它由本软件创建，会在归属与内容校验通过后删除对应 Plex 歌单；其他歌单不会改动。',{confirmText:'确认移除'}))return;await json('/api/external/sources/'+encodeURIComponent(current.id)+'/remove','POST',{confirm:true,title});notify('正在安全移除');current=null;await pollJob('已移除');});
document.querySelectorAll('.external-count-tab').forEach(button=>button.onclick=()=>action(async()=>{activeStatus=button.dataset.matchStatus;page=1;await openSource(current.id); }));
$('previousTracks').onclick=()=>action(async()=>{page=Math.max(1,page-1);await openSource(current.id);});
$('nextTracks').onclick=()=>action(async()=>{page+=1;await openSource(current.id);});
$('copyMissing').onclick=()=>action(async()=>{await copyText(await missingText());notify('补歌清单已复制，请粘贴到官方音乐客户端中使用。');});
$('downloadImage').onclick=()=>action(async()=>{await downloadLongImages();notify('补歌清单图片已生成');});
$('downloadText').onclick=event=>{event.preventDefault();action(()=>downloadExport('text'));};
$('downloadCsv').onclick=event=>{event.preventDefault();action(()=>downloadExport('csv'));};
$('copyShareLink').onclick=()=>action(async()=>{const url=new URL(location.href);url.searchParams.set('source',current.id);url.searchParams.set('tab','missing');await copyText(url.toString());notify('查看链接已复制；打开后仍需登录本应用。');});
const player=$('auditionPlayer');player.addEventListener('playing',()=>{const button=previewButton;if(button)button.textContent='暂停';if(previewProgress)previewProgress.classList.remove('is-error');});player.addEventListener('pause',()=>{if(previewButton&&previewKey)previewButton.textContent='继续';});player.addEventListener('timeupdate',()=>{if(previewProgress)previewProgress.textContent=audioTime(player.currentTime)+(Number.isFinite(player.duration)?' / '+audioTime(player.duration):'');});player.addEventListener('ended',()=>{if(previewButton)previewButton.textContent='重播';if(previewProgress&&Number.isFinite(player.duration))previewProgress.textContent=audioTime(player.duration)+' / '+audioTime(player.duration);});player.addEventListener('error',showPreviewError);
window.addEventListener('pch-profile-change',()=>action(async()=>{current=null;page=1;await loadSources();}));
async function boot(){
 const requested=new URLSearchParams(location.search).get('tab');if(['matched','review','missing'].includes(requested))activeStatus=requested;
 await loadSources();
}
window.addEventListener('pch-auth-ready',event=>{if(event.detail?.authenticated)action(boot);});
window.addEventListener('pch-auth-login',()=>action(boot));
})();
