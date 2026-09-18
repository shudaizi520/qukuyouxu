'use strict';const recommendationReasonsVisible='recommendationReasonsVisible';const $=id=>document.getElementById(id);let current=null,timer=null,busy=false,polling=false,lastPlan='',dailyOperation=null;
const ACTIVE_POLL_MS=3000,IDLE_POLL_MS=45000;
function note(t,e=false){PCHUI.notify(t,{error:e});}
async function request(path,method='GET',body){return PCHAuth.request(path,method,body);}async function post(path,body){return PCHAuth.post(path,body);}
function n(v){return Number.isFinite(Number(v))?Number(v).toLocaleString('zh-CN'):'0';}function time(v){return v?new Date(v*1000).toLocaleString('zh-CN',{hour12:false}):'尚无';}
async function action(fn){if(busy)return;busy=true;note('');try{await PCHUI.run(fn);try{await refresh();}catch(e){dailyPollError(e);}}catch(e){note(e.message,true);}finally{busy=false;schedulePolling();}}
function setupNeeded(s){const c=s.settings||{};return !(c.plex_url&&c.token_present&&c.section);}
function addSong(row,index){const el=document.createElement('div');el.className='song';const pos=document.createElement('span');pos.className='song-index';pos.textContent=String(index+1).padStart(2,'0');const left=document.createElement('div');const title=document.createElement('div');title.className='song-title';title.textContent=row.title||'未命名歌曲';left.append(title);const meta=document.createElement('div');meta.className='song-meta';meta.textContent=(row.artist||'未知歌手')+(row.album?' · '+row.album:'');left.append(meta);const reasonRows=row.reasons?.length?row.reasons:[row.bucket||row.source_bucket||'按当前推荐规则选入'];const why=document.createElement('div');why.className='reasons';why.textContent='推荐原因：'+reasonRows.join(' · ');left.append(why);const bucket=document.createElement('span');bucket.className='bucket';bucket.textContent=row.bucket||'推荐';const actions=document.createElement('div');actions.className='song-actions';const avoid=document.createElement('button');avoid.className='secondary';avoid.textContent='不再推荐';avoid.onclick=()=>action(async()=>{await post('/api/feedback',{kind:'track',id:String(row.id),value:'avoid'});note('已记录，下次生成时会排除这首歌。');});actions.append(avoid);if(row.artist){const artist=document.createElement('button');artist.className='secondary';artist.textContent='少推这个歌手';artist.onclick=()=>action(async()=>{if(!await PCHUI.confirm('以后减少 '+row.artist+' 的歌曲？当天通常最多保留 1 首。'))return;await post('/api/feedback',{kind:'artist',artist:row.artist,value:'avoid'});note('已记录；下次生成时会明显降权，通常最多 1 首。');});actions.append(artist);}el.append(pos,left,bucket,actions);$('songs').append(el);}
function setFlow(stage){for(const [id,n] of [['flowGenerate',1],['flowPreview',2],['flowPublish',3]]){$(id).classList.toggle('active',n===stage);$(id).classList.toggle('done',n<stage);}}
function render(s){current=s;clearDailyPollErrors();renderDailyOperation(s);$('version').textContent='v'+s.version;const need=setupNeeded(s);$('setupPrompt').hidden=!need;$('dailyArea').hidden=need;const job=s.job||{},running=!!job.running,cfg=s.daily_settings||{},plan=s.daily_plan||{},managed=s.daily_managed||null,behavior=s.behavior||{},repair=s.daily_repair||null;
 $('repairCard').hidden=!repair||need;$('repairDaily').disabled=running||!repair;if(repair)$('repairText').textContent='上次每日推荐停在写入中间：原有 '+n(repair.before_count)+' 首，目标 '+n(repair.desired_count)+' 首。只修复本助手自己的每日推荐。';
 $('generate').disabled=need||running;$('fullRefresh').disabled=need||running;$('generate').textContent=running&&job.kind==='daily_preview'?'正在生成…':(plan.id&&!plan.applied?'重新检查':'更新今日推荐');
 const blocking=dailyBlockReasons(plan);const publishable=!!(plan.id&&!plan.applied&&!blocking.length);$('recheckDaily').hidden=!blocking.length;$('recheckDaily').disabled=running;$('publish').disabled=running||!publishable;$('publish').textContent=running&&job.kind==='daily_apply'?'正在发布…':'发布到 Plexamp';$('publishHint').hidden=!blocking.length;$('publishHint').textContent=blocking.length?('需要处理：'+blocking.join('；')):'';
 $('targetCount').textContent=n(cfg.size??30);$('actualCount').textContent=n(plan.items?.length||0);$('favoriteCount').textContent='≤'+n(Math.floor((cfg.size??30)*(cfg.favorite_percent??20)/100));$('avoidDays').textContent=n(plan.stats?.daily_avoid_window_days??cfg.daily_avoid_days??21)+'天';const webhook=s.webhook||{};const learning={disabled:'已关闭',not_connected:'未连接',connected_waiting:'已连接，等待播放记录',learning:'已记录 '+n(behavior.event_count||0)+' 个有效行为'};$('behaviorText').textContent=learning[webhook.state]||learning.not_connected;
 $('dailyToggle').checked=!!cfg.enabled;$('dailyToggle').disabled=running||!managed;$('scheduleText').textContent=cfg.enabled?String(cfg.hour??6).padStart(2,'0')+':00':managed?'关闭':'首次发布后可开启';
 $('generate').classList.toggle('primary',!publishable);$('generate').classList.toggle('secondary',publishable);renderDailyNotices(plan,blocking);$('bucketSummary').replaceChildren();for(const [k,v] of Object.entries(plan.stats?.bucket_counts||{})){const x=document.createElement('span');x.textContent=k+' '+v+' 首';$('bucketSummary').append(x);}if(plan.stats)$('favoriteCount').textContent=n(plan.stats.favorite_selected_count||0)+' / '+n(Math.floor((cfg.size??30)*(cfg.favorite_percent??20)/100));$('songs').replaceChildren();(plan.items||[]).forEach(addSong);$('emptySongs').hidden=!!(plan.items||[]).length;
 if(plan.id&&!plan.applied&&blocking.length){$('dailyTitle').textContent='推荐已生成';$('dailyState').textContent='需要处理';$('dailyMessage').textContent='发布检查未通过';setFlow(2);}else if(plan.id&&!plan.applied){$('dailyTitle').textContent='今天的推荐';$('dailyState').textContent='等待发布';$('dailyMessage').textContent='这'+n(cfg.size??30)+'首只是在预览中，确认后才会发布';setFlow(2);}else if(plan.applied){$('dailyTitle').textContent='今天的推荐';$('dailyState').textContent='已发布';$('dailyMessage').textContent='已同步到 Plexamp';setFlow(3);}else if(managed){$('dailyTitle').textContent='今天的推荐';$('dailyState').textContent='待更新';$('dailyMessage').textContent='可以生成新一批';setFlow(1);}else{$('dailyTitle').textContent='今天的推荐';$('dailyState').textContent='待生成';$('dailyMessage').textContent='生成预览后再确认发布';setFlow(1);}if(lastPlan!==String(plan.id||'')){lastPlan=String(plan.id||'');window.scrollTo({top:0,behavior:'smooth'});}}
