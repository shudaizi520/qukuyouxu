(()=>{
'use strict';
const $=id=>document.getElementById(id);
let state={authenticated:false,username:null,setup_required:false};
let readyResolve;const ready=new Promise(r=>readyResolve=r);
function message(text,error=false){const n=$('authMessage');if(!n)return;n.hidden=!text;n.textContent=text||'';n.className='auth-message'+(error?' error':'');}
function showWorkspace(){if($('bootScreen'))$('bootScreen').hidden=true;if($('authShell'))$('authShell').hidden=true;if($('workspace'))$('workspace').hidden=false;for(const el of document.querySelectorAll('[data-auth-logout]'))el.hidden=false;document.body.dataset.authState='ready';}
function showAuth(){if($('bootScreen'))$('bootScreen').hidden=true;if($('workspace'))$('workspace').hidden=true;if($('authShell'))$('authShell').hidden=false;for(const el of document.querySelectorAll('[data-auth-logout]'))el.hidden=true;document.body.dataset.authState='login';renderMode();}
function renderMode(){const setup=!!state.setup_required;if($('loginPanel'))$('loginPanel').hidden=setup;if($('setupPanel'))$('setupPanel').hidden=!setup;if(setup&&$('setupUser')&&!$('setupUser').value)$('setupUser').value='admin';}
async function raw(path,options={}){return fetch(path,{credentials:'same-origin',cache:'no-store',...options});}
async function status(){const r=await raw('/api/auth/status');if(!r.ok)throw Error('无法检查登录状态');state=await r.json();return state;}
async function request(path,method='GET',body){const opt={method,credentials:'same-origin',cache:'no-store',headers:{}};if(body!==undefined){opt.headers['Content-Type']='application/json';opt.body=JSON.stringify(body);}const r=await fetch(path,opt);if(!r.ok){let d={};try{d=await r.json();}catch{}if(r.status===401){try{await status();}catch{}showAuth();}throw Error(d.error||'请求失败：'+r.status);}return r;}
async function post(path,body){return (await request(path,'POST',body)).json();}
async function boot(){try{await status();if(state.authenticated)showWorkspace();else showAuth();}catch(e){showAuth();message(e.message,true);}finally{readyResolve(state);window.dispatchEvent(new CustomEvent('pch-auth-ready',{detail:state}));}}
async function login(event){event.preventDefault();message('');try{const r=await raw('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:$('loginUser').value.trim(),password:$('loginPassword').value})});let d={};try{d=await r.json();}catch{}if(!r.ok)throw Error(d.error||'登录失败');$('loginPassword').value='';await status();showWorkspace();window.dispatchEvent(new CustomEvent('pch-auth-login',{detail:state}));}catch(e){message(e.message,true);}}
async function setup(event){event.preventDefault();message('');const password=$('setupPassword').value,confirm=$('setupConfirm').value;if(password!==confirm){message('两次输入的密码不一致',true);return;}try{const r=await raw('/api/auth/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:$('setupUser').value.trim(),password,confirm_password:confirm})});let d={};try{d=await r.json();}catch{}if(!r.ok)throw Error(d.error||'账户建立失败');$('setupPassword').value='';$('setupConfirm').value='';await status();showWorkspace();window.dispatchEvent(new CustomEvent('pch-auth-login',{detail:state}));}catch(e){message(e.message,true);}}
async function logout(){try{await raw('/api/auth/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});}catch{}state={authenticated:false,username:state.username,setup_required:false};showAuth();if($('loginUser')&&state.username)$('loginUser').value=state.username;window.dispatchEvent(new Event('pch-auth-logout'));}
window.PCHAuth={ready,request,post,status:()=>state,logout};
document.addEventListener('DOMContentLoaded',()=>{if($('loginForm'))$('loginForm').addEventListener('submit',login);if($('setupForm'))$('setupForm').addEventListener('submit',setup);for(const el of document.querySelectorAll('[data-auth-logout]'))el.addEventListener('click',logout);boot();});
})();

// Shared presentation layer. It never calls application APIs.
(() => {
 let trigger=null, active=null, queue=Promise.resolve(), toastTimer;
 document.addEventListener('click',e=>{const b=e.target.closest('button,input[type=submit]');if(b?.classList.contains('pch-pending')){e.preventDefault();e.stopImmediatePropagation();return;}if(b&&!b.closest('.pch-dialog,.pch-toast'))trigger=b;},true);
 document.addEventListener('submit',e=>{trigger=e.submitter||trigger;},true);
 function confirm(message,options={}) {
  const task=()=>new Promise(resolve=>{
   const previous=document.activeElement,d=document.createElement('dialog');
   d.className='pch-dialog';d.setAttribute('aria-labelledby','pch-dialog-title');
   const title=document.createElement('h2');title.id='pch-dialog-title';
   title.textContent=options.title||(/恢复|修复/.test(message)?'确认恢复操作':/发布/.test(message)?'确认发布歌单':'确认此操作');
   const eyebrow=document.createElement('span');eyebrow.className='pch-dialog-label';eyebrow.textContent='曲库有序 · 操作确认';
   const body=document.createElement('div');body.className='pch-dialog-body';
   String(message).split(/\n+/).filter(Boolean).forEach(line=>{const p=document.createElement('p');p.textContent=line;body.append(p);});
   const actions=document.createElement('div');actions.className='pch-dialog-actions';
   const cancel=document.createElement('button');cancel.className='secondary';cancel.textContent='取消';cancel.autofocus=true;
   const accept=document.createElement('button');accept.textContent=options.confirmText||'确认继续';
   const close=document.createElement('button');close.type='button';close.className='pch-dialog-close';close.textContent='×';close.setAttribute('aria-label','关闭确认框');
   let settled=false;
   const finish=value=>{if(settled)return;settled=true;d.close();d.remove();if(previous?.isConnected)previous.focus({preventScroll:true});resolve(value);};
   cancel.onclick=()=>finish(false);accept.onclick=()=>finish(true);close.onclick=()=>finish(false);
   d.addEventListener('cancel',e=>{e.preventDefault();finish(false);});
   d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)finish(false);}});
   actions.append(cancel,accept);d.append(close,eyebrow,title,body,actions);document.body.append(d);d.showModal();cancel.focus();
  });
  const result=queue.then(task);queue=result.catch(()=>{});return result;
 }
 function notify(text,{error=false}={}) {
  document.querySelectorAll('.pch-inline-feedback,.pch-toast').forEach(e=>e.remove());clearTimeout(toastTimer);
  const old=document.getElementById('notice');if(old){old.hidden=true;old.textContent='';}
  if(!text)return;
  const anchor=active||trigger;
  const inlineTarget=anchor?.closest('.daily-controls')?.querySelector('#dailyFeedback');
  if(inlineTarget){
   const inline=document.createElement('div');inline.className='pch-inline-feedback'+(error?' is-error':'');inline.textContent=text;inline.setAttribute('role',error?'alert':'status');
   inlineTarget.append(inline);
  }
  const toast=document.createElement('div');toast.className='pch-toast'+(error?' is-error':'');toast.setAttribute('role',error?'alert':'status');
  const icon=document.createElement('span');icon.className='pch-toast-icon';icon.textContent=error?'!':'✓';
  const msg=document.createElement('span');msg.textContent=text;
  const close=document.createElement('button');close.textContent='×';close.setAttribute('aria-label','关闭通知');close.onclick=()=>toast.remove();
  toast.append(icon,msg,close);document.body.append(toast);
  if(!error)toastTimer=setTimeout(()=>toast.remove(),6000);
 }
 async function run(fn){
  const b=trigger,previous=active;active=b;
  const ariaDisabled=b?.getAttribute('aria-disabled');
  if(b){b.classList.add('pch-pending');b.setAttribute('aria-busy','true');b.setAttribute('aria-disabled','true');}
  try{return await fn();}
  finally{if(b){b.classList.remove('pch-pending');b.removeAttribute('aria-busy');if(ariaDisabled===null)b.removeAttribute('aria-disabled');else b.setAttribute('aria-disabled',ariaDisabled);}active=previous;}
 }
 window.PCHUI={confirm,notify,run};
})();
