(()=>{
'use strict';

const DEFAULTS={
 maxEntryBytes:512*1024,
 maxEntriesPerScope:16,
 maxEntries:48,
 maxBytes:8*1024*1024,
};

function createProfilePageCache(options={}){
 const now=typeof options.now==='function'?options.now:()=>Date.now();
 const limits={
  maxEntryBytes:Number(options.maxEntryBytes??DEFAULTS.maxEntryBytes),
  maxEntriesPerScope:Number(options.maxEntriesPerScope??DEFAULTS.maxEntriesPerScope),
  maxEntries:Number(options.maxEntries??DEFAULTS.maxEntries),
  maxBytes:Number(options.maxBytes??DEFAULTS.maxBytes),
 };
 let username='',profiles=new Map(),activeProfileId='',activeScope='',generation=0,totalBytes=0,touch=0;
 const entries=new Map(),inflight=new Map();
 const textEncoder=typeof TextEncoder==='function'?new TextEncoder():null;
 const clean=value=>String(value??'').trim();
 const clone=value=>JSON.parse(JSON.stringify(value));
 const encoded=value=>{
  const json=JSON.stringify(value);
  if(json===undefined)throw new TypeError('缓存仅支持 JSON 数据');
  return {value:JSON.parse(json),bytes:textEncoder?textEncoder.encode(json).byteLength:json.length*2};
 };
 const identity=row=>{
  const parts=[username,clean(row?.id),clean(row?.account?.id),clean(row?.server?.machine),clean(row?.library?.id),clean(row?.created_at)];
  return parts.every(Boolean)?JSON.stringify(parts):'';
 };
 const cacheKey=(scope,path)=>`${scope}\n${path}`;
 const remove=key=>{const entry=entries.get(key);if(!entry)return;totalBytes-=entry.bytes;entries.delete(key);};
 const evict=scope=>{
  const oldest=list=>list.reduce((candidate,row)=>!candidate||row[1].lastUsed<candidate[1].lastUsed?row:candidate,null);
  while([...entries.values()].filter(row=>row.scope===scope).length>limits.maxEntriesPerScope){
   const victim=oldest([...entries].filter(([,row])=>row.scope===scope));if(!victim)break;remove(victim[0]);
  }
  while(entries.size>limits.maxEntries||totalBytes>limits.maxBytes){const victim=oldest([...entries]);if(!victim)break;remove(victim[0]);}
 };
 const store=(key,scope,profileId,path,tag,value)=>{
  const packed=encoded(value);
  if(packed.bytes>limits.maxEntryBytes)return;
  remove(key);
  entries.set(key,{scope,profileId,path,tags:new Set(Array.isArray(tag)?tag:[tag].filter(Boolean)),value:packed.value,bytes:packed.bytes,createdAt:now(),lastUsed:++touch});
  totalBytes+=packed.bytes;evict(scope);
 };
 const cancel=record=>{record.invalidated=true;record.abort?.();};
 const changed=()=>{generation++;for(const record of inflight.values())cancel(record);inflight.clear();};

 function setProfiles(nextUsername,items){
  const previous=activeScope;
  username=clean(nextUsername);
  profiles=new Map((Array.isArray(items)?items:[]).map(row=>[clean(row?.id),row]).filter(([id])=>id));
  activeScope=identity(profiles.get(activeProfileId));
  if(activeScope!==previous)changed();
 }
 function activate(profileId){
  const id=clean(profileId),scope=identity(profiles.get(id));
  if(id!==activeProfileId||scope!==activeScope){activeProfileId=id;activeScope=scope;changed();}
  return activeScope;
 }
 function startLoad(path,settings,scope,profileId,requestGeneration,key){
  if(inflight.has(key))return inflight.get(key).promise;
  const controller=typeof AbortController==='function'?new AbortController():null;
  const record={scope,profileId,tags:new Set(Array.isArray(settings.tag)?settings.tag:[settings.tag].filter(Boolean)),invalidated:false,abort:()=>controller?.abort(),promise:null};
  const promise=Promise.resolve().then(()=>settings.load(controller?.signal)).then(value=>{
   if(record.invalidated||generation!==requestGeneration||activeScope!==scope){const error=new Error('缓存范围已切换或失效');error.name='AbortError';throw error;}
   store(key,scope,profileId,path,settings.tag,value);
   return clone(value);
  }).finally(()=>{if(inflight.get(key)===record)inflight.delete(key);});
  record.promise=promise;inflight.set(key,record);
  return promise;
 }
 async function cachedJson(path,settings={}){
  if(typeof settings.load!=='function')throw new TypeError('cachedJson 需要 load 函数');
  const normalized=String(path||''),scope=activeScope,profileId=activeProfileId,requestGeneration=generation;
  if(!scope)return {value:clone(await settings.load()),source:'network'};
  const key=cacheKey(scope,normalized),entry=entries.get(key),age=entry?Math.max(0,now()-entry.createdAt):Infinity;
  if(entry&&age<Math.max(0,Number(settings.freshMs)||0)){entry.lastUsed=++touch;return {value:clone(entry.value),source:'fresh'};}
  if(entry&&age<=Math.max(0,Number(settings.retainMs)||0)){
   entry.lastUsed=++touch;
   startLoad(normalized,settings,scope,profileId,requestGeneration,key).then(value=>{
    if(generation===requestGeneration&&activeScope===scope&&typeof settings.onUpdate==='function')settings.onUpdate(clone(value));
   }).catch(()=>{});
   return {value:clone(entry.value),source:'stale'};
  }
  if(entry)remove(key);
  const value=await startLoad(normalized,settings,scope,profileId,requestGeneration,key);
  return {value:clone(value),source:'network'};
 }
 function invalidate(tags,profileId){
  const wanted=new Set((Array.isArray(tags)?tags:tags?[tags]:[]).map(clean).filter(Boolean));
  const target=clean(profileId)||activeProfileId;
  for(const [key,entry] of entries){
   if(target&&entry.profileId!==target)continue;
   if(wanted.size&&![...entry.tags].some(tag=>wanted.has(tag)))continue;
   remove(key);
  }
  for(const [key,record] of inflight){
   if(target&&record.profileId!==target)continue;
   if(wanted.size&&![...record.tags].some(tag=>wanted.has(tag)))continue;
   cancel(record);inflight.delete(key);
  }
 }
 function clear(){
  entries.clear();totalBytes=0;username='';profiles=new Map();activeProfileId='';activeScope='';changed();
 }
 function stats(){return {entries:entries.size,bytes:totalBytes,scopes:new Set([...entries.values()].map(row=>row.scope)).size,active:!!activeScope,generation,limits:{...limits}};}
 return {setProfiles,activate,cachedJson,invalidate,clear,stats};
}

globalThis.createProfilePageCache=createProfilePageCache;
let shared=null;
if(typeof window!=='undefined'){
 try{if(window.top!==window&&window.top.location.origin===window.location.origin)shared=window.top.PCHPageCache||null;}catch{}
}
globalThis.PCHPageCache=shared||createProfilePageCache();
})();