async function refresh(){if(polling)return current;polling=true;try{const s=await(await request('/api/status')).json();render(s);return s;}finally{polling=false;}}
$('generate').onclick=()=>action(async()=>{const baseline=String(current?.daily_plan?.id||'');await post('/api/jobs/daily_preview',{});beginDailyOperation('daily_preview',baseline);});
$('fullRefresh').onclick=()=>action(async()=>{if(!await PCHUI.confirm('整批重新生成今天的推荐？\n现有歌单要等你确认发布后才会变化。',{confirmText:'整批换一批'}))return;const baseline=String(current?.daily_plan?.id||'');await post('/api/jobs/daily_preview',{force_full:true});beginDailyOperation('daily_preview',baseline);});
$('publish').onclick=()=>action(async()=>{const p=current?.daily_plan;if(!p?.id)return;if(!await PCHUI.confirm('确认将当前预览发布到 Plexamp 的“'+(p.stats?.daily_target_title||'每日推荐')+'”？'))return;await post('/api/jobs/daily_apply',{confirm:true,plan_id:p.id});beginDailyOperation('daily_apply',String(p.id));});
$('repairDaily').onclick=()=>action(async()=>{const r=current?.daily_repair;if(!r?.snapshot_id)return;if(!await PCHUI.confirm('确认安全修复上次每日推荐？'))return;await post('/api/jobs/daily_repair',{confirm:true,snapshot_id:r.snapshot_id});note('正在修复。');});
$('dailyToggle').onchange=()=>action(async()=>{const enabled=$('dailyToggle').checked;const r=await post('/api/daily/schedule',{enabled});note(r.message);});

