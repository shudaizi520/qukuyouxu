(()=>{
 'use strict';
 const KEY='pch-appearance-theme';
 const choices=[['light','清爽浅色'],['warm','暖色纸感'],['night','深色夜间']];
 const valid=id=>choices.some(([value])=>value===id);
 let current='light',trigger=null,menu=null,options=[],settingsOptions=[],navigationItems=[],logoutItem=null;
 try{const saved=window.localStorage.getItem(KEY);if(valid(saved))current=saved;}catch(_error){}
 const embedded=window.self!==window.top||new URLSearchParams(window.location.search).get('embedded')==='1';
 if(embedded){
  try{const inherited=window.parent?.PCHAppearance?.getTheme();if(valid(inherited))current=inherited;}catch(_error){}
 }

 function updateMenu(){
  for(const option of options){
   const selected=option.dataset.theme===current;
   option.setAttribute('aria-checked',String(selected));
   option.classList.toggle('selected',selected);
  }
  for(const option of settingsOptions){
   const selected=(option.dataset.appearanceChoice||option.dataset.theme)===current;
   option.setAttribute('aria-pressed',String(selected));
   option.classList.toggle('selected',selected);
  }
 }
 function applyTheme(){document.documentElement.dataset.appearance=current;updateMenu();return current;}
 function syncFrames(){
  for(const frame of document.querySelectorAll?.('iframe')||[]){
   try{frame.contentWindow?.PCHAppearance?.receiveTheme(current);}catch(_error){}
  }
 }
 function receiveTheme(id){if(valid(id)){current=id;applyTheme();syncFrames();}}
 function setTheme(id){
  current=valid(id)?id:'light';applyTheme();
  try{window.localStorage.setItem(KEY,current);}catch(_error){}
  syncFrames();
  return current;
 }
 function closeMenu(restoreFocus=false){
  if(!menu)return;
  menu.hidden=true;trigger.setAttribute('aria-expanded','false');
  if(restoreFocus)trigger.focus();
 }
 function openMenu(){
  if(!menu)return;
  menu.hidden=false;trigger.setAttribute('aria-expanded','true');updateMenu();
  const first=navigationItems[0]||options.find(option=>option.dataset.theme===current)||options[0];
  if(first)first.focus();
 }
 function chooseTheme(id){
  if(embedded){
   try{
    const parentAppearance=window.parent?.PCHAppearance;
    if(parentAppearance&&parentAppearance!==window.PCHAppearance){parentAppearance.setTheme(id);return;}
   }catch(_error){}
  }
  setTheme(id);
 }
 function mountAppearanceSettings(){
  settingsOptions=Array.from(document.querySelectorAll?.('[data-appearance-choice]')||[]);
  for(const option of settingsOptions){
   const id=option.dataset.appearanceChoice||option.dataset.theme;
   option.addEventListener('click',()=>chooseTheme(id));
  }
  updateMenu();
 }
 function mount(){
  mountAppearanceSettings();
  if(embedded)return;
  const header=document.querySelector('.topbar');if(!header||header.querySelector?.('.appearance-picker'))return;
  const navigation=header.querySelector?.('nav');
  navigationItems=navigation?Array.from(navigation.children):[];
  const unified=navigationItems.length>0;
  if(unified){
   navigation.hidden=true;
   logoutItem=header.querySelector?.('.logout');
   if(logoutItem)navigation.append(logoutItem);
   trigger=navigationItems[navigationItems.length-1];
   trigger.className=(trigger.className?trigger.className+' ':'')+'appearance-trigger topbar-settings-trigger';
   trigger.setAttribute('aria-label','打开全部设置');
   trigger.setAttribute('title','菜单');
   trigger.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 6h16M4 12h16M4 18h16"/></svg><span>菜单</span>';
   header.append(trigger);
   updateMenu();
   return;
  }
  const picker=document.createElement('div');picker.className=unified?'appearance-picker topbar-menu-picker':'appearance-picker';
  trigger=document.createElement('button');trigger.type='button';trigger.className='appearance-trigger';
  trigger.setAttribute('aria-label',unified?'打开菜单':'切换主题');trigger.setAttribute('title',unified?'菜单':'切换主题');
  trigger.setAttribute('aria-haspopup','menu');trigger.setAttribute('aria-expanded','false');
  trigger.innerHTML=unified?'<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M4 6h16M4 12h16M4 18h16"/></svg><span>菜单</span>':'<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m8 3 4 2 4-2 5 4-2.5 4-1.5-.7V21H7V10.3l-1.5.7L3 7l5-4Z"/><path d="M9.5 4.2c.4 1.4 1.2 2.1 2.5 2.1s2.1-.7 2.5-2.1"/></svg>';
  menu=document.createElement('div');menu.className='appearance-menu';menu.hidden=true;
  menu.setAttribute('role','menu');menu.setAttribute('aria-label',unified?'应用菜单':'选择界面主题');
  options=choices.map(([id,label])=>{
   const option=document.createElement('button');option.type='button';option.className='appearance-option';
   option.dataset.theme=id;option.setAttribute('role','menuitemradio');option.setAttribute('aria-checked',String(id===current));
   const swatch=document.createElement('span');swatch.className='appearance-swatch appearance-swatch-'+id;
   swatch.setAttribute('aria-hidden','true');
   const name=document.createElement('span');name.textContent=label;
   option.append(swatch,name);option.addEventListener('click',()=>{setTheme(id);closeMenu(true);});
   menu.append(option);return option;
  });
  picker.append(trigger,menu);header.append(picker);
  trigger.addEventListener('click',()=>menu.hidden?openMenu():closeMenu());
  document.addEventListener('click',event=>{if(menu&&!menu.hidden&&!picker.contains(event.target))closeMenu();});
  document.addEventListener('keydown',event=>{
   if(!menu||menu.hidden)return;
   if(event.key==='Escape'){event.preventDefault?.();closeMenu(true);return;}
   if(event.key==='Tab'){closeMenu();return;}
   if(event.key!=='ArrowDown'&&event.key!=='ArrowUp')return;
   if(!menu.contains(document.activeElement))return;
   event.preventDefault?.();
   const items=[...navigationItems,...options,...(logoutItem&&!logoutItem.hidden?[logoutItem]:[])];
   const focused=items.indexOf(document.activeElement),direction=event.key==='ArrowDown'?1:-1;
   items[(focused+direction+items.length)%items.length].focus();
  });
  updateMenu();
 }
 applyTheme();
 window.PCHAppearance={setTheme,getTheme:()=>current,applyTheme,receiveTheme};
 window.addEventListener('storage',event=>{
  if(event.key!==KEY)return;
  current=valid(event.newValue)?event.newValue:'light';applyTheme();syncFrames();
 });
 if(document.readyState==='loading'||!document.readyState)document.addEventListener('DOMContentLoaded',mount);
 else mount();
})();
