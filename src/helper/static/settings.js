'use strict';
const $=id=>document.getElementById(id);
let busy=false,current=null,plexPin='',plexTimer=null,plexDeadline=0,plexProfiles=[],activeProfile='default',automationState={},webhookTimer=null;
function note(t,e=false){PCHUI.notify(t,{error:e});}
function markSaved(button){button.textContent='已保存';button.classList.add('is-saved');}
function markDirty(button){button.textContent='保存更改';button.classList.remove('is-saved');}
async function request(path,method='GET',body){return PCHAuth.request(path,method,body);}
async function post(path,b){return PCHAuth.post(path,b);}
async function action(fn){if(busy)return;busy=true;try{await PCHUI.run(fn);}catch(e){note(e.message,true);}finally{busy=false;}}
const settingsAnchors={accounts:'currentUser',learning:'people',system:'settings-system'};
function showSettingsPanel(name,updateHash=true){
 const target=$(settingsAnchors[name]||settingsAnchors.accounts);
 if(updateHash)history.replaceState(null,'','#'+name);
 target?.scrollIntoView({block:'start'});
}
for(const id of ['dailyAutomationHour','smartAutomationHour','libraryAutomationHour']){for(let hour=0;hour<24;hour++){const option=document.createElement('option');option.value=String(hour);option.textContent=String(hour).padStart(2,'0')+':00';$(id).append(option);}}
function renderSections(rows,saved){
 const select=$('section');select.replaceChildren();const seen=new Set();
 for(const row of Array.isArray(rows)?rows:[]){if(row.type&&row.type!=='artist')continue;const id=String(row.id??row.key??'').trim();if(!/^\d+$/.test(id)||seen.has(id))continue;seen.add(id);const o=document.createElement('option');o.value=id;o.textContent=(row.title||'音乐资料库')+' · '+id;select.append(o);}
 const value=String(saved||'');if(value&&!seen.has(value)){const o=document.createElement('option');o.value=value;o.textContent='已保存的音乐资料库 · '+value;select.append(o);}
 if(!value){const o=document.createElement('option');o.value='';o.textContent=seen.size?'请选择音乐资料库':'请先连接 Plex';select.prepend(o);}
 select.value=value;$('sectionHint').textContent=seen.size?'已读取 '+seen.size+' 个音乐资料库。':'尚未读取资料库。';
}
function renderOfficialSections(rows,saved){
 const select=$('officialSection');select.replaceChildren();const seen=new Set();
 for(const row of rows||[]){if(row.type&&row.type!=='artist')continue;const id=String(row.id??row.key??'');if(!id||seen.has(id))continue;seen.add(id);const o=document.createElement('option');o.value=id;o.textContent=row.name||row.title||'音乐资料库';select.append(o);}
 const value=String(saved||'');if(value&&!seen.has(value)){const o=document.createElement('option');o.value=value;o.textContent='音乐资料库 · '+value;select.append(o);seen.add(value);}
 if(!value&&seen.size){const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='请选择音乐资料库';placeholder.disabled=true;select.prepend(placeholder);}select.value=value;$('plexLibraryPanel').hidden=!seen.size||!!value;$('savePlexLibrary').hidden=true;
}
function renderSavedConnection(saved){
 const value=saved||{},configured=!!value.configured,state=value.state||'not_configured';const libraryReady=!!value.library?.id;const visibleState=configured&&!libraryReady?'library_required':state;
 const labels={not_configured:'未连接',library_required:'请选择音乐库',saved:'已保存',online:'在线',unreachable:'暂时离线',auth_invalid:'需重新连接'};
 $('plexState').textContent=labels[visibleState]||'已保存';$('plexState').dataset.state=visibleState;
 $('plexConnectionLabel').textContent=labels[visibleState]||'管理';
 $('plexSavedSummary').hidden=!configured;$('connectPlex').textContent=configured?'重新连接':'连接 Plex';$('disconnectPlex').hidden=!configured;
 const showTools=!configured||!libraryReady||state==='auth_invalid';$('plexConnectionTools').hidden=!showTools;$('plexConnectionTools').open=showTools;
 if(!configured)return;
 $('savedAccount').textContent=value.account?.title||value.account?.username||'Plex 账户';
 $('savedServer').textContent=value.server?.name||'Plex 服务器';
 $('savedLibrary').textContent=value.library?.name||value.library?.id||'尚未选择';
}
function profileDisplayName(row){return row?.account?.username||row?.account?.title||row?.name||'Plex 账户';}
function profileLabel(row){return profileDisplayName(row)+' · '+(row?.library?.name||row?.library?.id||'选择音乐库');}
function renderWebhook(webhook){
 const value=webhook||{};$('webhookUrl').textContent=location.origin+(value.endpoint_path||'/api/plex/webhook');
 const connected=value.global_connected??value.connected,lastReceived=value.global_last_received_at??value.last_received_at;
 $('webhookMessage').textContent=connected?'接收正常':lastReceived?'等待验证':'需要设置';
 $('webhookLast').textContent=lastReceived?'最近收到 · '+new Date(lastReceived*1000).toLocaleString('zh-CN',{hour12:false}):'尚未收到播放事件';
}
async function copyWebhookAddress(){
 const value=$('webhookUrl').textContent.trim();
 if(window.isSecureContext&&navigator.clipboard?.writeText){try{await navigator.clipboard.writeText(value);return;}catch(_e){}}
 const area=document.createElement('textarea');area.value=value;area.setAttribute('readonly','');area.style.position='fixed';area.style.left='-9999px';document.body.append(area);area.focus();area.select();
 let copied=false;try{copied=document.execCommand('copy');}finally{area.remove();}
 if(!copied)throw Error('浏览器未允许自动复制，请长按上方地址手动复制。');
}
async function refreshWebhookStatus(){const status=await responseJson('/api/status');renderWebhook(status.webhook);}
function startWebhookPolling(){if(webhookTimer)return;webhookTimer=setInterval(()=>{if(document.visibilityState==='visible'&&!$('settings-accounts').hidden)refreshWebhookStatus().catch(()=>{});},5000);}
function renderProfiles(data){
 plexProfiles=Array.isArray(data?.items)?data.items:[];const requested=PCHAuth.profile();const enabled=plexProfiles.filter(row=>row.enabled);const fallback=String(data?.active_profile_id||enabled[0]?.id||'default');activeProfile=enabled.some(row=>row.id===requested)?requested:fallback;if(activeProfile!==requested)PCHAuth.setProfile(activeProfile);
 renderManagedUsers();
}
function render(s,saved){
 current=s;const c=s.settings||{},d=s.daily_policy||{};
 $('version').textContent='v'+s.version;$('plexUrl').value=c.plex_url||'';$('accountLabel').value=c.account_label||'';$('plexToken').value='';
 $('tokenHint').textContent=c.token_present?'Plex Token 已保存；不修改时留空。':'尚未保存 Plex Token。';
 renderSavedConnection(saved);renderWebhook(s.webhook);
 renderSections(s.plex_connection?.sections,c.section);renderOfficialSections(s.plex_connection?.sections,c.section);
 $('dailySize').value=d.size??30;$('rediscoveryDays').value=d.rediscovery_days??90;$('dailyAvoidDays').value=d.daily_avoid_days??21;$('favoritePercent').value=d.favorite_percent??20;$('artistCap').value=d.artist_cap??2;$('accountName').textContent=PCHAuth.status().username||'admin';
}
function renderAutomation(value){
 const schedule=value||{};automationState=schedule;
 $('dailyAutomationHour').value=String(schedule.daily?.hour??6);
 $('smartIntervalDays').value=String(schedule.smart?.interval_days??7);$('smartAutomationHour').value=String(schedule.smart?.hour??3);
 $('libraryAutomationEnabled').checked=!!schedule.library?.enabled;$('libraryAutomationHour').value=String(schedule.library?.hour??0);
}
function automationPayload(){return {
 daily:{...automationState.daily,hour:Number($('dailyAutomationHour').value)},
 smart:{...automationState.smart,interval_days:Number($('smartIntervalDays').value),hour:Number($('smartAutomationHour').value)},
 library:{enabled:$('libraryAutomationEnabled').checked,hour:Number($('libraryAutomationHour').value)},
};}
async function responseJson(path){
 const response=await request(path);let data={};
 try{data=await response.json();}catch(_e){}
 if(!response.ok)throw Error(data.error||('读取失败：'+path));
 return data;
}
async function refresh(){
 const profiles=await responseJson('/api/plex/profiles');renderProfiles(profiles);
 const [libraryResult,savedResult,statusResult,policyResult,automationResult]=await Promise.allSettled([
  responseJson('/api/plex/profiles/libraries?profile_id='+encodeURIComponent(activeProfile)),responseJson('/api/plex/saved'),responseJson('/api/status'),responseJson('/api/daily/policy'),responseJson('/api/automation')
 ]);
 const saved=savedResult.status==='fulfilled'?savedResult.value:{configured:false,state:'not_configured'};
 renderSavedConnection(saved);
 if(statusResult.status!=='fulfilled')throw statusResult.reason;
 const s=statusResult.value;
 s.daily_policy=policyResult.status==='fulfilled'?policyResult.value:{};
 render(s,saved);renderOfficialSections(libraryResult.status==='fulfilled'?libraryResult.value.items:[],saved.library?.id||'');
 if(automationResult.status==='fulfilled')renderAutomation(automationResult.value);
}
function profileKind(kind){return {owner:'管理员',home:'家庭成员',shared:'共享朋友'}[kind]||'Plex 用户';}
function controlsCell(profile,key,label){
 const cell=document.createElement('label');cell.className='settings-control-cell';
 const caption=document.createElement('span');caption.className='settings-mobile-label';caption.textContent=label;
 const input=document.createElement('input');input.type='checkbox';input.className='toggle';input.checked=profile.controls?.[key]!==false;
 input.disabled=!profile.enabled;input.setAttribute('aria-label',profileLabel(profile)+' '+label);
 input.onchange=async()=>{const next=input.checked;input.disabled=true;
  try{const result=await post('/api/plex/profiles/control',{profile_id:profile.id,key,enabled:next});profile.controls=result.controls;}
  catch(error){input.checked=!next;note(error.message,true);}
  finally{input.disabled=!profile.enabled;}
 };
 cell.append(caption,input);return cell;
}
function renderManagedUsers(){
 const list=$('managedUserList');list.replaceChildren();
 for(const row of plexProfiles){
  const line=document.createElement('div');line.className='settings-user-row';
  const person=document.createElement('div');person.className='settings-person';
  const avatar=document.createElement('span');avatar.className='settings-avatar';avatar.textContent=profileDisplayName(row).trim().slice(0,1).toUpperCase();
  const text=document.createElement('div');const name=document.createElement('strong');name.textContent=profileLabel(row);
  text.append(name);
  if(row.removal){const badge=document.createElement('span');badge.className='settings-user-status';badge.textContent=row.removal.status==='needs_attention'?'清理需处理':row.removal.status==='legacy_cleanup_required'?'旧档案待清理':'正在移除';badge.title=row.removal.error||'';text.append(badge);}
  else if(row.daily_status?.status==='needs_attention'){const badge=document.createElement('span');badge.className='settings-user-status';badge.textContent='推荐需核对';badge.title=row.daily_status.reason||'';text.append(badge);}
  person.append(avatar,text);
  line.append(person,controlsCell(row,'learning','播放学习'),controlsCell(row,'daily','每日推荐'),controlsCell(row,'smart','智能歌单'));
  const actions=document.createElement('div');actions.className='settings-actions profile-actions';
  if(row.id!=='default'){
   const remove=document.createElement('button');remove.type='button';remove.className=row.removal?'secondary':'danger';remove.textContent=row.removal?(row.removal.status==='needs_attention'||row.removal.status==='legacy_cleanup_required'?'重试':'处理中'):'移除';remove.disabled=!!row.removal&&!['needs_attention','legacy_cleanup_required'].includes(row.removal.status);
   remove.onclick=()=>action(async()=>{if(!row.removal&&!await PCHUI.confirm('移除“'+profileLabel(row)+'”？\n程序在这个用户及曲库创建的 Plex 歌单会删除；用户自己创建的歌单不会动。',{confirmText:'移除并清理'}))return;await post('/api/plex/profiles/remove',{profile_id:row.id,confirm:true});if(row.id===activeProfile)PCHAuth.setProfile('default');await refresh();});actions.append(remove);
  }
  line.append(actions);
  list.append(line);
 }
}
function renderRecipients(rows,ownerProfileId,warnings=[]){
 const list=$('profileRecipientList');list.replaceChildren();
 $('profileRecipientLibraries').hidden=true;$('profileRecipientLibraries').replaceChildren();list.hidden=false;$('findPeople').textContent='刷新名单';
 const available=(rows||[]).filter(row=>!row.existing_profile_id);
 if(!available.length){const p=document.createElement('p');p.className='dialog-empty';p.textContent='没有其他可添加用户';list.append(p);}
 for(const row of available){
  const line=document.createElement('div');line.className='settings-user-row';const person=document.createElement('div');person.className='settings-person';
  const avatar=document.createElement('span');avatar.className='settings-avatar';avatar.textContent=(row.title||row.username||'P').trim().slice(0,1).toUpperCase();
  const text=document.createElement('div');const strong=document.createElement('strong');strong.textContent=row.title||row.username||'Plex 用户';const kind=document.createElement('span');kind.textContent=row.kind_label||'Plex 用户';text.append(strong,kind);person.append(avatar,text);
  const button=document.createElement('button');button.type='button';button.className='secondary';button.textContent='添加';
  button.onclick=()=>action(async()=>{const data=await post('/api/plex/recipients/libraries',{owner_profile_id:ownerProfileId,kind:row.kind,user_id:String(row.id)});renderRecipientLibraries(row,ownerProfileId,data);});
  line.append(person,button);list.append(line);
 }
 for(const message of warnings){const p=document.createElement('p');p.className='recipient-warning';p.textContent=message;list.append(p);}
}
function renderRecipientLibraries(person,ownerProfileId,data){
 const box=$('profileRecipientLibraries');box.replaceChildren();$('profileRecipientList').hidden=true;box.hidden=false;$('findPeople').textContent='返回';
 const title=document.createElement('strong');title.textContent=data.account?.username||person.title||person.username||'Plex 用户';box.append(title);
 for(const library of data.libraries||[]){const line=document.createElement('div');line.className='settings-user-row';const name=document.createElement('strong');name.textContent=library.name||('音乐资料库 · '+library.id);const add=document.createElement('button');add.type='button';add.className='primary';add.textContent='添加';add.onclick=()=>action(async()=>{const endpoint=person.kind==='home'?'/api/plex/recipients/home/import':'/api/plex/recipients/shared/import';const result=await post(endpoint,{owner_profile_id:ownerProfileId,user_id:String(person.id),library_id:String(library.id)});PCHAuth.setProfile(result.profile.id);$('addUserDialog').close();await refresh();note(result.message);});line.append(name,add);box.append(line);}
 if(!(data.libraries||[]).length){const empty=document.createElement('p');empty.className='dialog-empty';empty.textContent='没有可用音乐库';box.append(empty);}
}
async function loadAvailablePeople(){$('profileRecipientList').innerHTML='<p class="dialog-empty">正在读取…</p>';const data=await responseJson('/api/plex/recipients');renderRecipients(data.items||[],data.owner_profile_id,data.warnings||[]);}
$('createProfile').onsubmit=e=>{e.preventDefault();action(async()=>{const name=$('newProfileName').value.trim();if(!name)throw Error('请填写新档案名称。');const result=await post('/api/plex/profiles/create',{name});PCHAuth.setProfile(result.profile.id);$('newProfileName').value='';$('addUserDialog').close();note(result.message+' 请点击“连接 Plex”。');await refresh();});};
$('openAddUser').onclick=()=>{$('addUserDialog').showModal();action(loadAvailablePeople);};
$('closeAddUser').onclick=()=>$('addUserDialog').close();
$('findPeople').onclick=()=>action(loadAvailablePeople);
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
async function saveOfficialLibrary(){const select=$('officialSection'),previous=String(plexProfiles.find(row=>row.id===activeProfile)?.library?.id||'');if(!select.value)throw Error('请选择音乐资料库。');try{const result=await post('/api/plex/profiles/library',{profile_id:activeProfile,library_id:select.value});PCHAuth.setProfile(result.profile.id);await refresh();note('音乐资料库已保存。');}catch(error){select.value=previous;throw error;}}
$('savePlexLibrary').onclick=()=>action(saveOfficialLibrary);
$('officialSection').onchange=()=>{$('savePlexLibrary').hidden=$('officialSection').value===String(plexProfiles.find(row=>row.id===activeProfile)?.library?.id||'');};
$('plexForm').onsubmit=e=>{e.preventDefault();action(async()=>{if(!$('section').value)throw Error('请先选择音乐资料库。');const r=await post('/api/settings',{plex_url:$('plexUrl').value.trim(),plex_token:$('plexToken').value.trim(),section:$('section').value.trim(),account_label:$('accountLabel').value.trim()});note(r.message);await refresh();});};
$('testPlex').onclick=()=>action(async()=>{const r=await post('/api/plex/check',{});renderSections(r.sections,$('section').value);$('plexState').textContent='已连接 · '+(r.server||'Plex');note('连接成功。');await refresh();});
async function saveDailyPolicy(){await post('/api/daily/policy',{size:Number($('dailySize').value),rediscovery_days:Number($('rediscoveryDays').value),daily_avoid_days:Number($('dailyAvoidDays').value),favorite_percent:Number($('favoritePercent').value),artist_cap:Number($('artistCap').value)});markSaved($('dailySave'));await refresh();}
$('dailyForm').onsubmit=e=>{e.preventDefault();action(saveDailyPolicy);};
$('dailyForm').addEventListener('input',()=>markDirty($('dailySave')));
for(const id of ['dailyAutomationHour','smartIntervalDays','smartAutomationHour','libraryAutomationEnabled','libraryAutomationHour']){$(id).onchange=()=>action(async()=>{try{const saved=await post('/api/automation',automationPayload());renderAutomation(saved);}finally{await refresh();}});}
$('copyWebhook').onclick=()=>action(async()=>{await copyWebhookAddress();note('地址已复制。');});
$('passwordForm').onsubmit=e=>{e.preventDefault();action(async()=>{const a=$('newPassword').value,b=$('confirmPassword').value;if(a!==b)throw Error('两次输入的新密码不一致');const r=await post('/api/auth/password',{current_password:$('currentPassword').value,new_password:a,confirm_password:b});$('currentPassword').value='';$('newPassword').value='';$('confirmPassword').value='';note(r.message+'，其它旧登录会话已退出。');});};
async function boot(){const panel=location.hash.slice(1);try{await refresh();await resumePlexLogin();}catch(e){note(e.message,true);}if(settingsAnchors[panel])showSettingsPanel(panel,false);startWebhookPolling();}
window.addEventListener('pch-auth-ready',boot);window.addEventListener('pch-auth-login',boot);window.addEventListener('pch-auth-logout',()=>{plexPin='';stopPlexPolling();});
window.addEventListener('pagehide',()=>{stopPlexPolling();clearInterval(webhookTimer);webhookTimer=null;});
window.addEventListener('visibilitychange',()=>{if(document.hidden)stopPlexPolling();else if(plexPin)schedulePlexPolling(0);});
