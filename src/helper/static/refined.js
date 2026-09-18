/* Visible, persistent recommendation explanation control. */
(()=>{const toggle=document.getElementById('showReasons'),songs=document.getElementById('songs');if(!toggle||!songs)return;
 const key='recommendationReasonsVisible';
 const apply=value=>{songs.classList.toggle('show-reasons',value);toggle.setAttribute('aria-pressed',String(value));toggle.textContent=value?'隐藏推荐原因':'显示推荐原因';localStorage.setItem(key,value?'1':'0');};
 let visible=localStorage.getItem(key)==='1';apply(visible);toggle.addEventListener('click',()=>{visible=!visible;apply(visible);});
})();
