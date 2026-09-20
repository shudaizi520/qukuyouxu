'use strict';
const $=id=>document.getElementById(id);
let current=null,timer=null,reviewId='',busy=false,polling=false,lastPhase='',qqQrLoading=false,lastPlexLinkRefresh=0,metadataReviewOffset=0,metadataReviewNext=null;
const ACTIVE_POLL_MS=3000,IDLE_POLL_MS=45000,PLEX_LINK_TTL_MS=300000;
function note(message,error=false){PCHUI.notify(message,{error});}
function timeText(seconds){return seconds?new Date(seconds*1000).toLocaleString('zh-CN',{hour12:false}):'—';}
function number(value){return typeof value==='number'?value.toLocaleString('zh-CN'):'—';}
async function request(path,method='GET',body){return PCHAuth.request(path,method,body);}
async function action(fn){
 if(busy)return;busy=true;note('');
 try{await PCHUI.run(fn);await refresh();}catch(e){note(e.message,true);}finally{busy=false;schedulePolling();}
}
async function post(path,body){return (await request(path,'POST',body)).json();}
function addText(el,tag,text,className){const n=document.createElement(tag);n.textContent=text;if(className)n.className=className;el.append(n);return n;}
function blobDataUrl(blob){return new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result||''));r.onerror=()=>reject(Error('二维码读取失败'));r.readAsDataURL(blob);});}
async function loadQQQR(){if(qqQrLoading)return;qqQrLoading=true;try{const r=await request('/api/qq-auth/qrcode');const data=await blobDataUrl(await r.blob());$('qqAuthQR').src=data;$('qqAuthQRWrap').hidden=false;}finally{qqQrLoading=false;}}
function renderQQAuth(auth,running){
 const a=auth||{},logged=!!a.logged_in,phase=a.phase||'idle',active=['starting','qr_ready','scanned','confirming'].includes(phase);
 $('qqAuthPanel').hidden=false;$('qqAuthStart').hidden=logged||active;$('qqAuthStart').disabled=running;$('qqAuthLogout').hidden=!logged;$('qqAuthLogout').disabled=running;
 $('qqAuthTitle').textContent=logged?'QQ 已授权，可读取主题歌单':active?'正在授权 QQ':'先授权 QQ，再整理主题';
 $('qqAuthMessage').textContent=a.message||(logged?'授权有效。':'点击“扫码授权 QQ”，用手机 QQ 确认一次即可。');
 if(!active){$('qqAuthQRWrap').hidden=true;$('qqAuthQR').removeAttribute('src');}
 else if(['qr_ready','scanned','confirming'].includes(phase)&&!$('qqAuthQR').getAttribute('src'))loadQQQR().catch(e=>note(e.message,true));
 return logged;
}
async function refresh(skipPlexLink=false){
 if(polling)return;polling=true;
 try{current=await(await request('/api/workflow/status?release=1.4.4')).json();render(current);if(!libraryNavigationReady){libraryNavigationReady=true;revealLibraryTarget();}if(!skipPlexLink||!lastPlexLinkRefresh||Date.now()-lastPlexLinkRefresh>=PLEX_LINK_TTL_MS)await refreshPlexLink();return true;}finally{polling=false;}
}
function render(data){
 const w=data.workflow,s=w.state||{},sum=w.summary||{},job=w.job||{},running=!!job.running,discovery=w.discovery||{};
 const ss=w.single?.state||{};const phase=w.needs_setup?'setup':s.phase||'idle';
 if(lastPhase!==phase&&['review','ready','paused','cooldown','attention','error','theme_error'].includes(phase)&&!$('notice').classList.contains('error'))note('');lastPhase=phase;
 const rawUpstream=w.theme?.error;const upstream=rawUpstream&&Object.keys(rawUpstream).length?rawUpstream:null;const themeFailed=!!upstream||phase==='theme_error';
 const hold=Math.max(w.single?.cooldown?.until||0,s.retry_after||0,w.theme?.cooldown?.until||0);const cooling=hold>Date.now()/1000;
 $('setupPrompt').hidden=!w.needs_setup;
 const qqLogged=w.qq_auth?renderQQAuth(w.qq_auth,running):true;if(!w.qq_auth)$('qqAuthPanel').hidden=true;
 $('sourceStepState').textContent=w.needs_setup?'先连接 Plex':qqLogged?'已授权':'需要授权';
 $('totalCount').textContent=number(sum.library_count);$('classifiedCount').textContent=number(sum.matched);$('reviewCount').textContent=number(sum.review_count);$('playlistCount').textContent=number(sum.managed);
 const titles={idle:'新歌整理，从这里开始',setup:'先完成一次连接设置',checking:'正在读取新歌',enriching:'正在补充歌曲资料',planning:'正在整理分类',publishing:'正在同步歌单',review:'分类已准备好，等你确认',ready:'已整理，可以去听歌了',paused:'整理已暂停，进度已保留',cooldown:'联网暂时暂停，已有资料仍可用',theme_error:'主题读取失败，已有资料仍可用',error:'这轮整理遇到了问题',attention:'有部分歌单需要核对',external:'另一个任务正在执行'};
 const badges={idle:'等待开始',setup:'需要设置',checking:'执行中',enriching:'执行中',planning:'执行中',publishing:'执行中',review:'等待确认',ready:'已同步',paused:'已暂停',cooldown:'联网等待',theme_error:'读取失败',error:'需要处理',attention:'需要处理',external:'执行中'};
 $('taskTitle').textContent=titles[phase]||'整理新增歌曲';$('taskBadge').textContent=badges[phase]||'等待开始';$('taskBadge').className='status-pill'+(['review','cooldown','attention','error','theme_error'].includes(phase)?' warm':'');
 $('taskMessage').textContent=s.message||'点击整理新增歌曲，助手会依次处理，不需要到高级设置中逐个点击。';
 if(running&&job.message)$('taskMessage').textContent=job.message;
 if(phase==='idle'&&ss.processed)$('taskMessage').textContent='已保留 '+number(ss.processed)+' 首的资料处理记录。点击整理继续后续步骤，有效缓存不用重查。';
 if(phase==='cooldown'&&!themeFailed)$('taskMessage').textContent=(s.message||'QQ暂时不可用。')+(cooling?' 可继续时间：'+timeText(hold):' 现在可以点击继续整理。');
 if(phase==='setup')$('taskMessage').textContent='先填写 Plex 连接和音乐资料库。已经设置过的不需要重新填写。';
 if(!w.needs_setup&&!qqLogged&&!['review','publishing'].includes(phase)){$('taskTitle').textContent='先扫码授权 QQ';$('taskBadge').textContent='需要授权';$('taskMessage').textContent='主题歌单详情现在需要助手自己的 QQ 授权。扫一次码即可；不会读取你电脑浏览器的 Cookie 或密码。';}
 const stageNames=['checking','enriching','planning','publishing'];let stage=stageNames.indexOf(phase);
 if(phase==='review')stage=3;if(phase==='ready')stage=4;if(themeFailed&&['cooldown','theme_error'].includes(phase))stage=2;
 document.querySelectorAll('.steps li').forEach((li,i)=>{li.className=i<stage?'done':i===stage&&running?'active':'';});
 const jobTotal=Number(job.progress_total||0),jobCurrent=Number(job.progress_current||0);
 const singleTotal=Number(ss.library_count||0),singleCurrent=Number(ss.processed||0);
 const progressTotal=jobTotal||singleTotal,progressCurrent=jobTotal?jobCurrent:singleCurrent;
 const showProgress=!themeFailed&&((running&&['checking','enriching','planning','publishing','external'].includes(phase))||phase==='paused'||phase==='cooldown');
 $('progressArea').hidden=!showProgress;
 if(showProgress){
  $('progressText').textContent=job.message||s.message||titles[phase]||'正在整理';
  if(progressTotal>0){$('progress').max=progressTotal;$('progress').value=Math.min(progressCurrent,progressTotal);$('progressPercent').textContent=Math.round(Math.min(progressCurrent,progressTotal)/progressTotal*100)+'% · '+number(progressCurrent)+' / '+number(progressTotal);}
  else{$('progress').removeAttribute('value');$('progress').max=1;$('progressPercent').textContent='';}
 }
 $('incrementalAction').disabled=running||w.needs_setup||cooling;
 $('analyzeLibrary').disabled=running||w.needs_setup||(cooling&&phase!=='review')||(!qqLogged&&phase!=='review');
 $('cachedAction').hidden=!(themeFailed||phase==='cooldown'||s.cache_only);$('cachedAction').disabled=running||w.needs_setup;
 renderUpstreamError(upstream,w,hold,cooling);
 renderReferenceSkips(w.theme?.skipped_references||[]);
 $('analyzeLibrary').textContent=phase==='review'?'查看候选歌单':phase==='paused'||phase==='cooldown'?'继续分析':phase==='error'||phase==='attention'?'重新分析':'分析曲库';
 if(themeFailed&&phase!=='review')$('analyzeLibrary').textContent='重新分析';
 if(!qqLogged&&phase!=='review')$('analyzeLibrary').textContent='先完成 QQ 授权';
 $('pause').hidden=!(running&&job.can_pause);$('pause').disabled=false;
 $('incrementalAction').hidden=running;$('analyzeLibrary').hidden=running;$('cachedAction').hidden=running||!(themeFailed||phase==='cooldown'||s.cache_only);
 $('refreshReview').disabled=running||(cooling&&!w.review?.cache_only);
 $('attentionLink').hidden=!(['attention','error','theme_error'].includes(phase)||sum.review_count>0);
 const tips={checking:'正在执行，无需操作。关闭网页不会取消 NAS 任务。',enriching:'首次可能较久；新增歌曲会复用缓存，不是每次都重查全库。',planning:'资料处理完成，正在生成歌单结果，请稍候。',publishing:'正在提交已确认的变更，请不要重启应用。',review:'下一步：在下面勾选歌单，点击“确认选中歌单并同步”。',ready:'以后加歌：先让 Plex 扫描入库，再点一次整理；也可以开启下面的自动开关。',paused:'继续整理会复用检查点。自动开关与当前任务的暂停是两回事。',cooldown:'已完成的资料保留；不要反复点击或重新开始全库。',error:'先看下方“本次结果与排查”。已完成资料保留，不需要重装。',attention:'安全保护已跳过异常歌单，不会覆盖你的手工修改。详情见“本次结果与排查”。',external:'请等待高级任务完成，再使用首页的一键流程。'};
 $('nextStep').textContent=tips[phase]||'首次同步会在这里等你确认，不会直接修改 Plex 歌单。';
 if(themeFailed&&['theme_error','cooldown'].includes(phase))$('nextStep').textContent='可直接点“仅用已有资料生成预览”，无需等联网恢复；新主题未读取成功的部分会跳过。联网重试只尝试一次，不保证会恢复。';
 if(!w.needs_setup&&!qqLogged&&phase!=='review')$('nextStep').textContent='下一步：点上方“扫码授权 QQ”，手机确认后再点整理。已查好的三千首资料不会重跑。';
 renderReview(w.review,running);$('reviewEmpty').hidden=discovery.phase!=='empty';$('syncStepState').textContent=w.review?'等待确认':phase==='ready'?'已同步':'等待预览';
 $('migrationNotice').textContent=w.notice||'';
 const result=s.result;$('resultText').textContent=result?('最近一次：更新 '+(result.written||0)+' 个，无变化 '+(result.unchanged||0)+' 个，保护跳过 '+(result.blocked||0)+' 个。'):'还没有通过首页同步。已有歌单不会被删除或重新建立。';
 $('resultIssues').replaceChildren();for(const e of result?.errors||[])addText($('resultIssues'),'p',e);
 if(phase==='error'||phase==='attention'||phase==='theme_error'||phase==='cooldown')addText($('resultIssues'),'p',s.message||'');
 // Open Plex is resolved from this installation's saved settings, never a cached workflow URL.
 if(window.renderThemeExtras)window.renderThemeExtras(data);
 renderLibraryPresentation(w,phase,running);
}
function renderReferenceSkips(rows){
 $('referenceSkips').hidden=!rows.length;
 if(!rows.length)return;
 $('skipSummary').textContent='共有 '+rows.length+' 份参考歌单隐私校验未通过，暂不访问这些来源；不需要等待它们恢复，其他可访问来源继续。已查到的单曲资料保留，未把跳过来源计入成功。';
 $('skipRows').replaceChildren();
 for(const row of rows.slice(0,20))addText($('skipRows'),'p','歌单 '+row.id+'：'+row.reason+'。');
 if(rows.length>20)addText($('skipRows'),'p','其余记录包含在“下载排查报告”中。');
}
function renderUpstreamError(err,w,hold,cooling){
 $('upstreamError').hidden=!err;
 if(!err)return;
 $('errorSummary').textContent=err.message||'旧版本没有保留具体失败代码。';
 $('errorDetails').replaceChildren();
 const rows=[['失败时间',timeText(err.recorded_at)],['读取步骤',err.operation],['接口地址',err.endpoint],
  ['HTTP状态',err.http_status],['QQ code',err.code],['QQ subcode',err.subcode],['RPC code',err.rpc_code],
  ['QQ返回信息',err.server_message],['请求位置',Object.entries(err.context||{}).map(([k,v])=>k+'='+v).join('，')]];
 for(const [label,value] of rows){if(value===undefined||value===null||value==='')continue;addText($('errorDetails'),'dt',label);addText($('errorDetails'),'dd',String(value));}
 const seconds=Math.max(0,Math.ceil(hold-Date.now()/1000));
 let text=err.wait_basis==='legacy_blanket_corrected'?'已纠正旧版程序一律等待一小时的策略；原QQ错误码未保存，需要一次手动联网重试才能取得真实详情。':err.wait_basis==='server_retry_after'?'QQ返回了 Retry-After 等待提示，联网请求会遵守。':err.wait_basis==='legacy_local_backoff'?'这是旧版程序留下的等待时间，QQ没有在该旧记录里给出恢复时间。':'这是程序的防重复请求间隔，不是QQ宣布的恢复时间；普通错误不再统一锁一小时。';
 if(cooling)text+=' 联网最早可再试：'+timeText(hold)+'（约剩'+Math.ceil(seconds/60)+'分钟）。';
 else text+=' 现在可手动点“重新联网整理一次”。';
 text+=' 重试不保证成功；“仅用已有资料生成预览”不受这个时间限制。';
 $('retryExplanation').textContent=text;
}
function blockedReviewReasons(review){
 return [...new Set((review?.groups||[]).flatMap(group=>group.blocked||[]).filter(Boolean))];
}
function renderReview(review,running){
 $('reviewPanel').hidden=!review;if(!review){reviewId='';return;}
 const usableGroups=review.groups.filter(group=>!group.blocked.length);
 const blockedReasons=blockedReviewReasons(review);
 $('selectAll').disabled=!usableGroups.length;
 $('reviewMessage').textContent=review.expired?review.problem:'勾选需要的歌单，确认后才同步。首次确认的管理范围会记住，新分类或设置变化仍会先问你。';
 if(review.cache_only&&!review.expired)$('reviewMessage').textContent='本次仅使用已有有效缓存，没有联网扩充主题。过期、缺失或不完整来源不参与写入；请选择需要同步的结果。';
 if(!usableGroups.length&&blockedReasons.length)$('reviewMessage').textContent=blockedReasons.join('；');
 if(reviewId!==review.id){
  reviewId=review.id;$('reviewRows').replaceChildren();
  for(const g of usableGroups){
   const tr=document.createElement('tr');const td=addText(tr,'td','');const box=document.createElement('input');box.type='checkbox';box.value=g.id;box.checked=!!g.default_selected;box.disabled=!!g.blocked.length;box.setAttribute('aria-label','同步 '+g.title);box.onchange=updateSelection;td.append(box);
   addText(tr,'td',g.title);addText(tr,'td',number(g.count));const actionCell=addText(tr,'td','');const view=document.createElement('button');view.type='button';view.className='secondary evidence-button';view.textContent='查看歌曲';view.onclick=()=>action(()=>window.openThemeEvidence(g.id,false));actionCell.append(view);
   $('reviewRows').append(tr);
  }
 }
 $('confirmReview').disabled=running||review.expired||!usableGroups.length;updateSelection();
}
async function loadMetadataReview(offset=0){
 const r=await(await request('/api/metadata?review_only=true&offset='+offset+'&limit=50')).json();
 metadataReviewOffset=offset;metadataReviewNext=r.next;
 $('metadataReviewBack').disabled=offset===0;$('metadataReviewNext').disabled=r.next===null;
 $('metadataReviewRows').replaceChildren();
 const labels={incomplete:'标签不完整',conflict:'标签存在冲突',stale_correction:'原信息变化，需重新核对'};
 for(const row of r.items){
  const item=document.createElement('article');item.className='metadata-review-row';
  const identity=document.createElement('div');addText(identity,'strong',(row.original?.title||'无歌名')+' · '+(row.original?.artist||'无歌手'));addText(identity,'small','Plex 当前标签');
  const issue=document.createElement('div');addText(issue,'strong',labels[row.status]||'需要核对');addText(issue,'small',(row.issues||[]).join('；')||'身份信息不完整');
  const clue=document.createElement('div');addText(clue,'strong',row.suggestion?(row.suggestion.title+' · '+row.suggestion.artist):'没有明确文件名线索');addText(clue,'small',row.suggestion?.filename||'助手不会猜测或自动覆盖');
  item.append(identity,issue,clue);$('metadataReviewRows').append(item);
 }
 if(!r.items.length)addText($('metadataReviewRows'),'p','目前没有需要人工核对的标签问题。','muted');
 $('metadataReviewSummary').textContent='共 '+r.total+' 首，当前显示 '+(r.items.length?offset+1:0)+'～'+(offset+r.items.length)+'。不处理的歌曲会保持原样，不会被强行分类。';
}
async function openMetadataReview(){
 $('metadataReviewPanel').hidden=false;$('attentionLink').setAttribute('aria-expanded','true');
 await loadMetadataReview(0);$('metadataReviewPanel').scrollIntoView({block:'start'});
}
function selected(){return [...$('reviewRows').querySelectorAll('input:checked:not(:disabled)')].map(n=>n.value);}
function updateSelection(){const all=[...$('reviewRows').querySelectorAll('input:not(:disabled)')];const n=selected().length;$('selectedCount').textContent=n?'已选择 '+n+' 个歌单':'';$('selectAll').checked=!!all.length&&n===all.length;$('selectAll').indeterminate=n>0&&n<all.length;$('confirmReview').disabled=!!current?.workflow?.job?.running||!!current?.workflow?.review?.expired||!n;}
async function run(){
 if(!current)return;
 if(current.workflow.state.phase==='review'){$('reviewPanel').scrollIntoView({block:'start'});return;}
 if(!current.workflow.settings.initialized&&!await PCHUI.confirm('开始整理 Plex 已入库的歌曲？只向 QQ 查询歌名、歌手或曲目编号，不发送音频和 Plex 密钥。首次写入歌单仍需下一步确认。'))return;
 await post('/api/workflow/run',{confirm:true});note('整理已启动。页面会自动显示下一步，可关闭网页等待。');
}
async function runIncremental(){
 await post('/api/workflow/incremental',{confirm:true});note('已开始检查新增歌曲。旧歌曲直接复用，进度会自动保存。');
}
$('qqAuthStart').onclick=()=>action(async()=>{const r=await post('/api/qq-auth/start',{confirm:true});note(r.message||'二维码已生成，请用手机 QQ 扫码确认。');await loadQQQR();});
$('qqAuthLogout').onclick=()=>action(async()=>{if(!await PCHUI.confirm('退出助手中的 QQ 授权？不会影响电脑浏览器里的 QQ 登录，也不会删除已查好的歌曲资料。'))return;const r=await post('/api/qq-auth/logout',{confirm:true});note(r.message||'已退出助手中的 QQ 授权。');});
$('analyzeLibrary').onclick=()=>action(run);
$('incrementalAction').onclick=()=>action(runIncremental);
$('cachedAction').onclick=()=>action(async()=>{await post('/api/workflow/cached',{confirm:true});note('正在用已有资料生成预览，不访问 QQ，确认前不写歌单。');});
$('pause').onclick=()=>action(async()=>{const r=await post('/api/workflow/pause',{});note(r.message);});
$('refreshReview').onclick=()=>action(async()=>{await post(current.workflow.review?.cache_only?'/api/workflow/cached':'/api/workflow/run',{confirm:true});note('正在重新整理预览，有效缓存仍会复用。');});
$('selectAll').onchange=()=>{for(const n of $('reviewRows').querySelectorAll('input:not(:disabled)'))n.checked=$('selectAll').checked;updateSelection();};
$('confirmReview').onclick=()=>action(async()=>{const ids=selected();if(!ids.length){note('请先勾选至少一个可同步歌单。',true);return;}if(!await PCHUI.confirm('确认同步选中的 '+ids.length+' 个歌单？只新建或追加本助手管理的歌单，不删除旧歌，不修改音乐文件。'))return;await post('/api/workflow/confirm',{review_id:reviewId,selected_ids:ids,confirm:true});note('正在同步选中的歌单，请等待完成。');});
$('attentionLink').onclick=()=>action(openMetadataReview);
$('closeMetadataReview').onclick=()=>{$('metadataReviewPanel').hidden=true;$('attentionLink').setAttribute('aria-expanded','false');};
$('metadataReviewBack').onclick=()=>action(()=>loadMetadataReview(Math.max(0,metadataReviewOffset-50)));
$('metadataReviewNext').onclick=()=>action(()=>loadMetadataReview(metadataReviewNext??metadataReviewOffset));
$('downloadReport').onclick=()=>action(async()=>{const r=await request('/api/workflow/report');const url=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=url;a.download='music-workflow-report.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);});
$('downloadError').onclick=()=>action(async()=>{const r=await request('/api/workflow/report');const u=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=u;a.download='QQ-theme-error-report.json';a.click();setTimeout(()=>URL.revokeObjectURL(u),2000);});


function stopPolling(){clearTimeout(timer);timer=null;}
function workflowIsActive(){const w=current?.workflow||{},phase=w.state?.phase||'',qq=w.qq_auth?.phase||'';return !!w.job?.running||['checking','enriching','planning','publishing','external'].includes(phase)||['starting','qr_ready','scanned','confirming'].includes(qq);}
function schedulePolling(delay=workflowIsActive()?ACTIVE_POLL_MS:IDLE_POLL_MS){stopPolling();if(!PCHAuth.status().authenticated||document.hidden)return;timer=setTimeout(pollOnce,delay);}
async function pollOnce(){timer=null;if(!PCHAuth.status().authenticated||document.hidden)return;try{await refresh(true);}catch(e){note(e.message,true);}schedulePolling();}
async function startPolling(){stopPolling();if(!PCHAuth.status().authenticated||document.hidden)return;try{await refresh(true);}catch(e){note(e.message,true);}schedulePolling();}
window.addEventListener('visibilitychange',()=>{if(document.hidden)stopPolling();else startPolling();});
window.addEventListener('pagehide',stopPolling);
window.addEventListener('pch-auth-ready',startPolling);
window.addEventListener('pch-auth-login',startPolling);
window.addEventListener('pch-auth-logout',stopPolling);

/* Presentation only: preserve the workflow API, approval and ownership checks. */
let presentationAuthState=null;
function renderLibraryPresentation(w,phase,running){
 const auth=w.qq_auth,logged=!!auth?.logged_in,disclosure=$('qqDisclosure');
 const discovery=w.discovery||{};
 if(auth){
  if(!logged)disclosure.open=true;
  else if(presentationAuthState!==true)disclosure.open=false;
  presentationAuthState=logged;
  $('sourceStepState').textContent=logged?'QQ 已授权':'QQ 需要授权';
 }else{$('sourceStepState').textContent='QQ 状态待确认';}
 $('reviewEmpty').hidden=discovery.phase!=='empty';
 $('attentionLink').textContent='查看待核对'+(w.summary?.review_count?' · '+number(w.summary.review_count):'');
 if(discovery.phase==='choose')$('taskTitle').textContent='发现可创建的歌单';
 else if(discovery.phase==='empty')$('taskTitle').textContent='分析完成';
 else if(discovery.phase==='before_analysis'&&!w.needs_setup)$('taskTitle').textContent='分析曲库';
 else if(phase==='ready'){
  $('taskTitle').textContent='最近整理';
  const r=w.state?.result;
  if(r)$('taskMessage').textContent=number(r.written||0)+' 个歌单已更新 · '+number(r.unchanged||0)+' 个无需变化'+(r.blocked?' · '+number(r.blocked)+' 个保护跳过':'');
 }
 if(phase==='idle')$('taskMessage').textContent='点击“分析曲库”，完成后选择要创建的歌单。';
 const help={publishing:'正在同步，请勿重启应用。',attention:'保护已生效，未覆盖异常歌单。请查看运行详情。',paused:'进度已保留，可以继续整理。'};
 $('nextStep').hidden=true;if(help[phase])$('nextStep').textContent=help[phase];
 const allGroupsBlocked=!!w.review?.groups?.length&&!(w.review.groups.some(group=>!group.blocked.length));
 const reviewNeedsMessage=!!(w.review?.expired||w.review?.cache_only||allGroupsBlocked&&blockedReviewReasons(w.review).length);
 $('reviewMessage').hidden=!reviewNeedsMessage;
 if(!running&&!w.needs_setup&&logged&&discovery.phase==='before_analysis')$('analyzeLibrary').textContent='分析曲库';
 $('task').hidden=false;
}

/* Never trust an old workflow URL or put authentication into the link. */
let plexNavigationSequence=0,libraryNavigationReady=false;
async function refreshPlexLink(){

 lastPlexLinkRefresh=Date.now();
 const sequence=++plexNavigationSequence,link=$('openPlex'),label=$('plexLinkStatus');
 link.removeAttribute('href');link.setAttribute('aria-disabled','true');
 label.textContent='正在读取当前连接…';
 try{
  const s=await(await request('/api/status')).json();if(sequence!==plexNavigationSequence)return;
  const raw=s.settings?.plex_url;
  if(typeof raw!=='string'||!raw.trim())throw Error('请先设置 Plex 连接');
  const url=new URL(raw);
  if(!['http:','https:'].includes(url.protocol)||url.username||url.password)throw Error('请检查 Plex 地址');
  url.search='';url.hash='';
  const base=url.pathname.replace(/\/web(?:\/index.html)?\/?$/,'').replace(/\/+$/,'');
  url.pathname=base+'/web/';
  link.href=url.href;link.removeAttribute('aria-disabled');
  link.title='打开当前设置中的 Plex：'+url.host;label.textContent=url.host;
 }catch{
  if(sequence!==plexNavigationSequence)return;
  link.removeAttribute('href');link.setAttribute('aria-disabled','true');
  link.title='请到设置检查 Plex 连接';label.textContent='连接地址未就绪，请检查设置';
 }
}
window.addEventListener('hashchange',revealLibraryTarget);
function revealLibraryTarget(){
 let id;try{id=decodeURIComponent(location.hash.slice(1));}catch{return;}
 const target=document.getElementById(id);if(!target)return;
 if(id==='metadataReviewPanel'){openMetadataReview().catch(e=>note(e.message,true));return;}
 for(let node=target;node;node=node.parentElement)if(node.tagName==='DETAILS')node.open=true;
 target.scrollIntoView({block:'start'});
}
window.addEventListener('pch-auth-logout',()=>{lastPlexLinkRefresh=0;libraryNavigationReady=false;plexNavigationSequence++;$('openPlex').removeAttribute('href');$('openPlex').setAttribute('aria-disabled','true');$('plexLinkStatus').textContent='';});
