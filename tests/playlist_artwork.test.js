import {test} from 'node:test';
import assert from 'node:assert/strict';
import {artworkUrl, createPlaylistArtwork} from '../src/helper/static/playlist-artwork.js';

class FakeElement {
 constructor(tag='span') {this.tagName=tag;this.children=[];this.dataset={};this.attributes={};this.textContent='';this.isConnected=true;this.parentNode=null;}
 get src() {return this.attributes.src||'';}
 set src(value) {this.attributes.src=String(value);}
 append(...children) {for(const child of children){child.parentNode=this;this.children.push(child);}this.textContent='';}
 replaceChildren(...children) {this.children=[];this.textContent='';this.append(...children);}
 remove() {if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(child=>child!==this);this.parentNode=null;}
 setAttribute(key,value) {this.attributes[key]=String(value);}
 getAttribute(key) {return this.attributes[key]??null;}
 removeAttribute(key) {delete this.attributes[key];}
}
const document={createElement:tag=>new FakeElement(tag)};
const item={kind:'plex',key:'72',can_play:true};
const flush=()=>new Promise(resolve=>setTimeout(resolve,0));

test('cover URLs are scoped to the selected profile',()=>{
 assert.equal(artworkUrl('19','user & library'),'/api/playlists/library/tracks/19/artwork?profile_id=user%20%26%20library');
});

test('known track artwork paints a compact montage and absent artwork keeps a placeholder',()=>{
 const art=createPlaylistArtwork({document,request:async()=>({track_ids:[]}),profileId:()=> 'profile-1'});
 art.reset('profile-1');
 const image=document.createElement('span');
 art.paintTracks(image,[{id:'12',thumb:'/library/metadata/12/thumb/1'},{id:'13',thumb:'/library/metadata/13/thumb/1'}]);
 assert.deepEqual(image.children.map(child=>child.src),[
  '/api/playlists/library/tracks/12/artwork?profile_id=profile-1',
  '/api/playlists/library/tracks/13/artwork?profile_id=profile-1',
 ]);
 const empty=document.createElement('span');
 art.paintTracks(empty,[{id:'14',thumb:''}]);
 assert.equal(empty.textContent,'♫');
});

test('failed image load restores the designed placeholder',()=>{
 const art=createPlaylistArtwork({document,request:async()=>({track_ids:[]}),profileId:()=> 'p1'});
 art.reset('p1');
 const node=document.createElement('span');
 art.paintTracks(node,[{id:'12',thumb:'yes'}]);
 node.children[0].onerror();
 assert.equal(node.children.length,0);
 assert.equal(node.textContent,'♫');
});

test('an existing artwork node keeps its loaded image while it refreshes',async()=>{
 const art=createPlaylistArtwork({document,request:async()=>({track_ids:['12']}),profileId:()=> 'p1'});
 art.reset('p1');
 const node=art.create(item,'sidebar');
 await flush();
 const loadedImage=node.children[0];
 assert.equal(loadedImage.src,artworkUrl('12','p1'));

 const reused=art.create(item,'sidebar',node);
 assert.equal(reused,node);
 assert.equal(reused.children[0],loadedImage);
 assert.notEqual(reused.textContent,'♫');
 await flush();
 assert.equal(reused.children[0],loadedImage);
});

test('an interrupted image load is restored when the same node is reused',async()=>{
 let resolveCover,requests=0;
 const art=createPlaylistArtwork({document,request:()=>++requests===1?new Promise(resolve=>{resolveCover=resolve;}):Promise.resolve({track_ids:['12']}),profileId:()=> 'p1'});
 art.reset('p1');
 const node=art.create(item,'sidebar');
 await flush();
 resolveCover({track_ids:['12']});
 await flush();
 assert.equal(node.children[0].src,artworkUrl('12','p1'));

 art.reset('p1');
 assert.equal(node.textContent,'♫');
 const reused=art.create(item,'sidebar',node);
 await flush();
 assert.equal(reused.children[0].src,artworkUrl('12','p1'));
});

test('a pending cover response cannot paint after profile switch',async()=>{
 let finish;
 const art=createPlaylistArtwork({document,request:()=>new Promise(resolve=>{finish=resolve;}),profileId:()=> 'p1'});
 art.reset('p1');
 const node=art.create(item,'sidebar');
 await flush();
 art.reset('p2');
 finish({track_ids:['12']});
 await flush();
 assert.equal(node.textContent,'♫');
 assert.equal(node.children.length,0);
});

test('large lists fetch no more than three covers at once',async()=>{
 let started=0;
 const art=createPlaylistArtwork({document,request:()=>{started++;return new Promise(()=>{});},profileId:()=> 'p1'});
 art.reset('p1');
 for(let index=0;index<7;index++)art.create({kind:'plex',key:String(index),can_play:true},'sidebar');
 await flush();
 assert.equal(started,3);
});

test('without IntersectionObserver a newly created detached cover loads after it is attached',async()=>{
 let started=0;
 const detachedDocument={createElement:tag=>{const node=new FakeElement(tag);if(tag==='span')node.isConnected=false;return node;}};
 const art=createPlaylistArtwork({document:detachedDocument,request:async()=>{started++;return {track_ids:['12']};},profileId:()=> 'p1'});
 art.reset('p1');
 const node=art.create(item,'sidebar');
 node.isConnected=true;
 await flush();
 assert.equal(started,1);
 assert.equal(node.children[0].src,artworkUrl('12','p1'));
});
