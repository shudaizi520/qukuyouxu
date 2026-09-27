export function buildVisualizerLevels(frequencyData,count){
 const barCount=Math.max(0,Math.floor(Number(count)||0));
 if(!barCount)return [];
 const size=frequencyData?.length||0;
 if(size<8)return Array(barCount).fill(0);
 const firstBin=3,lastBin=Math.max(firstBin+1,Math.min(size-1,Math.floor(size*.62)));
 const frequencyBands=Array.from({length:barCount},(_value,index)=>{
  const start=Math.max(firstBin,Math.floor(firstBin*Math.pow(lastBin/firstBin,index/barCount)));
  const end=Math.max(start+1,Math.floor(firstBin*Math.pow(lastBin/firstBin,(index+1)/barCount)));
  let sum=0,peak=0,samples=0;
  for(let bin=start;bin<=Math.min(lastBin,end);bin++){
   const value=frequencyData[bin]/255;
   sum+=value;peak=Math.max(peak,value);samples++;
  }
  return samples?(sum/samples)*.72+peak*.28:0;
 });
 const framePeak=Math.max(...frequencyBands);
 if(framePeak<.025)return Array(barCount).fill(0);
 const frameAverage=frequencyBands.reduce((sum,value)=>sum+value,0)/barCount;
 const amplitude=Math.min(1,.2+frameAverage*1.16+framePeak*.2);
 const spectralEnvelope=frequencyBands.map((_value,index)=>{
  let weighted=0,weightTotal=0;
  for(let offset=-7;offset<=7;offset++){
   const sample=Math.max(0,Math.min(barCount-1,index+offset));
   const weight=8-Math.abs(offset);
   weighted+=frequencyBands[sample]*weight;weightTotal+=weight;
  }
  return weighted/weightTotal;
 });
 const whitened=frequencyBands.map((energy,index)=>{
  const contrast=(energy-spectralEnvelope[index])/(spectralEnvelope[index]+.08);
  return Math.max(0,Math.min(1,.5+contrast*3.6));
 });
 const detailed=frequencyBands.map((energy,index)=>{
  const relative=Math.pow(energy/framePeak,.62);
  return Math.min(1,amplitude*(.12+relative*.33+whitened[index]*.62));
 });
 const smoothSpectrum=detailed.map((level,index)=>{
  const farBefore=detailed[index-2]??level,before=detailed[index-1]??level;
  const after=detailed[index+1]??level,farAfter=detailed[index+2]??level;
  return level*.5+(before+after)*.2+(farBefore+farAfter)*.05;
 });
 const centeredSpectrum=Array(barCount).fill(0);
 const leftCenter=Math.floor((barCount-1)/2),rightCenter=Math.ceil((barCount-1)/2);
 let sourceIndex=0,firstRadius=0;
 if(leftCenter===rightCenter){centeredSpectrum[leftCenter]=smoothSpectrum[0];sourceIndex=1;firstRadius=1;}
 for(let radius=firstRadius;sourceIndex<barCount;radius++){
  const left=leftCenter-radius,right=rightCenter+radius;
  const first=smoothSpectrum[sourceIndex]??0,second=smoothSpectrum[sourceIndex+1]??first;
  const pair=(first+second)/2;
  if(left>=0)centeredSpectrum[left]=pair*.25+first*.75;
  if(right<barCount)centeredSpectrum[right]=pair*.25+second*.75;
  sourceIndex+=2;
 }
 return centeredSpectrum.map((level,index)=>{
  const position=barCount===1?.5:index/(barCount-1);
  const edgeWindow=Math.min(1,position/.16,(1-position)/.16);
  return level*Math.pow(Math.max(0,edgeWindow),.68);
 });
}

export function smoothVisualizerLevels(previous,target,attack=.76,release=.34){
 return target.map((next,index)=>{
  const before=previous[index]||0,rate=next>before?attack:release;
  return before+(next-before)*rate;
 });
}

export function visualizerGeometry(width){
 const available=Math.max(1,Number(width)||1),barWidth=3.2,gap=6.5;
 const count=Math.max(42,Math.min(88,Math.floor((available+gap)/(barWidth+gap))));
 const span=count*(barWidth+gap)-gap;
 return {barWidth,gap,count,span,start:(available-span)/2};
}

