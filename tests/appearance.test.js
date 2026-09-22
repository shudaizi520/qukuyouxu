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

function fixture(saved='',{blocked=false,embedded=false,parentWindow=null,frames=[],withActions=false}={}){
 const header=new Element('header'),events={},storage={value:saved};
 let navigation=null,logout=null,status=null,settings=null;
 if(withActions){
  navigation=new Element('nav');status=new Element('a');settings=new Element('button');
  logout=new Element('button');logout.className='logout';
  navigation.append(status,settings);header.append(navigation,logout);
 }
 storage.getItem=()=>{if(blocked)throw Error('storage blocked');return storage.value;};
 storage.setItem=(_key,value)=>{if(blocked)throw Error('storage blocked');storage.value=value;};
 const document={documentElement:{dataset:{}},querySelector:selector=>selector==='.topbar'?header:null,
  querySelectorAll:selector=>selector==='iframe'?frames:[],
  createElement:tag=>new Element(tag),addEventListener:(name,fn)=>{events[name]=fn;}};
 const window={location:{search:''},localStorage:storage,events:{},addEventListener(name,fn){this.events[name]=fn;}};
 window.self=window;window.top=embedded?{}:window;window.parent=parentWindow||window;
 new Function('window','document','URLSearchParams',source)(window,document,URLSearchParams);
 return {window,document,header,events,storage,navigation,logout,status,settings,mount:()=>events.DOMContentLoaded()};
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

test('one top-right menu contains navigation, appearance, and logout',()=>{
 const setup=fixture('',{withActions:true});setup.mount();
 const picker=setup.header.children.at(-1),trigger=picker.children[0],menu=picker.children[1];
 assert.equal(setup.navigation.hidden,true);
 assert.equal(trigger.getAttribute('aria-label'),'打开菜单');
 assert.equal(setup.status.parentNode,menu);
 assert.equal(setup.settings.parentNode,menu);
 assert.equal(setup.logout.parentNode,menu);
 trigger.events.click();
 assert.equal(menu.hidden,false);
 setup.settings.events.click();
 assert.equal(menu.hidden,true);
 assert.equal(trigger.focused,true);
});

test('open menu leaves arrow keys alone when focus is outside it',()=>{
 const setup=fixture('',{withActions:true});setup.mount();
 const picker=setup.header.children.at(-1),trigger=picker.children[0];
 trigger.events.click();
 setup.document.activeElement=new Element('input');
 let prevented=false;
 setup.events.keydown({key:'ArrowDown',preventDefault(){prevented=true;}});
 assert.equal(prevented,false);
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
