(() => {
 'use strict';

 const routes=[
  {id:'settings',href:'/settings',label:'系统设置'},
  {id:'external',href:'/external',label:'导入歌单'},
  {id:'mixes',href:'/mixes',label:'智能歌单'},
  {id:'library',href:'/library',label:'曲库整理'},
  {id:'status',href:'/status',label:'运行状态'},
  {id:'appearance',href:'/appearance',label:'外观'},
 ];
 const actionSelector=[
  'button.primary','button.secondary','button.danger','button.button',
  'a.button','a.primary.link','a.secondary.link','.external-file-action','.external-text-action',
 ].join(',');

 function decorateActions(root){
  for(const action of root.querySelectorAll(actionSelector)){
   if(action.closest('.management-nav'))continue;
   action.classList.add('management-action');
  }
 }

 function createNavigation(activePage){
  const nav=document.createElement('nav');
  nav.className='management-nav';
  nav.setAttribute('aria-label','管理页面');
  for(const route of routes){
   const link=document.createElement('a');
   link.href=route.href;
   link.target='_top';
   link.textContent=route.label;
   if(route.id===activePage)link.setAttribute('aria-current','page');
   nav.append(link);
  }
  return nav;
 }

 function createStage(shell,activePage){
  const stage=document.createElement('div');
  stage.className='management-stage';
  const content=[...shell.childNodes];
  stage.append(createNavigation(activePage),...content);
  shell.append(stage);
  return stage;
 }

 function boot(){
  const activePage=document.body.dataset.managementPage;
  const shell=document.querySelector('[data-management-shell]');
  if(!activePage||!shell||shell.querySelector(':scope > .management-stage'))return;
  const stage=createStage(shell,activePage);
  decorateActions(stage);
  new MutationObserver(records=>{
   for(const record of records){
    for(const node of record.addedNodes){
     if(node.nodeType!==Node.ELEMENT_NODE)continue;
     if(node.matches?.(actionSelector))node.classList.add('management-action');
     decorateActions(node);
    }
   }
  }).observe(stage,{childList:true,subtree:true});
 }

 if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});
 else boot();
})();
