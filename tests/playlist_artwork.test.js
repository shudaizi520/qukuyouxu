import {test} from 'node:test';
import assert from 'node:assert/strict';
import {artworkUrl, createPlaylistArtwork} from '../src/helper/static/playlist-artwork.js';
import * as artworkModule from '../src/helper/static/playlist-artwork.js';

class FakeElement {
 constructor(tag='span') {this.tagName=tag;this.children=[];this.dataset={};this.attributes={};this.textContent='';this.isConnected=true;this.parentNode=null;}
 get src() {return this.attributes.src||'';}
 set src(value) {this.attributes.src=String(value);}
 append(...children) {for(const child of children){child.parentNode=this;child.isConnected=true;this.children.push(child);}}
 replaceChildren(...children) {for(const child of this.children){child.parentNode=null;child.isConnected=false;}this.children=[];this.textContent='';this.append(...children);}
 remove() {if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(child=>child!==this);this.parentNode=null;}
 setAttribute(key,value) {this.attributes[key]=String(value);}
 getAttribute(key) {return this.attributes[key]??null;}
 removeAttribute(key) {delete this.attributes[key];}
}
const document={createElement:tag=>new FakeElement(tag)};
const item={kind:'plex',key:'72',can_play:true};
const flush=()=>new Promise(resolve=>setTimeout(resolve,0));
class FakeStorage{
 constructor(){this.values=new Map();}
 get length(){return this.values.size;}
 key(index){return [...this.values.keys()][index]??null;}
 getItem(key){return this.values.has(key)?this.values.get(key):null;}
 setItem(key,value){this.values.set(String(key),String(value));}
 removeItem(key){this.values.delete(String(key));}
}

test('cover URLs are scoped to the selected profile',()=>{
 assert.equal(artworkUrl('19','user & library'),'/api/playlists/library/tracks/19/artwork?profile_id=user%20%26%20library');
 assert.equal(artworkUrl('19','user & library','large'),'/api/playlists/library/tracks/19/artwork?profile_id=user%20%26%20library&size=large');
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

test('a player artwork failure settles immediately without an artificial delayed retry',()=>{
 assert.equal(typeof artworkModule.loadArtworkImage,'function');
 const image={isConnected:true,src:'',onload:null,onerror:null};
 const scheduled=[];
 let loaded=0,failed=0;
 artworkModule.loadArtworkImage(image,'/api/playlists/library/tracks/19/artwork?profile_id=p1&size=large',{
  schedule:callback=>scheduled.push(callback),
  onLoad:()=>{loaded+=1;},
  onFailure:()=>{failed+=1;},
 });
 assert.equal(image.src,'/api/playlists/library/tracks/19/artwork?profile_id=p1&size=large');
 image.onerror();
 assert.equal(failed,1);
 assert.equal(loaded,0);
 assert.deepEqual(scheduled,[]);
 assert.equal(image.src,'/api/playlists/library/tracks/19/artwork?profile_id=p1&size=large');
});

test('switching tracks removes the old cover before the new cover is ready',()=>{
 assert.equal(typeof artworkModule.stageArtworkImage,'function');
 const container=document.createElement('span');
 const oldCover=document.createElement('img');oldCover.src='/old.jpg';container.append(oldCover);
 const nextCover=document.createElement('img');
 let ready=0,failed=0;

 artworkModule.stageArtworkImage(container,nextCover,'/new.jpg',{
  onReady:()=>{ready+=1;},
  onFailure:()=>{failed+=1;},
 });
 assert.deepEqual(container.children,[nextCover]);
 assert.equal(container.textContent,'♫');
 assert.equal(nextCover.hidden,true);

 nextCover.onload();
 assert.deepEqual(container.children,[nextCover]);
 assert.equal(nextCover.hidden,false);
 assert.equal(ready,1);
 assert.equal(failed,0);
});

test('failed staged artwork leaves a neutral placeholder instead of another track cover',()=>{
 const container=document.createElement('span');
 const oldCover=document.createElement('img');oldCover.src='/old.jpg';container.append(oldCover);
 const nextCover=document.createElement('img');
 let failed=0;
 artworkModule.stageArtworkImage(container,nextCover,'/new.jpg',{onFailure:()=>{failed+=1;}});

 nextCover.onerror();
 assert.deepEqual(container.children,[]);
 assert.equal(container.textContent,'♫');
 assert.equal(failed,1);
});

test('a late cover response cannot overwrite the newest track artwork',()=>{
 const container=document.createElement('span');
 const first=document.createElement('img');
 const second=document.createElement('img');

 artworkModule.stageArtworkImage(container,first,'/first.jpg');
 artworkModule.stageArtworkImage(container,second,'/second.jpg');
 assert.deepEqual(container.children,[second]);
 assert.equal(container.textContent,'♫');

 first.onload();
 assert.deepEqual(container.children,[second]);
 assert.equal(container.textContent,'♫');
 second.onload();
 assert.deepEqual(container.children,[second]);
 assert.equal(second.hidden,false);
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

test('a matching playlist revision restores cover ids without another detail request',async()=>{
 const previous=globalThis.sessionStorage,storage=new FakeStorage();globalThis.sessionStorage=storage;
 try{
  let requests=0;
  const options={document,request:async()=>{requests++;return {track_ids:['12']};},profileId:()=> 'p1',cacheUser:()=> 'alice',cacheScope:()=> 'scope-1'};
  const current={...item,playlist_id:'72',count:10,updated_at:100};
  const first=createPlaylistArtwork(options);first.reset('p1');const firstNode=first.create(current,'sidebar');await flush();
  firstNode.children[0]?.onload?.();
  assert.equal(requests,1);

  const reloaded=createPlaylistArtwork(options);reloaded.reset('p1');const restored=reloaded.create(current,'sidebar');await flush();
  assert.equal(requests,1);
  assert.equal(restored.children[0].src,artworkUrl('12','p1'));

  restored.children[0]?.onload?.();
  const changed=reloaded.create({...current,updated_at:101},'sidebar');await flush();
  assert.equal(requests,2);
  changed.children[0]?.onload?.();
 }finally{if(previous===undefined)delete globalThis.sessionStorage;else globalThis.sessionStorage=previous;}
});