function renderDailyNotices(plan,blocking){
 const meta=document.getElementById('dailyMeta');
 meta.replaceChildren();meta.hidden=!plan.id;
 if(plan.id){
  const stats=plan.stats||{};
  const target=typeof stats.daily_target_title==='string'&&stats.daily_target_title?stats.daily_target_title:'每日推荐';
  const destination=document.createElement('span');destination.className='daily-meta-target';
  const label=document.createElement('span');label.className='daily-meta-label';label.textContent=plan.applied?'歌单':'发布到';
  const name=document.createElement('strong');name.className='daily-meta-value';name.textContent=target;
  destination.append(label,name);meta.append(destination);
  const count=document.createElement('span');count.className='daily-meta-stat';
  count.textContent=(Array.isArray(plan.items)?plan.items.length:0)+' 首';meta.append(count);
  const overlap=stats.rotation_overlap;
  if(overlap!==null&&overlap!==undefined&&Number.isInteger(Number(overlap))&&Number(overlap)>=0){
   const repeat=document.createElement('span');repeat.className='daily-meta-stat';
   repeat.textContent='与上批重复 '+Number(overlap)+' 首';
   repeat.title='优先按设置避开最近推荐；候选不足时会按 7、3、1 天逐步放宽。';meta.append(repeat);
  }
 }
 const warnings=document.getElementById('warnings');warnings.replaceChildren();
 const routine=w=>typeof w==='string'&&(
  /^发布目标：.+（生成预览不会修改 Plex）$/.test(w)||
  /^本批\d+首，与上一批重复\d+首；按最近推荐防重复规则选择。$/.test(w));
 for(const message of [...blocking.map(x=>'发布检查：'+x),...(plan.warnings||[]).filter(w=>!routine(w))]){
  const row=document.createElement('div');row.className='warning';row.textContent=message;warnings.append(row);
 }
}

function dailyBlockReasons(plan){
 const raw=plan.blocked;
 if(Array.isArray(raw))return raw.filter(Boolean).map(x=>typeof x==='string'?x:(x.message||x.reason||'发布检查未通过'));
 if(typeof raw==='string')return raw.trim()?[raw]:[];
 return raw?['发布检查未通过，请重新检查。']:[];
}
function dailyBlockAdvice(reasons,repair){
 if(repair)return '上次写入尚未收尾，请使用上方“安全修复”，完成后重新生成预览。';
 const text=reasons.join('；');
 if(/账户|服务器|资料库|身份/.test(text))return '请先在“设置”确认 Plex 地址、账户及音乐资料库，再重新检查。';
 if(/手动|管理标记|同名/.test(text))return '当前歌单与托管记录不一致，助手会保留现有歌单；重新检查后仍阻止时，请提供这里的具体原因。';
 return '可以点击“重新检查并生成预览”。现有歌单会保持，检查通过后才可发布。';
}


function stopPolling(){clearTimeout(timer);timer=null;}
function pollingDelay(){return current?.job?.running||dailyOperation?ACTIVE_POLL_MS:IDLE_POLL_MS;}
function schedulePolling(delay=pollingDelay()){stopPolling();if(!PCHAuth.status().authenticated||document.hidden)return;timer=setTimeout(pollOnce,delay);}
async function pollOnce(){timer=null;if(!PCHAuth.status().authenticated||document.hidden)return;try{await refresh();}catch(e){dailyPollError(e);}schedulePolling();}
async function startPolling(){stopPolling();if(!PCHAuth.status().authenticated||document.hidden)return;try{await refresh();}catch(e){dailyPollError(e);}schedulePolling();}
window.addEventListener('visibilitychange',()=>{if(document.hidden)stopPolling();else startPolling();});
window.addEventListener('pagehide',stopPolling);
window.addEventListener('pch-auth-ready',startPolling);
window.addEventListener('pch-auth-login',startPolling);
window.addEventListener('pch-auth-logout',stopPolling);

$('recheckDaily').onclick=()=>action(async()=>{const baseline=String(current?.daily_plan?.id||'');await post('/api/jobs/daily_preview',{});beginDailyOperation('daily_preview',baseline);});

