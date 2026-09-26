import {test} from 'node:test';
import assert from 'node:assert/strict';

await import('../src/helper/static/page-cache.js');

const createProfilePageCache=globalThis.createProfilePageCache;
const deferred=()=>{
 let resolve,reject;
 const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});
 return {promise,resolve,reject};
};
class FakeStorage{
 constructor(){this.values=new Map();}
 getItem(key){return this.values.has(key)?this.values.get(key):null;}
 setItem(key,value){this.values.set(String(key),String(value));}
 removeItem(key){this.values.delete(String(key));}
}
const profile=(id,library=id,extra={})=>({
 id,
 enabled:true,
 created_at:'2026-09-26T00:00:00Z',
 account:{id:`account-${id}`},
 server:{machine:`server-${id}`},
 library:{id:library},
 ...extra,
});
const setup=(options={})=>{
 const cache=createProfilePageCache(options);
 cache.setProfiles('alice',[profile('a'),profile('b')]);
 cache.activate('a');
 return cache;
};

test('profile identities never share entries',async()=>{
 const cache=setup();
 let loads=0;
 const load=async()=>({owner:++loads});
 assert.deepEqual(await cache.cachedJson('/api/playlists',{tag:'playlists',freshMs:1000,retainMs:2000,load}),{value:{owner:1},source:'network'});
 cache.activate('b');
 assert.deepEqual(await cache.cachedJson('/api/playlists',{tag:'playlists',freshMs:1000,retainMs:2000,load}),{value:{owner:2},source:'network'});
 cache.activate('a');
 assert.deepEqual(await cache.cachedJson('/api/playlists',{tag:'playlists',freshMs:1000,retainMs:2000,load}),{value:{owner:1},source:'fresh'});
});

test('late response cannot populate a changed scope',async()=>{
 const cache=setup(),pending=deferred();
 const result=cache.cachedJson('/api/playlists',{tag:'playlists',freshMs:1000,retainMs:2000,load:()=>pending.promise});
 cache.activate('b');
 pending.resolve({owner:'a'});
 await assert.rejects(result,error=>error?.name==='AbortError');
 cache.activate('a');
 let loads=0;
 const fresh=await cache.cachedJson('/api/playlists',{tag:'playlists',freshMs:1000,retainMs:2000,load:async()=>({owner:`retry-${++loads}`})});
 assert.equal(fresh.source,'network');
 assert.equal(loads,1);
});

test('logout clears entries and suppresses callbacks',async()=>{
 let time=0,updates=0;
 const cache=setup({now:()=>time}),pending=deferred();
 await cache.cachedJson('/api/playlists',{tag:'playlists',freshMs:10,retainMs:100,load:async()=>({value:1})});
 time=20;
 const stale=await cache.cachedJson('/api/playlists',{tag:'playlists',freshMs:10,retainMs:100,load:()=>pending.promise,onUpdate:()=>updates++});
 assert.equal(stale.source,'stale');
 cache.clear();
 pending.resolve({value:2});
 await new Promise(resolve=>setTimeout(resolve,0));
 assert.equal(updates,0);
 assert.equal(cache.stats().entries,0);
});

test('incomplete identities bypass cache',async()=>{
 const cache=createProfilePageCache();
 cache.setProfiles('alice',[profile('a','')]);
 assert.equal(cache.activate('a'),'');
 let loads=0;
 const options={tag:'playlists',freshMs:1000,retainMs:2000,load:async()=>({load:++loads})};
 assert.equal((await cache.cachedJson('/api/playlists',options)).source,'network');
 assert.equal((await cache.cachedJson('/api/playlists',options)).source,'network');
 assert.equal(loads,2);
 assert.equal(cache.stats().entries,0);
});

test('same profile id with a changed library misses',async()=>{
 const cache=setup();
 let loads=0;
 const options={tag:'playlists',freshMs:1000,retainMs:2000,load:async()=>({load:++loads})};
 await cache.cachedJson('/api/playlists',options);
 cache.setProfiles('alice',[profile('a','library-new'),profile('b')]);
 cache.activate('a');
 assert.equal((await cache.cachedJson('/api/playlists',options)).source,'network');
 assert.equal(loads,2);
});

test('fresh stale and expired entries follow time boundaries',async()=>{
 let time=0,loads=0;
 const cache=setup({now:()=>time});
 const options=()=>({tag:'playlists',freshMs:10,retainMs:30,load:async()=>({load:++loads})});
 const first=await cache.cachedJson('/api/playlists',options());
 first.value.load=999;
 time=9;
 const fresh=await cache.cachedJson('/api/playlists',options());
 assert.deepEqual(fresh,{value:{load:1},source:'fresh'});
 fresh.value.load=888;
 time=10;
 const stale=await cache.cachedJson('/api/playlists',options());
 assert.deepEqual(stale,{value:{load:1},source:'stale'});
 await new Promise(resolve=>setTimeout(resolve,0));
 time=41;
 const expired=await cache.cachedJson('/api/playlists',options());
 assert.equal(expired.source,'network');
 assert.equal(expired.value.load,3);
});

