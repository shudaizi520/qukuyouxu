'use strict';
const $=id=>document.getElementById(id);
let busy=false,current=null,plexPin='',plexTimer=null,plexDeadline=0,plexProfiles=[],activeProfile='default',batchPlans={},webhookTimer=null,learningEnabled=true,learningAccount='',webhookStatus={};
function note(t,e=false){PCHUI.notify(t,{error:e});}
function markSaved(button){button.textContent='已保存';button.classList.add('is-saved');}
function markDirty(button){button.textContent='保存更改';button.classList.remove('is-saved');}
async function request(path,method='GET',body){return PCHAuth.request(path,method,body);}
async function post(path,b){return PCHAuth.post(path,b);}
async function action(fn){if(busy)return;busy=true;try{await PCHUI.run(fn);}catch(e){note(e.message,true);}finally{busy=false;}}
function showSettingsPanel(name){
 for(const button of document.querySelectorAll('[data-settings-target]'))button.classList.toggle('active',button.dataset.settingsTarget===name);
 for(const panel of document.querySelectorAll('.settings-panel'))panel.hidden=panel.id!=='settings-'+name;
 history.replaceState(null,'','#'+name);
}
for(const button of document.querySelectorAll('[data-settings-target]'))button.onclick=()=>showSettingsPanel(button.dataset.settingsTarget);
for(let hour=0;hour<24;hour++){const option=document.createElement('option');option.value=String(hour);option.textContent=String(hour).padStart(2,'0')+':00';$('dailyHour').append(option);}
function renderSections(rows,saved){
 const select=$('section');select.replaceChildren();const seen=new Set();
 for(const row of Array.isArray(rows)?rows:[]){if(row.type&&row.type!=='artist')continue;const id=String(row.id??row.key??'').trim();if(!/^\d+$/.test(id)||seen.has(id))continue;seen.add(id);const o=document.createElement('option');o.value=id;o.textContent=(row.title||'音乐资料库')+' · '+id;select.append(o);}
 const value=String(saved||'');if(value&&!seen.has(value)){const o=document.createElement('option');o.value=value;o.textContent='已保存的音乐资料库 · '+value;select.append(o);}
 if(!value){const o=document.createElement('option');o.value='';o.textContent=seen.size?'请选择音乐资料库':'请先连接 Plex';select.prepend(o);}
 select.value=value;$('sectionHint').textContent=seen.size?'已读取 '+seen.size+' 个音乐资料库。':'尚未读取资料库。';
}
function renderOfficialSections(rows,saved){
 const select=$('officialSection');select.replaceChildren();let count=0;
 for(const row of rows||[]){if(row.type&&row.type!=='artist')continue;const o=document.createElement('option');o.value=String(row.id??row.key??'');o.textContent=(row.title||'音乐资料库')+' · '+o.value;select.append(o);count++;}
 const value=String(saved||'');if(!value&&count){const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='请选择音乐资料库';placeholder.disabled=true;select.prepend(placeholder);}select.value=value;$('plexLibraryPanel').hidden=!count;
}
function renderAccounts(data){
 const select=$('behaviorUser'),accounts=data.accounts||[],wanted=String(data.behavior_account_id||''),effective=wanted||(accounts.length===1?String(accounts[0].id):'');select.replaceChildren();
 const first=document.createElement('option');first.value='';first.textContent=accounts.length?'请选择 Plex 用户':'暂无可用用户';select.append(first);
 for(const row of accounts){const o=document.createElement('option');o.value=String(row.id);o.textContent=row.name+' · '+row.id;select.append(o);}
 select.value=effective;
 learningEnabled=data.behavior_enabled!==false;learningAccount=effective;$('behaviorEnabled').checked=learningEnabled;updateLearningState();
}
function updateLearningState(){
 const state=webhookStatus?.state||'not_connected';let label='已关闭';
 if(learningEnabled&&!learningAccount)label='选择账户';
 else if(learningEnabled&&state==='learning')label='学习中';
 else if(learningEnabled&&state==='connected_waiting')label='已接通';
 else if(learningEnabled)label='设置 Webhook';
 $('webhookState').textContent=label;
 $('behaviorSave').disabled=learningEnabled&&!learningAccount;
}
function renderSavedConnection(saved){
 const value=saved||{},configured=!!value.configured,state=value.state||'not_configured';const libraryReady=!!value.library?.id;const visibleState=configured&&!libraryReady?'library_required':state;
 const labels={not_configured:'未连接',library_required:'请选择音乐库',saved:'已保存',online:'在线',unreachable:'暂时离线',auth_invalid:'需重新连接'};
 $('plexState').textContent=labels[visibleState]||'已保存';$('plexState').dataset.state=visibleState;
 $('plexConnectionLabel').textContent=labels[visibleState]||'管理';
 $('plexSavedSummary').hidden=true;$('connectPlex').textContent=configured?'重新连接':'连接 Plex';$('disconnectPlex').hidden=!configured;
 const showTools=!configured||!libraryReady||state==='auth_invalid';$('plexConnectionTools').hidden=!showTools;$('plexConnectionTools').open=showTools;
 if(!configured)return;
 $('savedAccount').textContent=value.account?.title||value.account?.username||'Plex 账户';
 $('savedServer').textContent=value.server?.name||'Plex 服务器';
 $('savedLibrary').textContent=value.library?.name||value.library?.id||'尚未选择';
 $('savedHealth').textContent=!libraryReady?'请选择音乐资料库':state==='online'?'最近检查正常':state==='unreachable'?'Plex 暂时不可达，设置仍已保存':state==='auth_invalid'?'授权失效，请重新连接':'连接资料已保存';
}
function profileDisplayName(row){return row?.account?.username||row?.account?.title||row?.name||'Plex 账户';}
function renderWebhook(webhook){
 const value=webhook||{},state=value.state||'not_connected';webhookStatus=value;$('webhookUrl').textContent=location.origin+(value.endpoint_path||'/api/plex/webhook');updateLearningState();
 const messages={disabled:'已关闭',not_connected:'需要设置',connected_waiting:'已接通',learning:'接收正常'};
 $('webhookMessage').textContent=messages[state]||messages.not_connected;
 $('webhookLast').textContent=value.last_received_at?'最近收到：'+new Date(value.last_received_at*1000).toLocaleString('zh-CN',{hour12:false})+' · '+(value.last_event||'Plex 事件'):'在 Plex 保存地址后播放一首歌，收到事件后这里会自动显示“已接通”。';
}
async function copyWebhookAddress(){
 const value=$('webhookUrl').textContent.trim();
 if(window.isSecureContext&&navigator.clipboard?.writeText){try{await navigator.clipboard.writeText(value);return;}catch(_e){}}
 const area=document.createElement('textarea');area.value=value;area.setAttribute('readonly','');area.style.position='fixed';area.style.left='-9999px';document.body.append(area);area.focus();area.select();
 let copied=false;try{copied=document.execCommand('copy');}finally{area.remove();}
 if(!copied)throw Error('浏览器未允许自动复制，请长按上方地址手动复制。');
}
async function refreshWebhookStatus(){const status=await responseJson('/api/status');renderWebhook(status.webhook);}
function startWebhookPolling(){if(webhookTimer)return;webhookTimer=setInterval(()=>{if(document.visibilityState==='visible'&&!$('settings-learning').hidden)refreshWebhookStatus().catch(()=>{});},5000);}
function renderProfiles(data){
 plexProfiles=Array.isArray(data?.items)?data.items:[];activeProfile=String(data?.active_profile_id||'default');
 const select=$('plexProfile');select.replaceChildren();
 for(const row of plexProfiles){const option=document.createElement('option');option.value=row.id;const kinds={owner:'账户',home:'家庭成员',shared:'共享朋友'};option.textContent=profileDisplayName(row)+' · '+(kinds[row.kind]||'Plex');select.append(option);}
 select.value=activeProfile;select.disabled=plexProfiles.length<2;$('profileSwitcher').hidden=plexProfiles.length<2;
 const active=plexProfiles.find(row=>row.id===activeProfile);
 $('currentProfileName').textContent=active?.account?.username||profileDisplayName(active);
 $('currentAvatar').textContent=profileDisplayName(active).trim().slice(0,1).toUpperCase();
 $('profileSummary').textContent=active?((active.server?.name||'Plex')+' · '+(active.library?.name||active.library?.id||'尚未选择曲库')):'尚未连接';
 $('batchDailyTools').hidden=plexProfiles.length<2;
 renderManagedUsers();
}
function render(s,verified,saved){
 current=s;const c=s.settings||{},d=s.daily_policy||{},u=verified||{};
 $('version').textContent='v'+s.version;$('plexUrl').value=c.plex_url||'';$('accountLabel').value=c.account_label||'';$('plexToken').value='';
 $('tokenHint').textContent=c.token_present?'Plex Token 已保存；不修改时留空。':'尚未保存 Plex Token。';
 renderSavedConnection(saved);renderWebhook(s.webhook);
 renderSections(s.plex_connection?.sections,c.section);renderOfficialSections(s.plex_connection?.sections,c.section);renderAccounts(u);
 $('dailySize').value=d.size??30;$('rediscoveryDays').value=d.rediscovery_days??90;$('dailyAvoidDays').value=d.daily_avoid_days??21;$('favoritePercent').value=d.favorite_percent??20;$('artistCap').value=d.artist_cap??2;$('dailyHour').value=d.hour??6;$('accountName').textContent=PCHAuth.status().username||'admin';
 $('dailyAuto').checked=!!s.daily_settings?.enabled;$('dailyAutoStatus').textContent=$('dailyAuto').checked?'每天 '+String(d.hour??6).padStart(2,'0')+':00':'未开启';
 $('systemVersion').textContent=s.version;
}
async function responseJson(path){
 const response=await request(path);let data={};
 try{data=await response.json();}catch(_e){}
 if(!response.ok)throw Error(data.error||('读取失败：'+path));
 return data;
}
async function refresh(){
 const [profileResult,savedResult,statusResult,policyResult,accountResult,workflowResult]=await Promise.allSettled([
  responseJson('/api/plex/profiles'),responseJson('/api/plex/saved'),responseJson('/api/status'),responseJson('/api/daily/policy'),responseJson('/api/product/settings/verified'),responseJson('/api/workflow/status')
 ]);
 if(profileResult.status==='fulfilled')renderProfiles(profileResult.value);
 const saved=savedResult.status==='fulfilled'?savedResult.value:{configured:false,state:'not_configured'};
 renderSavedConnection(saved);
 if(statusResult.status!=='fulfilled')throw statusResult.reason;
 const s=statusResult.value,v=accountResult.status==='fulfilled'?accountResult.value:{};
 s.daily_policy=policyResult.status==='fulfilled'?policyResult.value:{};
 render(s,v,saved);
 const workflow=workflowResult.status==='fulfilled'?workflowResult.value.workflow?.settings:{};
 $('libraryAuto').checked=!!workflow?.enabled;$('libraryAuto').disabled=!workflow?.initialized;$('libraryAutoStatus').textContent=workflow?.enabled?'每天 00:00':workflow?.initialized?'未开启':'完成首次整理后可开启';
}
function ownerProfile(){return plexProfiles.find(row=>row.kind==='owner'&&row.token_present)||null;}
function profileKind(kind){return {owner:'管理员',home:'家庭成员',shared:'共享朋友'}[kind]||'Plex 用户';}
function renderManagedUsers(){
 const list=$('managedUserList');list.replaceChildren();
 for(const row of plexProfiles){
  const line=document.createElement('div');line.className='settings-user-row';
  const person=document.createElement('div');person.className='settings-person';
  const avatar=document.createElement('span');avatar.className='settings-avatar';avatar.textContent=profileDisplayName(row).trim().slice(0,1).toUpperCase();
  const text=document.createElement('div');const name=document.createElement('strong');name.textContent=profileDisplayName(row);const kind=document.createElement('span');kind.textContent=profileKind(row.kind);text.append(name,kind);person.append(avatar,text);
  const actions=document.createElement('div');actions.className='settings-actions';
  const open=document.createElement('button');open.type='button';open.className='secondary';open.textContent=row.id===activeProfile?'当前':'打开';open.disabled=row.id===activeProfile;
  open.onclick=()=>action(async()=>{await post('/api/plex/profiles/select',{profile_id:row.id});await refresh();showSettingsPanel('recommend');});actions.append(open);
  if(row.kind!=='owner'){
   const remove=document.createElement('button');remove.type='button';remove.className='danger';remove.textContent='移除';
   remove.onclick=()=>action(async()=>{if(!await PCHUI.confirm('停止为“'+(row.name||'这位用户')+'”生成每日推荐？\nPlex 中已有歌单会保留。',{confirmText:'移除用户'}))return;const result=await post('/api/plex/profiles/remove',{profile_id:row.id,confirm:true});await refresh();note(result.message);});actions.append(remove);
  }
  line.append(person,actions);list.append(line);
 }
}
function renderRecipients(rows,owner,warnings=[]){
 const list=$('profileRecipientList');list.replaceChildren();
 const available=(rows||[]).filter(row=>!row.existing_profile_id);
 if(!available.length){const p=document.createElement('p');p.className='dialog-empty';p.textContent='没有其他可添加用户';list.append(p);}
 for(const row of available){
  const line=document.createElement('div');line.className='settings-user-row';const person=document.createElement('div');person.className='settings-person';
  const avatar=document.createElement('span');avatar.className='settings-avatar';avatar.textContent=(row.title||row.username||'P').trim().slice(0,1).toUpperCase();
  const text=document.createElement('div');const strong=document.createElement('strong');strong.textContent=row.title||row.username||'Plex 用户';const kind=document.createElement('span');kind.textContent=row.kind_label||'Plex 用户';text.append(strong,kind);person.append(avatar,text);
  const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent=row.archived_profile_id?'重新添加':'添加';
  button.onclick=()=>action(async()=>{let result;if(row.archived_profile_id){result=await post('/api/plex/profiles/restore',{profile_id:row.archived_profile_id});}else{const endpoint=row.kind==='home'?'/api/plex/recipients/home/import':'/api/plex/recipients/shared/import';result=await post(endpoint,{owner_profile_id:owner.id,user_id:String(row.id),library_id:String(owner.library?.id||'')});}await refresh();await loadAvailablePeople();note(result.message);});
  line.append(person,button);list.append(line);
 }
 for(const message of warnings){const p=document.createElement('p');p.className='recipient-warning';p.textContent=message;list.append(p);}
}
async function loadAvailablePeople(){const owner=ownerProfile();if(!owner?.library?.id)throw Error('请先连接 Plex 并选择音乐资料库。');$('profileRecipientList').innerHTML='<p class="dialog-empty">正在读取…</p>';const data=await responseJson('/api/plex/recipients?owner_profile_id='+encodeURIComponent(owner.id));renderRecipients(data.items||[],owner,data.warnings||[]);}
$('plexProfile').onchange=()=>action(async()=>{await post('/api/plex/profiles/select',{profile_id:$('plexProfile').value});await refresh();});
$('createProfile').onsubmit=e=>{e.preventDefault();action(async()=>{const name=$('newProfileName').value.trim();if(!name)throw Error('请填写新档案名称。');const result=await post('/api/plex/profiles/create',{name});$('newProfileName').value='';note(result.message+' 请点击“连接 Plex”。');await refresh();});};
$('openAddUser').onclick=()=>{const owner=ownerProfile();if(!owner?.library?.id){$('plexConnectionTools').hidden=false;$('plexConnectionTools').open=true;note('请先选择音乐资料库。',true);$('officialSection').focus();return;}$('addUserDialog').showModal();action(loadAvailablePeople);};
$('closeAddUser').onclick=()=>$('addUserDialog').close();
$('findPeople').onclick=()=>action(loadAvailablePeople);
function renderBatch(items){const box=$('batchResults');box.hidden=false;box.replaceChildren();batchPlans={};for(const row of items||[]){const line=document.createElement('div');line.className='batch-row';const name=document.createElement('strong');name.textContent=row.name||row.profile_id;const state=document.createElement('span');state.textContent=row.status==='ready'?`可发布 · ${row.count} 首`:row.status==='published'?`已发布 · ${row.result?.written||0} 首`:row.status==='blocked'?'已暂停':row.error||'失败';line.append(name,state);box.append(line);if(row.status==='ready'&&row.plan_id)batchPlans[row.profile_id]=row.plan_id;}$('batchPublish').disabled=!Object.keys(batchPlans).length;}
$('batchPreview').onclick=()=>action(async()=>{const result=await post('/api/profiles/daily/batch-preview',{});renderBatch(result.items);note(`已检查所有启用档案，${result.ready} 个可发布。`);});
$('batchPublish').onclick=()=>action(async()=>{const count=Object.keys(batchPlans).length;if(!count)throw Error('没有可发布的预览。');if(!await PCHUI.confirm(`将为 ${count} 个 Plex 档案分别发布每日推荐。\n每个档案只使用自己的授权、曲库和播放历史。`,{confirmText:'确认批量发布'}))return;const result=await post('/api/profiles/daily/batch-publish',{confirm:true,plans:batchPlans});renderBatch(result.items);note(`已完成：${result.published} 个档案发布成功。`);});
function loginStatus(text,error=false){const e=$('plexLoginStatus');e.hidden=false;e.textContent=text;e.classList.toggle('is-error',error);}
function offerManualFallback(){const b=$('showManualFallback');if(b)b.hidden=false;}
function renderServers(rows){
 const select=$('plexServer');select.replaceChildren();
 for(const row of rows||[]){const o=document.createElement('option');o.value=row.machine;o.textContent=row.name+(row.owned?' · 我的服务器':' · 已共享');select.append(o);}
 $('plexServerPanel').hidden=!select.options.length;
}
function stopPlexPolling(){clearTimeout(plexTimer);plexTimer=null;}
function schedulePlexPolling(delay=2500){stopPlexPolling();if(!plexPin||document.hidden||Date.now()>=plexDeadline)return;plexTimer=setTimeout(pollPlex,delay);}
async function pollPlex(){
 if(!plexPin||document.hidden)return;
 if(Date.now()>=plexDeadline){stopPlexPolling();plexPin='';loginStatus('Plex 授权已超时，请重新连接。',true);return;}
 try{
  const response=await request('/api/plex/login/status?pin_id='+encodeURIComponent(plexPin));let data={};try{data=await response.json();}catch(_e){}
  if(!response.ok)throw Error(data.error||'无法读取 Plex 登录状态');
  if(data.status==='pending'){loginStatus(data.message||'等待你在 Plex 官方页面确认…');schedulePlexPolling();return;}
  stopPlexPolling();
  if(data.status==='expired'){plexPin='';loginStatus(data.message||'Plex 授权已过期，请重新连接。',true);return;}
  await resumePlexLogin();
 }catch(e){stopPlexPolling();loginStatus(e.message,true);offerManualFallback();}
}
async function resumePlexLogin(){
 const data=await post('/api/plex/login/resume',{});
 if(data.status==='idle')return;
 $('plexConnectionTools').hidden=false;$('plexConnectionTools').open=true;
 if(data.status==='pending'){
  plexPin=String(data.pin_id||'');plexDeadline=Math.min(Number(data.expires_at||0)*1000,Date.now()+10*60*1000);
  loginStatus(data.message||'等待你在 Plex 官方页面确认…');schedulePlexPolling();return;
 }
 stopPlexPolling();
 if(data.status==='expired'){plexPin='';loginStatus(data.message||'Plex 授权已过期，请重新连接。',true);return;}
 if(data.status==='selection_required'){
  plexPin=String(data.pin_id||'');renderServers(data.servers);loginStatus(data.message||'请选择要使用的 Plex 服务器。');return;
 }
 if(data.status==='connected'){
  plexPin='';renderSections(data.sections,data.section);renderOfficialSections(data.sections,data.section);$('plexServerPanel').hidden=true;
  loginStatus(data.section?'Plex 已连接。':'Plex 已连接，请选择音乐资料库。');await refresh();
 }
}
$('connectPlex').onclick=()=>action(async()=>{
 $('plexConnectionTools').hidden=false;$('plexConnectionTools').open=true;
 const popup=window.open('about:blank','plexOfficialLogin','popup,width=860,height=720');
 if(popup){popup.document.title='连接 Plex';popup.document.body.textContent='正在跳转到 Plex 官方登录…';}
 try{
  const data=await post('/api/plex/login/start',{});plexPin=String(data.pin_id||'');plexDeadline=Math.min(Number(data.expires_at||0)*1000,Date.now()+10*60*1000);
  if(popup)popup.location.replace(data.auth_url);else window.open(data.auth_url,'_blank','noopener');
  loginStatus(data.message||'请在 Plex 官方页面完成授权。');await pollPlex();
 }catch(e){stopPlexPolling();if(popup&&!popup.closed)popup.close();offerManualFallback();throw e;}
});
$('disconnectPlex').onclick=()=>action(async()=>{if(!await PCHUI.confirm('断开当前 Plex 账户？\n不会删除 Plex 中已有歌单和本地音乐缓存。',{confirmText:'断开 Plex'}))return;const result=await post('/api/plex/disconnect',{confirm:true});plexPin='';stopPlexPolling();await refresh();$('plexConnectionTools').hidden=false;$('plexConnectionTools').open=true;note(result.message);});
$('showManualFallback').onclick=()=>{const p=$('manualFallback');p.hidden=false;$('showManualFallback').hidden=true;$('advancedPlex').open=true;};
$('usePlexServer').onclick=()=>action(async()=>{
 if(!plexPin||!$('plexServer').value)throw Error('请选择 Plex 服务器。');
 const data=await post('/api/plex/login/connect',{confirm:true,pin_id:plexPin,machine:$('plexServer').value});plexPin='';stopPlexPolling();
 renderSections(data.sections,data.section);renderOfficialSections(data.sections,data.section);$('plexServerPanel').hidden=true;loginStatus(data.message);note('Plex 已连接。请选择音乐资料库并保存。');await refresh();
});
async function saveOfficialLibrary(){if(!$('officialSection').value)throw Error('请选择音乐资料库。');const option=$('officialSection').selectedOptions[0];const r=await post('/api/plex/library/select',{section:$('officialSection').value,name:option?.textContent?.split(' · ')[0]||''});note(r.message||'音乐资料库已保存。');await refresh();}
$('savePlexLibrary').onclick=()=>action(saveOfficialLibrary);
$('officialSection').onchange=()=>action(saveOfficialLibrary);
$('plexForm').onsubmit=e=>{e.preventDefault();action(async()=>{if(!$('section').value)throw Error('请先选择音乐资料库。');const r=await post('/api/settings',{plex_url:$('plexUrl').value.trim(),plex_token:$('plexToken').value.trim(),section:$('section').value.trim(),account_label:$('accountLabel').value.trim()});note(r.message);await refresh();});};
$('testPlex').onclick=()=>action(async()=>{const r=await post('/api/plex/check',{});renderSections(r.sections,$('section').value);$('plexState').textContent='已连接 · '+(r.server||'Plex');note('连接成功。');await refresh();});
async function saveDailyPolicy(){await post('/api/daily/policy',{size:Number($('dailySize').value),rediscovery_days:Number($('rediscoveryDays').value),daily_avoid_days:Number($('dailyAvoidDays').value),favorite_percent:Number($('favoritePercent').value),artist_cap:Number($('artistCap').value),hour:Number($('dailyHour').value)});markSaved($('dailySave'));await refresh();}
$('dailyForm').onsubmit=e=>{e.preventDefault();action(saveDailyPolicy);};
$('dailyForm').addEventListener('input',()=>markDirty($('dailySave')));
$('dailyHour').onchange=()=>action(saveDailyPolicy);
$('dailyAuto').onchange=()=>{const enabled=$('dailyAuto').checked;action(async()=>{try{const r=await post('/api/daily/schedule',{enabled});note(r.message);}catch(error){$('dailyAuto').checked=!enabled;throw error;}finally{await refresh();}});};
$('libraryAuto').onchange=()=>{const enabled=$('libraryAuto').checked;action(async()=>{try{if(enabled&&!await PCHUI.confirm('开启每天 00:00 检查新增歌曲？',{confirmText:'开启'})){$('libraryAuto').checked=false;return;}const r=await post('/api/workflow/schedule',{enabled,confirm:true});note(r.message);}catch(error){$('libraryAuto').checked=!enabled;throw error;}finally{await refresh();}});};
$('behaviorForm').onsubmit=e=>{e.preventDefault();action(async()=>{await post('/api/product/settings/verified',{behavior_enabled:$('behaviorEnabled').checked,behavior_account_id:$('behaviorUser').value});markSaved($('behaviorSave'));await refresh();});};
$('behaviorForm').addEventListener('input',()=>markDirty($('behaviorSave')));
$('behaviorEnabled').onchange=()=>{learningEnabled=$('behaviorEnabled').checked;updateLearningState();};
$('behaviorUser').onchange=()=>{learningAccount=$('behaviorUser').value;updateLearningState();};
$('copyWebhook').onclick=()=>action(async()=>{await copyWebhookAddress();note('地址已复制。');});
$('webhookHelpToggle').onclick=()=>{$('webhookHelp').hidden=!$('webhookHelp').hidden;};
$('passwordForm').onsubmit=e=>{e.preventDefault();action(async()=>{const a=$('newPassword').value,b=$('confirmPassword').value;if(a!==b)throw Error('两次输入的新密码不一致');const r=await post('/api/auth/password',{current_password:$('currentPassword').value,new_password:a,confirm_password:b});$('currentPassword').value='';$('newPassword').value='';$('confirmPassword').value='';note(r.message+'，其它旧登录会话已退出。');});};
async function boot(){const panel=location.hash.slice(1);if(['accounts','recommend','learning','automation','system'].includes(panel))showSettingsPanel(panel);try{await refresh();await resumePlexLogin();}catch(e){note(e.message,true);}startWebhookPolling();}
window.addEventListener('pch-auth-ready',boot);window.addEventListener('pch-auth-login',boot);window.addEventListener('pch-auth-logout',()=>{plexPin='';stopPlexPolling();});
window.addEventListener('pagehide',()=>{stopPlexPolling();clearInterval(webhookTimer);webhookTimer=null;});
window.addEventListener('visibilitychange',()=>{if(document.hidden)stopPlexPolling();else if(plexPin)schedulePlexPolling(0);});
