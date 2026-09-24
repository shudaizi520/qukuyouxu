import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';

const source=readFileSync(new URL('../src/helper/static/appearance.js',import.meta.url),'utf8');

class Element {
 constructor(tag='div'){this.tagName=tag;this.children=[];this.events={};this.attributes={};this.dataset={};this.className='';this.classList={toggle(){}};this.hidden=false;this.focused=false;this.textContent='';}
 append(...nodes){for(const node of nodes){if(node.parentNode)node.parentNode.children=node.parentNode.children.filter(child=>child!==node);this.children.push(node);node.parentNode=this;}}
 setAttribute(key,value){this.attributes[key]=String(value);}
 getAttribute(key){return this.attributes[key]||null;}
 addEventListener(key,fn){this.events[key]=fn;}
 focus(){this.focused=true;}
 contains(node){return node===this||this.children.some(child=>child.contains(node));}
 querySelector(selector){for(const child of this.children){if(selector==='nav'&&child.tagName==='nav')return child;if(selector.startsWith('.')&&child.className.split(' ').includes(selector.slice(1)))return child;const nested=child.querySelector(selector);if(nested)return nested;}return null;}
}

function fixture(saved='',{blocked=false,embedded=false,parentWindow=null,frames=[],withActions=false,withSettingsPanel=false}={}){
 const header=new Element('header'),events={},storage={value:saved};
 let navigation=null,logout=null,status=null,settings=null;
 const appearanceChoices=[];
 if(withActions){
  navigation=new Element('nav');status=new Element('a');settings=new Element('button');
  logout=new Element('button');logout.className='logout';
  navigation.append(status,settings);header.append(navigation,logout);
 }
 if(withSettingsPanel){
  for(const theme of ['light','warm','night']){const choice=new Element('button');choice.dataset.theme=theme;appearanceChoices.push(choice);}
 }
 storage.getItem=()=>{if(blocked)throw Error('storage blocked');return storage.value;};
 storage.setItem=(_key,value)=>{if(blocked)throw Error('storage blocked');storage.value=value;};
 const document={documentElement:{dataset:{}},querySelector:selector=>selector==='.topbar'?header:null,
  querySelectorAll:selector=>selector==='iframe'?frames:selector==='[data-appearance-choice]'?appearanceChoices:[],
  createElement:tag=>new Element(tag),addEventListener:(name,fn)=>{events[name]=fn;}};
 const window={location:{search:''},localStorage:storage,events:{},addEventListener(name,fn){this.events[name]=fn;}};
 window.self=window;window.top=embedded?{}:window;window.parent=parentWindow||window;
 new Function('window','document','URLSearchParams',source)(window,document,URLSearchParams);
 return {window,document,header,events,storage,navigation,logout,status,settings,appearanceChoices,mount:()=>events.DOMContentLoaded()};
}

test('invalid or blocked storage falls back to the readable light theme',()=>{
 for(const setup of [fixture('invalid'),fixture('',{blocked:true})]){
  assert.equal(setup.document.documentElement.dataset.appearance,'light');
  setup.window.PCHAppearance.setTheme('night');
  assert.equal(setup.document.documentElement.dataset.appearance,'night');
 }
});

test('theme choice persists and a storage event updates another page',()=>{
 const setup=fixture();setup.mount();
 setup.window.PCHAppearance.setTheme('warm');
 assert.equal(setup.storage.value,'warm');
 assert.equal(setup.window.PCHAppearance.getTheme(),'warm');
 setup.window.events.storage({key:'pch-appearance-theme',newValue:'night'});
 assert.equal(setup.document.documentElement.dataset.appearance,'night');
});

test('one clothing control opens an accessible menu and Escape closes it',()=>{
 const setup=fixture();setup.mount();
 assert.equal(setup.header.children.length,1);
 const picker=setup.header.children[0],trigger=picker.children[0],menu=picker.children[1];
 assert.equal(trigger.getAttribute('aria-label'),'切换主题');
 trigger.events.click();
 assert.equal(menu.hidden,false);
 assert.equal(menu.children.length,3);
 setup.events.keydown({key:'Escape'});
 assert.equal(menu.hidden,true);
 assert.equal(trigger.focused,true);
 trigger.events.click();
 setup.events.click({target:setup.header});
 assert.equal(menu.hidden,true);
});

test('embedded tools inherit the theme without a duplicate clothing control',()=>{
 const setup=fixture('night',{embedded:true});setup.mount();
 assert.equal(setup.document.documentElement.dataset.appearance,'night');
 assert.equal(setup.header.children.length,0);
});

test('the top-right menu control is the existing direct settings action without a popup',()=>{
 const setup=fixture('',{withActions:true});setup.mount();
 assert.equal(setup.navigation.hidden,true);
 assert.equal(setup.settings.parentNode,setup.header);
 assert.equal(setup.settings.getAttribute('aria-label'),'打开全部设置');
 assert.match(setup.settings.className,/appearance-trigger/);
 assert.equal(setup.header.querySelector('.appearance-menu'),null);
 assert.equal(setup.logout.parentNode,setup.navigation);
});

test('appearance choices live in settings and update the parent workspace immediately',()=>{
 const frames=[];
 const parent=fixture('',{frames});parent.mount();
 const child=fixture('',{embedded:true,parentWindow:parent.window,withSettingsPanel:true});
 frames.push({contentWindow:child.window});child.mount();
 assert.equal(child.appearanceChoices[0].getAttribute('aria-pressed'),'true');
 child.appearanceChoices[1].events.click();
 assert.equal(parent.window.PCHAppearance.getTheme(),'warm');
 assert.equal(child.document.documentElement.dataset.appearance,'warm');
 assert.equal(child.appearanceChoices[1].getAttribute('aria-pressed'),'true');
});

test('blocked storage still synchronizes a parent theme with embedded tools',()=>{
 const frames=[];
 const parent=fixture('',{blocked:true,frames});parent.mount();
 parent.window.PCHAppearance.setTheme('warm');
 const child=fixture('',{blocked:true,embedded:true,parentWindow:parent.window});child.mount();
 assert.equal(child.document.documentElement.dataset.appearance,'warm');
 frames.push({contentWindow:child.window});
 parent.window.PCHAppearance.setTheme('night');
 assert.equal(child.document.documentElement.dataset.appearance,'night');
});