export function createPlaybackVisualizer({canvas,media,view=globalThis}){
 const context=canvas?.getContext('2d');
 if(!context)return {mount(){},setPlaying(){},destroy(){}};
 let playing=false,frame=0,width=0,height=0,resizeObserver=null,displayLevels=[];
 let audioContext=null,source=null,analyser=null,frequencyData=null,audioUnavailable=false;
 const reducedMotion=!!view.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

 function ensureAnalyser(){
  if(analyser||audioUnavailable||!media)return analyser;
  const AudioContextClass=view.AudioContext||view.webkitAudioContext;
  if(!AudioContextClass){audioUnavailable=true;return null;}
  try{
   audioContext=new AudioContextClass();source=audioContext.createMediaElementSource(media);analyser=audioContext.createAnalyser();
   analyser.fftSize=4096;analyser.smoothingTimeConstant=.38;analyser.minDecibels=-92;analyser.maxDecibels=-22;
   frequencyData=new Uint8Array(analyser.frequencyBinCount);source.connect(analyser);analyser.connect(audioContext.destination);
  }catch(_error){audioUnavailable=true;analyser=null;frequencyData=null;}
  return analyser;
 }

 function fit(){
  const rect=canvas.getBoundingClientRect();
  const ratio=Math.min(2,Math.max(1,view.devicePixelRatio||1));
  width=Math.max(1,Math.round(rect.width));height=Math.max(1,Math.round(rect.height));
  const pixelWidth=Math.round(width*ratio),pixelHeight=Math.round(height*ratio);
  if(canvas.width!==pixelWidth||canvas.height!==pixelHeight){canvas.width=pixelWidth;canvas.height=pixelHeight;}
  context.setTransform(ratio,0,0,ratio,0,0);
 }
 function paintGlow(levels,start,barWidth,gap){
  context.save();context.filter='blur(5px)';context.globalCompositeOperation='lighter';
  const glow=context.createLinearGradient(0,height,0,6);
  glow.addColorStop(0,'rgba(0,190,94,.08)');glow.addColorStop(.46,'rgba(0,241,116,.38)');glow.addColorStop(1,'rgba(57,255,153,.68)');
  context.fillStyle=glow;
  levels.forEach((level,index)=>{
   if(level<.004)return;
   const barHeight=Math.max(1,level*height*.86),x=Math.round(start+index*(barWidth+gap));
   context.fillRect(x-2,height-barHeight,barWidth+4,barHeight);
  });
  context.restore();
 }
 function paint(){
  fit();context.clearRect(0,0,width,height);
  const {barWidth,gap,count,start}=visualizerGeometry(width);
  if(analyser&&frequencyData&&playing)analyser.getByteFrequencyData(frequencyData);
  const target=buildVisualizerLevels(frequencyData&&playing?frequencyData:null,count);
  displayLevels=smoothVisualizerLevels(displayLevels,target);
  paintGlow(displayLevels,start,barWidth,gap);
  const gradient=context.createLinearGradient(0,height,0,6);
  gradient.addColorStop(0,'rgba(4,159,91,.2)');gradient.addColorStop(.38,'rgba(4,220,111,.84)');gradient.addColorStop(1,'rgba(76,255,161,1)');
  context.fillStyle=gradient;context.shadowColor='rgba(16,255,132,.58)';context.shadowBlur=5;
  for(let index=0;index<count;index++){
   const position=count===1?.5:index/(count-1),level=displayLevels[index]||0;
   if(level<.004)continue;
   const edgeWindow=Math.min(1,position/.16,(1-position)/.16);
   const barHeight=Math.max(1,level*height*.86),x=Math.round(start+index*(barWidth+gap)),y=Math.round(height-barHeight),drawHeight=Math.max(1,Math.round(barHeight));
   context.globalAlpha=.12+.88*Math.pow(Math.max(0,edgeWindow),.75);
   context.fillRect(x,y,barWidth,drawHeight);
  }
  context.globalAlpha=1;
  return displayLevels.some(level=>level>.005);
 }
 function tick(){
  frame=0;
  const hasEnergy=paint();
  if(!reducedMotion&&(playing||hasEnergy))frame=view.requestAnimationFrame(tick);
 }
 function setPlaying(next){
  playing=!!next;
  if(frame){view.cancelAnimationFrame(frame);frame=0;}
  if(playing){ensureAnalyser();audioContext?.resume?.().catch?.(()=>{});}
  if(!reducedMotion)frame=view.requestAnimationFrame(tick);else paint();
 }
 function mount(){
  if(view.ResizeObserver){resizeObserver=new view.ResizeObserver(paint);resizeObserver.observe(canvas);}
  else view.addEventListener?.('resize',paint);
  paint();
 }
 function destroy(){
  if(frame)view.cancelAnimationFrame(frame);frame=0;resizeObserver?.disconnect();view.removeEventListener?.('resize',paint);
  try{source?.disconnect();analyser?.disconnect();}catch(_error){}audioContext?.close?.();source=null;analyser=null;audioContext=null;frequencyData=null;displayLevels=[];
 }
 return {mount,setPlaying,destroy};
}
