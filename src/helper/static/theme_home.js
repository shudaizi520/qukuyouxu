/* Theme choices and provenance are part of the existing main workflow. */
(()=>{
 'use strict';
 const get=id=>document.getElementById(id);
 let profileKey='',dirty=false,evidenceCategory='',nextOffset=null,onlyNew=true;
 const say=(parent,tag,text,cls)=>{const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;parent.append(e);return e;};
 function safeQQ(value){try{const u=new URL(value);if(u.protocol==='https:'&&['y.qq.com','i.y.qq.com'].includes(u.hostname)&&!u.username&&!u.password)return u.href;}catch{}return null;}
 window.renderThemeExtras=data=>{
  const t=data.workflow?.theme;if(!t)return;const s=t.settings||{};
  const signature=JSON.stringify([s.selected,s.enabled,t.topics]);
  const running=!!data.workflow?.job?.running;
  const updateCount=()=>{get('themeProfileNote').textContent='已选 '+get('themeChoices').querySelectorAll('input:checked').length+' 项'+(dirty?' · 未保存':'');get('saveThemes').disabled=running||!dirty;};
  if(profileKey!==signature&&!dirty){
   profileKey=signature;get('themeChoices').replaceChildren();
   const category=name=>['网络热歌','KTV金曲','综艺现场','影视金曲','合唱精选'].includes(name)?'主题':['开车精选','运动精选','工作陪伴','睡前舒缓'].includes(name)?'场景':['伤感情歌','治愈陪伴','甜蜜情歌'].includes(name)?'心情':'更多';
   const groups=new Map();
   for(const topic of t.topics||[]){const kind=category(topic.name);if(!groups.has(kind))groups.set(kind,[]);groups.get(kind).push(topic);}
   for(const [name,topics] of groups){
    const row=say(get('themeChoices'),'div','','theme-group');say(row,'span',name,'theme-group-name');
    const choices=say(row,'div','','theme-group-options');
    for(const topic of topics){
     const label=document.createElement('label'),box=document.createElement('input');box.type='checkbox';box.value=topic.key;box.checked=(s.selected||[]).includes(topic.key);box.dataset.topicName=topic.name;
     box.onchange=()=>{dirty=true;updateCount();};label.append(box);say(label,'span',topic.name);choices.append(label);
    }
   }
   get('themeEnabled').checked=!!s.enabled;
  }
  for(const input of get('themeChoices').querySelectorAll('input')){
   input.disabled=running;
   const label=input.parentElement;label.querySelector('.theme-missing')?.remove();
   if((t.unavailable||[]).some(x=>x.key===input.value||x.name===input.dataset.topicName))say(label,'small','缺来源','theme-missing');
  }
  get('themeEnabled').disabled=running;updateCount();
  const unavailable=t.unavailable||[];get('themeUnavailable').hidden=!unavailable.length;
  get('themeUnavailable').textContent=unavailable.length?unavailable.map(x=>x.name).join('、')+'暂未找到来源，其他主题可继续整理。':'';
  const p=t.progress,show=running&&data.workflow.state?.phase==='planning'&&p;
  get('themeRunSummary').hidden=!show;
  get('themeRunSummary').textContent=show?'正在读取 '+p.title+' · 第 '+p.page+'/'+p.max_pages+' 批 · 已匹配 '+p.matched+' 首':'';
 };

 get('themeEnabled').onchange=()=>{dirty=true;get('themeProfileNote').textContent='主题设置尚未保存';get('saveThemes').disabled=false;};
 get('saveThemes').onclick=()=>action(async()=>{
  const selected=[...get('themeChoices').querySelectorAll('input:checked')].map(x=>x.value);
  const r=await post('/api/themes/settings',{confirm:true,enabled:get('themeEnabled').checked,selected});dirty=false;profileKey='';note(r.message);get('themeProfileNote').textContent='已保存';await refresh();
 });
 async function loadEvidence(offset=0){
  const d=await (await request('/api/themes/evidence?category_id='+encodeURIComponent(evidenceCategory)+'&offset='+offset+'&limit=40&added_only='+String(onlyNew))).json();
  get('themeEvidenceTitle').textContent=d.title+' · '+(onlyNew?'本次新增':'匹配歌曲')+'与来源';
  get('themeEvidenceNote').textContent='共 '+d.total+' 首。这些是参考歌单的选歌关联，不是单曲权威情绪标签；资料核对不代表音频鉴定。';
  if(offset===0)get('themeEvidenceRows').replaceChildren();
  if(!d.items?.length&&offset===0)say(get('themeEvidenceRows'),'p','这个主题当前没有新增曲目。可检查参考范围，不需要重跑全库单曲详情。');
  for(const row of d.items||[]){
   const card=document.createElement('article');card.className='theme-evidence-card';say(card,'h3',row.title+' / '+row.artist);
   say(card,'p',(row.methods||[]).includes('verified_mid')?'匹配依据：复用已核对的QQ录音编号，同时检查来源没有明显矛盾。':'匹配依据：歌名、完整歌手和版本等元数据核对。','muted');
   for(const origin of row.origins||[]){
    const p=say(card,'p','');const url=safeQQ(origin.url);
    if(url){const a=say(p,'a',origin.title||'QQ参考歌单');a.href=url;a.target='_blank';a.rel='noopener noreferrer';}
    else say(p,'span',origin.title||'参考资料');
    if(origin.basis)say(p,'small',' · '+origin.basis);
   }
   const b=say(card,'button','不放进这个主题');b.type='button';b.className='text-button';
   b.onclick=()=>action(async()=>{
    if(!await PCHUI.confirm('以后不再把“'+row.title+'”补入这个主题？其他主题不受影响；已在旧歌单中的成员不会自动删除。'))return;
    const r=await post('/api/themes/exclude',{confirm:true,category_id:evidenceCategory,track_id:row.id,excluded:true});get('themeEvidence').hidden=true;note(r.message);
   });get('themeEvidenceRows').append(card);
  }
  nextOffset=d.next;get('moreThemeEvidence').hidden=nextOffset==null;
 }
 window.openThemeEvidence=async(categoryId,added=true)=>{evidenceCategory=categoryId;onlyNew=added;get('themeEvidence').hidden=false;await loadEvidence();get('themeEvidence').scrollIntoView({block:'start'});};
 get('moreThemeEvidence').onclick=()=>action(()=>loadEvidence(nextOffset||0));
 get('closeThemeEvidence').onclick=()=>{get('themeEvidence').hidden=true;};
 if(typeof current!=='undefined'&&current)window.renderThemeExtras(current);
})();


