import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createPlaylistSections,groupPlaylists,orderPlaylists} from '../src/helper/static/playlist-sections.js';

class FakeClassList {
 constructor(){this.values=new Set();}
 add(value){this.values.add(value);}
 remove(value){this.values.delete(value);}
}

class FakeElement {
 constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.attributes={};this.classList=new FakeClassList();this.textContent='';}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=[...children];}
 setAttribute(key,value){this.attributes[key]=String(value);}
}

const elements=new Map([
 ['customPlaylistList',new FakeElement('nav')],
 ['playlistSectionCards',new FakeElement()],
 ['playlistSectionTitle',new FakeElement('button')],
]);
const document={
 createElement:tag=>new FakeElement(tag),
 getElementById:id=>elements.get(id),
};
const smart={section:'smart',kind:'daily',key:'daily',title:'每日推荐',can_play:true,count:50};

test('playlists keep the user-defined cross-section priority',()=>{
 const input=[
  {section:'library',kind:'category',key:'base:国语'},
  {section:'smart',kind:'smart',key:'time_capsule'},
  {section:'custom',kind:'plex',key:'90'},
  {section:'smart',kind:'smart',key:'weekly'},
 {section:'custom',kind:'external',key:'qq-1'},
  {section:'custom',kind:'plex',key:'91',smart:true},
  {section:'custom',kind:'favorite',key:'liked'},
  {section:'smart',kind:'daily',key:'daily'},
 ];

 const ordered=orderPlaylists(input);
 assert.deepEqual(ordered.map(row=>row.key),[
  'daily','liked','90','qq-1','91','weekly','time_capsule','base:国语',
 ]);
 const groups=groupPlaylists(input);
 assert.deepEqual(groups.all.map(row=>row.key),ordered.map(row=>row.key));
 assert.deepEqual(groups.smart.map(row=>row.key),['daily','weekly','time_capsule']);
 assert.deepEqual(groups.custom.map(row=>row.key),['liked','90','qq-1','91']);
 assert.deepEqual(groups.library.map(row=>row.key),['base:国语']);
 assert.deepEqual(groups.sidebar.map(row=>row.key),['liked','90','qq-1','91']);
});

test('sidebar omits assistant-generated playlists and keeps personal before Plex rules',()=>{
 const input=[
  {section:'custom',kind:'plex',key:'rule',smart:true,title:'四星'},
  {section:'smart',kind:'daily',key:'daily',title:'每日推荐'},
  {section:'library',kind:'category',key:'base:国语',title:'国语'},
  {section:'custom',kind:'plex',key:'manual',smart:false,title:'自建'},
  {section:'smart',kind:'smart',key:'weekly',title:'每周常听'},
  {section:'custom',kind:'external',key:'imported',title:'导入'},
  {section:'custom',kind:'favorite',key:'liked',title:'我喜欢'},
 ];
 const groups=groupPlaylists(input);
 assert.deepEqual(groups.sidebar.map(row=>row.key),['liked','manual','imported','rule']);
});

test('artwork nodes are reused only inside the same full profile scope',()=>{
 const sections=createPlaylistSections({
  document,onOpenPlaylist:()=>{},onOpenTool:()=>{},
  createArtwork:(_item,_variant,existing)=>existing||new FakeElement('artwork'),
 });

 sections.setItems([smart],'profile:account-a:server-a:library-a:1');
 sections.renderSection('smart');
 const first=elements.get('playlistSectionCards').children[0].children[0];

 sections.renderSection('smart');
 const reused=elements.get('playlistSectionCards').children[0].children[0];
 assert.equal(reused,first);

 sections.setItems([smart],'profile:account-b:server-b:library-b:2');
 sections.renderSection('smart');
 const isolated=elements.get('playlistSectionCards').children[0].children[0];
 assert.notEqual(isolated,first);
});
