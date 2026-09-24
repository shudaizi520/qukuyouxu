'use strict';const recommendationReasonsVisible='recommendationReasonsVisible';const $=id=>document.getElementById(id);let current=null,timer=null,busy=false,polling=false,lastPlan='',dailyOperation=null;
const ACTIVE_POLL_MS=3000,IDLE_POLL_MS=45000;
function note(t,e=false){PCHUI.notify(t,{error:e});}
async function request(path,method='GET',body){return PCHAuth.request(path,method,body);}async function post(path,body){return PCHAuth.post(path,body);}
function n(v){return Number.isFinite(Number(v))?Number(v).toLocaleString('zh-CN'):'0';}function time(v){return v?new Date(v*1000).toLocaleString('zh-CN',{hour12:false}):'尚无';}
async function action(fn){if(busy)return;busy=true;note('');try{await PCHUI.run(fn);try{await refresh();}catch(e){dailyPollError(e);}}catch(e){note(e.message,true);}finally{busy=false;schedulePolling();}}
function setupNeeded(s){const c=s.settings||{};return !(c.plex_url&&c.token_present&&c.section);}
function addSong(row,index){
 const el=document.createElement('div');el.className='song';
 const pos=document.createElement('span');pos.className='song-index';pos.textContent=String(index+1).padStart(2,'0');
 const left=document.createElement('div');const title=document.createElement('div');title.className='song-title';title.textContent=row.title||'未命名歌曲';left.append(title);
 const meta=document.createElement('div');meta.className='song-meta';meta.textContent=(row.artist||'未知歌手')+(row.album?' · '+row.album:'');left.append(meta);
 const reasonRows=row.reasons?.length?row.reasons:[row.bucket||row.source_bucket||'按当前推荐规则选入'];const why=document.createElement('div');why.className='reasons';why.textContent='推荐原因：'+reasonRows.join(' · ');left.append(why);
 const bucket=document.createElement('span');bucket.className='bucket';bucket.textContent=row.bucket||'推荐';
 const menu=document.createElement('details');menu.className='song-menu';const summary=document.createElement('summary');summary.setAttribute('aria-label','歌曲操作');summary.textContent='•••';menu.append(summary);
 const actions=document.createElement('div');actions.className='song-menu-items';const avoid=document.createElement('button');avoid.type='button';avoid.textContent='不再推荐';avoid.onclick=()=>action(async()=>{await post('/api/feedback',{kind:'track',id:String(row.id),value:'avoid'});note('已记录，下次生成时会排除这首歌。');});actions.append(avoid);
 if(row.artist){const artist=document.createElement('button');artist.type='button';artist.textContent='少推这个歌手';artist.onclick=()=>action(async()=>{if(!await PCHUI.confirm('以后减少 '+row.artist+' 的歌曲？当天通常最多保留 1 首。'))return;await post('/api/feedback',{kind:'artist',artist:row.artist,value:'avoid'});note('已记录；下次生成时会明显降权，通常最多 1 首。');});actions.append(artist);}
 menu.append(actions);el.append(pos,left,bucket,menu);$('songs').append(el);
}
function setFlow(stage){for(const [id,n] of [['flowGenerate',1],['flowPreview',2],['flowPublish',3]]){$(id).classList.toggle('active',n===stage);$(id).classList.toggle('done',n<stage);}}
function render(s){
 current=s;clearDailyPollErrors();renderDailyOperation(s);
 const need=setupNeeded(s);$('setupPrompt').hidden=!need;$('dailyArea').hidden=need;
 const job=s.job||{},running=!!job.running,cfg=s.daily_settings||{},plan=s.daily_plan||{},published=s.daily_published||null,managed=s.daily_managed||null,repair=s.daily_repair||null;
 const previewReady=!!(plan.id&&!plan.applied),source=previewReady?plan:(published||plan),isPublished=!previewReady&&!!published,blocking=dailyBlockReasons(plan),publishable=previewReady&&!blocking.length;
 const active=s.active_profile||{},library=active.library||{},profileParts=[active.name,library.name].filter(Boolean);$('activeProfileLabel').textContent=profileParts.join(' · ');
 $('repairCard').hidden=!repair||need;$('repairDaily').disabled=running||!repair;
 if(repair)$('repairText').textContent='上次发布没有完成，请点击“修复上次发布”后再继续。';
 $('generate').hidden=previewReady;$('generate').disabled=need||running;
 $('generate').textContent=running&&job.kind==='daily_preview'?'正在生成…':managed?'生成新一批':'生成今日歌单';
 $('fullRefresh').hidden=!previewReady;$('fullRefresh').disabled=need||running;$('fullRefresh').textContent='换一批';
 $('publish').hidden=!previewReady;$('publish').disabled=running||!publishable;
 $('publish').textContent=running&&job.kind==='daily_apply'?'正在发布…':'发布到 Plexamp';
 $('targetCount').textContent=n(cfg.size??50);$('actualCount').textContent=n(source.items?.length||source.count||0);$('favoriteCount').textContent='≤'+n(Math.floor((cfg.size??50)*(cfg.favorite_percent??20)/100));$('avoidDays').textContent=n(source.stats?.daily_avoid_window_days??cfg.daily_avoid_days??21)+'天';
 renderDailyNotices(source,blocking);$('bucketSummary').replaceChildren();
 for(const [k,v] of Object.entries(source.stats?.bucket_counts||{})){const x=document.createElement('span');x.textContent=k+' '+v+' 首';$('bucketSummary').append(x);}
 if(source.stats)$('favoriteCount').textContent=n(source.stats.favorite_selected_count||0)+' / '+n(Math.floor((cfg.size??50)*(cfg.favorite_percent??20)/100));
 $('songs').replaceChildren();(source.items||[]).forEach(addSong);$('emptySongs').hidden=!!(source.items||[]).length;
 if(!$('emptySongs').hidden)$('emptySongs').textContent=isPublished?'已发布 '+n(source.count||0)+' 首':'点击“生成今日歌单”开始';
 $('dailyTitle').textContent='今日歌单';
 if(previewReady&&blocking.length){$('dailyState').textContent='暂不可发布';$('dailyMessage').textContent='请重新生成后再试。';setFlow(2);}
 else if(previewReady){$('dailyState').textContent='待发布';$('dailyMessage').textContent=n(plan.items?.length||cfg.size||50)+' 首歌曲已准备好，发布后会更新 Plex 中的“每日推荐”。';setFlow(2);}
 else if(isPublished||plan.applied){$('dailyState').textContent='已发布';$('dailyMessage').textContent='已更新 Plex 中的“每日推荐”。';setFlow(3);}
 else{$('dailyState').textContent='待生成';$('dailyMessage').textContent='先生成并预览，确认发布后才会修改 Plex。';setFlow(1);}
 if(lastPlan!==String(source.id||source.plan_id||'')){lastPlan=String(source.id||source.plan_id||'');window.scrollTo({top:0,behavior:'smooth'});}
}
async function refresh(){if(polling)return current;polling=true;try{const s=await(await request('/api/status')).json();render(s);return s;}finally{polling=false;}}
$('generate').onclick=()=>action(async()=>{const baseline=String(current?.daily_plan?.id||'');await post('/api/jobs/daily_preview',{});beginDailyOperation('daily_preview',baseline);});
$('fullRefresh').onclick=()=>action(async()=>{const baseline=String(current?.daily_plan?.id||'');await post('/api/jobs/daily_preview',{force_full:true});beginDailyOperation('daily_preview',baseline);});
$('publish').onclick=()=>action(async()=>{const p=current?.daily_plan;if(!p?.id)return;await post('/api/jobs/daily_apply',{confirm:true,plan_id:p.id});beginDailyOperation('daily_apply',String(p.id));});
$('repairDaily').onclick=()=>action(async()=>{const r=current?.daily_repair;if(!r?.snapshot_id)return;if(!await PCHUI.confirm('确认安全修复上次每日推荐？'))return;await post('/api/jobs/daily_repair',{confirm:true,snapshot_id:r.snapshot_id});note('正在修复。');});
function renderDailyNotices(plan,blocking){
 const meta=document.getElementById('dailyMeta');
 meta.replaceChildren();meta.hidden=true;
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
 document.getElementById('warnings').replaceChildren();
}

function dailyBlockReasons(plan){
 const raw=plan.blocked;
 if(Array.isArray(raw))return raw.filter(Boolean).map(x=>typeof x==='string'?x:(x.message||x.reason||'暂时无法发布'));
 if(typeof raw==='string')return raw.trim()?[raw]:[];
 return raw?['暂时无法发布，请重新生成。']:[];
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
window.addEventListener('pch-profile-change',startPolling);

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
  const blocked=dailyBlockReasons(plan);text=blocked.length?'预览已生成，但暂时无法发布。':'预览已生成，可以直接发布。';failed=!!blocked.length;finished=true;
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
