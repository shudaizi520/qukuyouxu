import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createPlaylistSections} from '../src/helper/static/playlist-sections.js';

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
 ['playlistSectionTitle',new FakeElement('h1')],
 ['playlistSectionSettings',new FakeElement('button')],
]);
const document={
 createElement:tag=>new FakeElement(tag),
 getElementById:id=>elements.get(id),
};
const smart={section:'smart',kind:'daily',key:'daily',title:'每日推荐',can_play:true,count:50};

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