(function(){
 const button=document.createElement('button');
 button.id='reviewDailyOwnership';button.className='secondary wide';
 button.textContent='核对发布冲突';button.hidden=true;
 const anchor=document.getElementById('publishHint');
 anchor.before(button);
 async function review(){
  button.disabled=true;
  try{
   const r=await (await request('/api/daily/reconciliation')).json();
   if(!r.can_accept){
    const next=await (await request('/api/daily/restart')).json();
    const ok=await PCHUI.confirm('旧歌单“'+next.title+'”的管理标记缺失或不符。\n\n是否保留旧歌单完全不动，改用一张新的“'+next.new_title+'”？\n\n本次只设置新目标；之后还需生成预览并点击发布，才会建立新歌单。未来每天更新同一张新歌单，不会每天新建。\n\n“我的最爱”和其他歌单不受影响。');
    if(!ok)return;
    const out=await post('/api/daily/restart',{confirm:true,playlist_id:next.playlist_id,review_fingerprint:next.review_fingerprint,new_title:next.new_title});
    note(out.message);await refresh();return;
   }
   if(!r.changed){note('管理记录与当前歌单一致，请重新检查并生成预览。');return;}
   const ok=await PCHUI.confirm('当前歌单：'+r.title+'，共 '+r.count+' 首。\n\n仍带有本助手的管理标记，但内容或描述与记录不同。\n是否认可当前状态，并重新交给助手管理？\n\n本次不修改 Plex，自动更新会暂停。之后你点击“发布”时，会用新预览替换这张歌单的内容，并按原流程保存快照。\n\n若要永久保留当前编辑，请取消。');
   if(!ok)return;
   const out=await post('/api/daily/reconciliation',{confirm:true,playlist_id:r.playlist_id,review_fingerprint:r.review_fingerprint});
   note(out.message);await refresh();
  }catch(e){note(e.message,true);}finally{button.disabled=false;}
 }
 button.onclick=review;
 function update(){
   button.hidden=!/手动修改|管理标记|托管记录/.test(anchor.textContent||'');
 }
 new MutationObserver(update).observe(anchor,{childList:true,subtree:true,characterData:true});
 update();
})();


/* Track only requests started here; server state, not a timer, proves success. */
function dailyPollError(e){
 note(e.message,true);
 for(const el of document.querySelectorAll('#dailyFeedback .pch-inline-feedback,.pch-toast.is-error'))el.dataset.dailyPollError='true';
}
function clearDailyPollErrors(){
 for(const el of document.querySelectorAll('[data-daily-poll-error]'))el.remove();
}
function beginDailyOperation(kind,baseline){
 dailyOperation={kind,plan:baseline,started:Date.now(),seenRunning:false};
 renderDailyOperation(current||{});schedulePolling(0);
}
function renderDailyOperation(s){
 const el=document.getElementById('dailyJobFeedback');if(!el||!dailyOperation)return;
 const op=dailyOperation,job=s.job||{},plan=s.daily_plan||{};
 let text='',failed=false,finished=false;
 if(job.running){
  op.seenRunning ||= job.kind===op.kind;
  text=job.kind===op.kind?(op.kind==='daily_apply'?'正在发布到 Plexamp…':'正在生成预览，完成后请确认发布。'):'正在等待当前任务完成…';
 }else if(op.kind==='daily_preview'&&plan.id&&String(plan.id)!==op.plan){
  const blocked=dailyBlockReasons(plan);text=blocked.length?'预览已生成，请先处理上方的发布检查。':'预览已生成，可以确认发布。';failed=!!blocked.length;finished=true;
 }else if(op.kind==='daily_apply'&&String(plan.id)===op.plan&&plan.applied){
  text='已发布到 Plexamp。';finished=true;
 }else if(op.seenRunning||Date.now()-op.started>15000){
  text=job.error||job.message||'任务已结束，但未确认完成。请到运行状态查看原因。';
  // A generic completed job alone cannot prove the requested plan was applied.
  text='未确认本次'+(op.kind==='daily_apply'?'发布':'预览')+'成功。'+text;failed=true;finished=true;
 }else{text=op.kind==='daily_apply'?'已提交发布，正在检查进度…':'已提交生成，正在检查进度…';}
 el.textContent=String(text);el.classList.toggle('is-error',failed);
 if(finished)dailyOperation=null;
}