test('concurrent loads share one request',async()=>{
 const cache=setup(),pending=deferred();
 let loads=0;
 const options={tag:'playlists',freshMs:1000,retainMs:2000,load:()=>{loads++;return pending.promise;}};
 const one=cache.cachedJson('/api/playlists',options);
 const two=cache.cachedJson('/api/playlists',options);
 await Promise.resolve();
 assert.equal(loads,1);
 pending.resolve({ok:true});
 assert.deepEqual(await one,{value:{ok:true},source:'network'});
 assert.deepEqual(await two,{value:{ok:true},source:'network'});
});

test('entry count and byte limits evict least recently used data',async()=>{
 const defaults=createProfilePageCache().stats().limits;
 assert.deepEqual(defaults,{maxEntryBytes:512*1024,maxEntriesPerScope:16,maxEntries:48,maxBytes:8*1024*1024});

 let time=0;
 const cache=setup({now:()=>++time,maxEntryBytes:80,maxEntriesPerScope:2,maxEntries:3,maxBytes:140});
 const put=path=>cache.cachedJson(path,{tag:'summary',freshMs:1000,retainMs:2000,load:async()=>({path,payload:'x'.repeat(12)})});
 await put('/one');
 await put('/two');
 await cache.cachedJson('/one',{tag:'summary',freshMs:1000,retainMs:2000,load:async()=>({bad:true})});
 await put('/three');
 assert.equal(cache.stats().entries,2);
 let reloads=0;
 assert.equal((await cache.cachedJson('/two',{tag:'summary',freshMs:1000,retainMs:2000,load:async()=>({reload:++reloads})})).source,'network');
 assert.equal(reloads,1);

 const tooLarge=setup();
 let bigLoads=0;
 const big={text:'x'.repeat(512*1024)};
 await tooLarge.cachedJson('/big',{tag:'summary',freshMs:1000,retainMs:2000,load:async()=>{bigLoads++;return big;}});
 await tooLarge.cachedJson('/big',{tag:'summary',freshMs:1000,retainMs:2000,load:async()=>{bigLoads++;return big;}});
 assert.equal(bigLoads,2);
 assert.equal(tooLarge.stats().entries,0);
});

test('a write invalidates only the declared tags for its profile',async()=>{
 const cache=setup();
 let loads=0;
 const read=(path,tag)=>cache.cachedJson(path,{tag,freshMs:1000,retainMs:2000,load:async()=>({load:++loads})});
 await read('/api/playlists','playlists');
 await read('/api/mixes/status','smart-mixes');
 cache.activate('b');
 await read('/api/playlists','playlists');
 cache.invalidate(['playlists'],'a');
 cache.activate('a');
 assert.equal((await read('/api/playlists','playlists')).source,'network');
 assert.equal((await read('/api/mixes/status','smart-mixes')).source,'fresh');
 cache.activate('b');
 assert.equal((await read('/api/playlists','playlists')).source,'fresh');
});

test('an invalidated in-flight read cannot restore stale data after a write',async()=>{
 const cache=setup(),pending=deferred();
 const first=cache.cachedJson('/api/playlists',{tag:'playlists',freshMs:1000,retainMs:2000,load:()=>pending.promise});
 await Promise.resolve();
 cache.invalidate(['playlists'],'a');
 pending.resolve({version:'before-write'});
 await assert.rejects(first,error=>error?.name==='AbortError');
 const retry=await cache.cachedJson('/api/playlists',{tag:'playlists',freshMs:1000,retainMs:2000,load:async()=>({version:'after-write'})});
 assert.deepEqual(retry,{value:{version:'after-write'},source:'network'});
});

test('safe profile data survives a full page reload without crossing users',async()=>{
 const storage=new FakeStorage();
 let time=100,loads=0;
 const first=setup({now:()=>time,storage});
 await first.cachedJson('/api/playlists',{tag:'playlists',freshMs:1000,retainMs:2000,load:async()=>({owner:'alice',load:++loads})});

 const reloaded=setup({now:()=>time+10,storage});
 const restored=await reloaded.cachedJson('/api/playlists',{tag:'playlists',freshMs:1000,retainMs:2000,load:async()=>({owner:'network',load:++loads})});
 assert.deepEqual(restored,{value:{owner:'alice',load:1},source:'fresh'});
 assert.equal(loads,1);

 reloaded.setProfiles('bob',[profile('a'),profile('b')]);
 reloaded.activate('a');
 const isolated=await reloaded.cachedJson('/api/playlists',{tag:'playlists',freshMs:1000,retainMs:2000,load:async()=>({owner:'bob',load:++loads})});
 assert.equal(isolated.source,'network');
 assert.equal(isolated.value.owner,'bob');
});

test('logout removes persisted page data',async()=>{
 const storage=new FakeStorage(),cache=setup({storage});
 await cache.cachedJson('/api/playlists',{tag:'playlists',freshMs:1000,retainMs:2000,load:async()=>({ok:true})});
 assert.ok(storage.getItem('pch-page-cache:v1'));
 cache.clear();
 assert.equal(storage.getItem('pch-page-cache:v1'),null);
});
