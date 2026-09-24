export function buildVisualizerLevels(frequencyData,count){
 const barCount=Math.max(0,Math.floor(Number(count)||0));
 if(!barCount)return [];
 const size=frequencyData?.length||0;
 if(size<2)return Array(barCount).fill(0);
 const usable=Math.min(size-1,110),spectrum=[];
 for(let bin=1;bin<=usable;bin++){
  const normalized=Math.min(1,Math.max(0,(frequencyData[bin]-5)/214));
  spectrum.push(Math.pow(normalized,.58));
 }
 const strongest=[...spectrum].sort((left,right)=>right-left).slice(0,Math.min(10,spectrum.length));
 const globalLevel=Math.min(1,strongest.reduce((sum,value)=>sum+value,0)/Math.max(1,strongest.length));
 const sample=ratio=>{
  const point=Math.min(spectrum.length-1,Math.max(0,ratio*(spectrum.length-1)));
  const center=Math.floor(point),fraction=point-center;
  const left=spectrum[center]||0,right=spectrum[Math.min(spectrum.length-1,center+1)]||0;
  const interpolated=left+(right-left)*fraction;
  let nearby=0;
  for(let bin=Math.max(0,center-1);bin<=Math.min(spectrum.length-1,center+2);bin++)nearby=Math.max(nearby,spectrum[bin]||0);
  return interpolated*.72+nearby*.28;
 };
 const raw=Array.from({length:barCount},(_value,index)=>{
  const position=barCount===1?.5:index/(barCount-1),radialDistance=Math.abs(position-.5)*2;
  const centeredEnergy=sample(Math.min(1,.015+Math.pow(radialDistance,.82)*.68));
  const continuousSweep=sample(Math.min(1,.02+Math.pow(position,.92)*.82));
  const reverseSweep=sample(Math.min(1,.025+Math.pow(1-position,1.06)*.78));
  const detail=centeredEnergy*.64+continuousSweep*.24+reverseSweep*.12;
  const edgeWindow=Math.min(1,position/.08,(1-position)/.08);
  const centerWeight=.72+.28*(1-Math.pow(radialDistance,1.35));
  return Math.min(1,(globalLevel*.11+detail*1.05)*Math.sqrt(Math.max(0,edgeWindow))*centerWeight);
 });
 return raw.map((level,index)=>{
  const before=raw[index-1]??level,after=raw[index+1]??level;
  return level*.9+(before+after)*.05;
 });
}

export function smoothVisualizerLevels(previous,target,attack=.78,release=.18){
 return target.map((next,index)=>{
  const before=previous[index]||0,rate=next>before?attack:release;
  return before+(next-before)*rate;
 });
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
   analyser.fftSize=512;analyser.smoothingTimeConstant=.38;analyser.minDecibels=-92;analyser.maxDecibels=-22;
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
  context.save();context.filter='blur(8px)';context.globalCompositeOperation='lighter';
  const glow=context.createLinearGradient(0,height,0,6);
  glow.addColorStop(0,'rgba(0,190,94,.04)');glow.addColorStop(.46,'rgba(0,241,116,.26)');glow.addColorStop(1,'rgba(57,255,153,.48)');
  context.fillStyle=glow;
  levels.forEach((level,index)=>{
   if(level<.004)return;
   const barHeight=Math.max(1,level*height*.76),x=Math.round(start+index*(barWidth+gap));
   context.fillRect(x-2,height-barHeight,barWidth+4,barHeight);
  });
  context.restore();
 }
 function paint(){
  fit();context.clearRect(0,0,width,height);
  const barWidth=4,gap=4,count=Math.max(52,Math.min(100,Math.floor(width/(barWidth+gap))));
  const span=count*(barWidth+gap)-gap,start=(width-span)/2;
  if(analyser&&frequencyData&&playing)analyser.getByteFrequencyData(frequencyData);
  const target=buildVisualizerLevels(frequencyData&&playing?frequencyData:null,count);
  displayLevels=smoothVisualizerLevels(displayLevels,target);
  paintGlow(displayLevels,start,barWidth,gap);
  const gradient=context.createLinearGradient(0,height,0,6);
  gradient.addColorStop(0,'rgba(4,159,91,.16)');gradient.addColorStop(.38,'rgba(4,220,111,.82)');gradient.addColorStop(1,'rgba(76,255,161,1)');
  context.fillStyle=gradient;context.shadowColor='rgba(16,255,132,.68)';context.shadowBlur=6;
  for(let index=0;index<count;index++){
   const position=count===1?.5:index/(count-1),level=displayLevels[index]||0;
   if(level<.004)continue;
   const edgeWindow=Math.min(1,position/.08,(1-position)/.08);
   const barHeight=Math.max(1,level*height*.76),x=Math.round(start+index*(barWidth+gap)),y=Math.round(height-barHeight),drawHeight=Math.max(1,Math.round(barHeight));
   context.globalAlpha=.28+.72*Math.sqrt(Math.max(0,edgeWindow));
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
