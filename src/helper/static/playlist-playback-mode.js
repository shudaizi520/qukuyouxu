export const PLAYBACK_MODES=Object.freeze(['shuffle','sequential','single','list']);

export function normalizePlaybackMode(value){
 return PLAYBACK_MODES.includes(value)?value:'list';
}

export function createPlaybackNavigator({random=Math.random}={}){
 let queue=[];
 let current='';
 let playbackMode='list';
 let shuffleBag=[];
 let shuffleHistory=[];
 let shuffleHistoryIndex=-1;

 function randomIndex(length){
  const value=Number(random());
  if(!Number.isFinite(value))return 0;
  return Math.min(length-1,Math.max(0,Math.floor(value*length)));
 }
 function shuffled(values){
  const result=values.slice();
  for(let index=result.length-1;index>0;index--){
   const target=randomIndex(index+1);
   [result[index],result[target]]=[result[target],result[index]];
  }
  return result;
 }
 function resetShuffle(){
  shuffleHistory=current?[current]:[];
  shuffleHistoryIndex=shuffleHistory.length-1;
  shuffleBag=shuffled(queue.filter(id=>id!==current));
 }
 function refillShuffle(){
  const candidates=queue.filter(id=>id!==current);
  shuffleBag=shuffled(candidates);
 }
 function use(id){
  if(!id||!queue.includes(id))return null;
  current=id;
  return id;
 }
 function linearNext(wrap){
  const index=queue.indexOf(current);
  if(index<0)return null;
  if(index+1<queue.length)return use(queue[index+1]);
  return wrap&&queue.length?use(queue[0]):null;
 }
 function linearPrevious(wrap){
  const index=queue.indexOf(current);
  if(index<0)return null;
  if(index>0)return use(queue[index-1]);
  return wrap&&queue.length?use(queue.at(-1)):null;
 }
 function shuffleNext(){
  if(!queue.length)return null;
  if(queue.length===1)return use(queue[0]);
  if(shuffleHistoryIndex+1<shuffleHistory.length){
   shuffleHistoryIndex+=1;
   return use(shuffleHistory[shuffleHistoryIndex]);
  }
  if(!shuffleBag.length)refillShuffle();
  const next=shuffleBag.pop();
  if(!next)return null;
  shuffleHistory.push(next);
  shuffleHistoryIndex=shuffleHistory.length-1;
  return use(next);
 }

 function setQueue(ids,currentId){
  const seen=new Set();
  queue=[];
  for(const value of Array.isArray(ids)?ids:[]){
   const id=String(value??'');
   if(!id||seen.has(id))continue;
   seen.add(id);queue.push(id);
  }
  const requested=String(currentId??'');
  current=queue.includes(requested)?requested:(queue[0]||'');
  resetShuffle();
 }
 function setMode(value){
  const next=normalizePlaybackMode(value);
  if(next===playbackMode)return playbackMode;
  playbackMode=next;
  if(next==='shuffle')resetShuffle();
  return playbackMode;
 }
 function next(reason='manual'){
  if(!queue.length||!current)return null;
  if(playbackMode==='single'&&reason==='ended')return current;
  if(playbackMode==='shuffle')return shuffleNext();
  return linearNext(playbackMode!=='sequential');
 }
 function previous(){
  if(!queue.length||!current)return null;
  if(playbackMode==='shuffle'){
   if(shuffleHistoryIndex>0){
    shuffleHistoryIndex-=1;
    return use(shuffleHistory[shuffleHistoryIndex]);
   }
   return current;
  }
  return linearPrevious(playbackMode!=='sequential');
 }

 return {setQueue,setMode,next,previous,mode:()=>playbackMode,current:()=>current};
}