/* v0.3.6: explicit lifecycle controls and evidence-based duplicate hints. */
(()=>{
 'use strict';
 const get=id=>document.getElementById(id);let loading=false,lastLoaded=0;
 const button=(text,cls,fn,disabled=false)=>{const b=document.createElement('button');b.type='button';b.className=cls;b.textContent=text;b.disabled=disabled;b.onclick=fn;return b;};
 async function loadManaged(force=false){
  if(loading||(!force&&Date.now()-lastLoaded<10000)||!get('managedPlaylists'))return;
  loading=true;try{
   const [data,overlap]=await Promise.all([(await request('/api/managed/playlists')).json(),(await request('/api/themes/overlaps')).json()]);
   renderManaged(data);renderOverlaps(overlap);lastLoaded=Date.now();
  }catch(e){note(e.message,true);}finally{loading=false;}
 }
 function renderManaged(data){
  const box=get('managedPlaylists');box.replaceChildren();
  if(!(data.items||[]).length){const p=document.createElement('p');p.className='muted';p.textContent='还没有助手托管的分类歌单。';box.append(p);}
  for(const row of data.items||[]){
   const line=document.createElement('article');line.className='managed-playlist-row';
   const info=document.createElement('div'),title=document.createElement('strong'),meta=document.createElement('span');
   title.textContent=row.title;meta.textContent=(row.count==null?'曲目数待核对':row.count+' 首')+' · '+row.status+(row.enabled?'':' · 已停止维护');info.append(title,meta);
   const actions=document.createElement('div');actions.className='managed-playlist-actions';
   if(row.safe_to_forget)actions.append(button('清除记录','danger',async()=>{if(!await PCHUI.confirm('Plex 中已经找不到“'+row.title+'”。只清除助手里的历史记录，不删除歌曲或音乐文件。确定继续？'))return;await action(async()=>{const r=await post('/api/managed/forget',{confirm:true,category_id:row.category_id,playlist_id:row.playlist_id,title:row.title});note(r.message);await loadManaged(true);await refresh();});}));
   actions.append(button(row.enabled?'停止维护':'已停止','secondary',async()=>{if(!await PCHUI.confirm('停止维护“'+row.title+'”？Plex 中的歌单和歌曲都会保留。'))return;await action(async()=>{const r=await post('/api/managed/disable',{confirm:true,category_id:row.category_id});note(r.message);await loadManaged(true);});},!row.enabled));
   actions.append(button('移除歌单','danger',async()=>{if(!await PCHUI.confirm('从 Plex 移除助手创建的“'+row.title+'”（'+row.count+' 首）？\\n\\n只删除这张歌单，不删除歌曲；操作前会保存恢复快照。'))return;await action(async()=>{const r=await post('/api/managed/remove',{confirm:true,category_id:row.category_id,title:row.title});note(r.message);await loadManaged(true);await refresh();});},!row.safe_to_remove));
   if(!row.safe_to_remove)actions.lastChild.title='歌单被手动修改或管理标记不符时，为保护内容不能移除';
   if(row.safe_to_forget){actions.lastChild.remove();actions.lastChild.title='只清除助手本地历史记录，不访问 Plex 删除接口';}
   line.append(info,actions);box.append(line);
  }
  const retired=get('retiredPlaylists'),retiredRows=data.retired||[],disclosure=get('retiredDisclosure');retired.replaceChildren();
  disclosure.hidden=!retiredRows.length;get('retiredCount').textContent=retiredRows.length?retiredRows.length+' 个':'';
  for(const row of retiredRows){const line=document.createElement('article');line.className='managed-playlist-row retired';const info=document.createElement('div'),title=document.createElement('strong'),meta=document.createElement('span');title.textContent=row.title;meta.textContent='已移除 · 可从快照恢复';info.append(title,meta);line.append(info,button('恢复歌单','secondary',async()=>{if(!await PCHUI.confirm('从安全快照恢复“'+row.title+'”？恢复后默认停止维护。'))return;await action(async()=>{const r=await post('/api/managed/restore',{confirm:true,snapshot_id:row.snapshot_id});note(r.message);await loadManaged(true);await refresh();});}));retired.append(line);}
 }
 function renderOverlaps(data){
  const box=get('overlapNotice');box.replaceChildren();const rows=data.items||[];box.hidden=!rows.length;
  if(!rows.length)return;const heading=document.createElement('strong');heading.textContent='发现内容高度重合的分类';box.append(heading);
  for(const row of rows.slice(0,5)){const p=document.createElement('p');p.className='overlap-row';p.textContent=row.left_title+' 与 '+row.right_title+' 共同 '+row.shared+' 首（重合 '+Math.round(row.containment*100)+'%），建议优先保留“'+row.recommend_keep_title+'”。';box.append(p);}
 }
 const prior=window.renderThemeExtras;
 window.renderThemeExtras=data=>{if(prior)prior(data);if(!data.workflow?.job?.running)loadManaged(false);};
 get('refreshManaged')?.addEventListener('click',()=>loadManaged(true));
 window.addEventListener('pch-auth-ready',()=>loadManaged(true));window.addEventListener('pch-auth-login',()=>loadManaged(true));
})();
