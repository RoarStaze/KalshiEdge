"""Visible, self-contained KalshiEdge ambient scene and orbiting card outlines.

This module is presentation-only. It never calls endpoints, changes the DOM IDs
used by the trading engine, or mutates the page's data. The scene also works when
the optional local neon-city background WebP has not been installed.
"""
from __future__ import annotations


MOTION_STYLE = r"""
/* Preserve the original art behind the UI, without softening the text. */
body::before{
 filter:blur(5px) brightness(.82) saturate(1.25);
 transform:scale(1.07);
 transform-origin:center;
 animation:ke-image-breathe 18s ease-in-out infinite alternate;
}
body::after{background:linear-gradient(90deg,rgba(0,8,14,.14),rgba(0,12,18,.30) 50%,rgba(0,8,14,.14))}
@keyframes ke-image-breathe{
 from{transform:scale(1.07) translate3d(-.6%,.4%,0)}
 to{transform:scale(1.12) translate3d(.8%,-.6%,0)}
}
/* Real moving layers even if the optional JPEG/WebP is missing. */
.ke-live-scene{
 position:fixed;inset:0;z-index:0;overflow:hidden;pointer-events:none;
 background:radial-gradient(ellipse at 50% 110%,rgba(0,133,121,.13),transparent 64%);
}
.ke-live-scene::before{
 content:"";position:absolute;inset:-30%;
 background:
  radial-gradient(ellipse 27% 35% at 12% 47%,rgba(13,226,165,.30),transparent 80%),
  radial-gradient(ellipse 32% 38% at 92% 29%,rgba(21,163,215,.32),transparent 80%),
  radial-gradient(ellipse 38% 23% at 51% 86%,rgba(21,225,162,.23),transparent 80%);
 filter:blur(28px);
 animation:ke-aurora-flow 12s ease-in-out infinite alternate;
}
.ke-live-scene::after{
 content:"";position:absolute;inset:0;z-index:4;
 background:radial-gradient(ellipse at 50% 48%,rgba(0,9,17,.15),rgba(0,7,12,.07) 55%,rgba(0,5,10,.20));
}
@keyframes ke-aurora-flow{
 from{transform:translate3d(-5%,3%,0) scale(1)}
 to{transform:translate3d(5%,-4%,0) scale(1.12)}
}
.ke-live-grid{
 position:absolute;left:-25%;bottom:-31%;width:150%;height:92%;
 background-image:
  linear-gradient(90deg,rgba(45,255,185,.36) 1px,transparent 1px),
  linear-gradient(0deg,rgba(45,255,185,.31) 1px,transparent 1px);
 background-size:72px 72px;
 transform:perspective(600px) rotateX(64deg);
 transform-origin:center bottom;
 opacity:.32;
 -webkit-mask-image:linear-gradient(to bottom,transparent 1%,black 65%);
 mask-image:linear-gradient(to bottom,transparent 1%,black 65%);
 animation:ke-grid-scroll 7s linear infinite;
}
@keyframes ke-grid-scroll{
 from{background-position:0 0,0 0}
 to{background-position:72px 0,0 72px}
}
.ke-live-beam{
 position:absolute;z-index:2;top:-22%;bottom:-22%;width:16%;
 left:-45%;opacity:.54;
 background:linear-gradient(90deg,transparent,rgba(18,252,171,.05) 33%,rgba(35,254,194,.27) 53%,transparent);
 filter:blur(26px);
 transform:skewX(-23deg);
 animation:ke-beam-sweep 10s linear infinite;
}
.ke-live-beam.ke-live-beam-second{
 background:linear-gradient(90deg,transparent,rgba(26,150,244,.20),transparent);
 animation-delay:-6.2s;
 animation-duration:14s;
 width:10%;opacity:.32;
}
@keyframes ke-beam-sweep{
 from{left:-40%}
 to{left:140%}
}
.ke-live-canvas{
 position:absolute;inset:0;width:100%;height:100%;
 z-index:3;display:block;pointer-events:none;
}
.ke-live-haze{
 position:absolute;inset:0;z-index:4;
 background:linear-gradient(180deg,rgba(0,10,16,.14),transparent 28%,rgba(0,8,13,.21));
 pointer-events:none;
}
/* The foreground behaves like raised, translucent cockpit glass. */
body[data-page] :is(.site-nav,.forecast,.context>.manual-trade,.training-card,
 .history-view .card,.api-card){
 background:linear-gradient(155deg,rgba(4,21,27,.82),rgba(3,13,21,.88) 74%)!important;
 border-color:rgba(47,235,178,.45)!important;
 box-shadow:0 24px 70px rgba(0,0,0,.61),0 0 31px rgba(17,222,169,.16)!important;
 backdrop-filter:blur(11px) saturate(1.2);
 -webkit-backdrop-filter:blur(11px) saturate(1.2);
}
body[data-page] .site-nav{background:rgba(3,22,25,.8)!important}
/* A real vector stroke follows the rounded perimeter at constant speed.
   Each card receives its own SVG, sized by ResizeObserver. */
.ke-neon-target{position:relative}
.ke-border-overlay{
 position:absolute!important;inset:0;display:block;width:100%;height:100%;
 overflow:visible;z-index:9;pointer-events:none!important;
 border:0!important;background:transparent!important;
 box-shadow:none!important;
}
.ke-neon-rail{
 fill:none;stroke:#24e5a9;stroke-width:1.5;stroke-opacity:.44;
 vector-effect:non-scaling-stroke;
}
.ke-neon-runner{
 fill:none;stroke:#76ffcc;stroke-width:3;stroke-linecap:round;
 stroke-dasharray:19 81;stroke-dashoffset:0;pathLength:100;
 filter:drop-shadow(0 0 3px rgba(85,255,196,.95)) drop-shadow(0 0 7px rgba(26,246,160,.75));
 vector-effect:non-scaling-stroke;
 animation:ke-border-travel 3.4s linear infinite;
}
@keyframes ke-border-travel{
 to{stroke-dashoffset:-100}
}
/* Keep the glowing vector decorative rather than intercepting actions. */
.ke-neon-target>.ke-border-overlay,
.ke-neon-target>.ke-border-overlay *{pointer-events:none!important}
@media(max-width:700px){
 .ke-live-scene::before{opacity:.65}
 .ke-live-grid{opacity:.19}
 .ke-live-beam{opacity:.32}
 .ke-neon-runner{stroke-width:2.5}
}
@media(prefers-reduced-motion:reduce){
 body::before{animation:none!important;transform:scale(1.07)}
 .ke-live-scene::before,.ke-live-grid,.ke-live-beam{animation:none!important}
 .ke-live-beam{left:20%;opacity:.12}
 .ke-neon-runner{animation:none!important;stroke-dasharray:100 0;stroke-opacity:.6}
}
"""

MOTION_MARKUP = r"""<div class="ke-live-scene" aria-hidden="true">
 <div class="ke-live-grid"></div>
 <div class="ke-live-beam"></div>
 <div class="ke-live-beam ke-live-beam-second"></div>
 <canvas id="ke-live-canvas" class="ke-live-canvas"></canvas>
 <div class="ke-live-haze"></div>
</div>"""

MOTION_SCRIPT = r"""<script>
/* Purely decorative motion: no fetches, browser storage, or trading state. */
(function(){
 "use strict";
 const boxSelector=[
  ".site-nav",".card",".manual-trade",".training-step",".training-count",
  ".training-pill",".kpi",".history-section",".api-card",".api-status",
  ".hero-price-surface",".hero-clock-panel",".lock-banner",".record-note",
  ".mt-auto-record",".mt-auto-performance",".mt-inputbox",".mt-pills",
  ".mt-auto-history",".mt-sell-holding",".mt-confirm",".mt-holding",
  ".training-progress",".train-status",".filter"
 ].join(",");
 const svgNS="http://www.w3.org/2000/svg";
 const processed=new WeakSet();
 let count=0;
 function resizeOutline(node){
  const svg=node.querySelector(":scope > svg.ke-border-overlay");
  if(!svg)return;
  const w=Math.max(5,node.clientWidth),h=Math.max(5,node.clientHeight);
  const radius=parseFloat(getComputedStyle(node).borderTopLeftRadius)||10;
  const rx=Math.min(radius,w/2,h/2);
  for(const rect of svg.children){
   rect.setAttribute("x","2");
   rect.setAttribute("y","2");
   rect.setAttribute("width",String(Math.max(1,w-4)));
   rect.setAttribute("height",String(Math.max(1,h-4)));
   rect.setAttribute("rx",String(rx));
   rect.setAttribute("pathLength","100");
  }
 }
 const resizer=typeof ResizeObserver==="function"?
   new ResizeObserver(entries=>{
    for(const entry of entries)resizeOutline(entry.target);
   }):null;
 function decorate(node){
  if(processed.has(node)||!(node instanceof HTMLElement))return;
  if(node.matches("input,textarea,select,option"))return;
  processed.add(node);
  node.classList.add("ke-neon-target");
  const svg=document.createElementNS(svgNS,"svg");
  svg.setAttribute("class","ke-border-overlay");
  svg.setAttribute("aria-hidden","true");
  svg.setAttribute("focusable","false");
  for(const klass of ["ke-neon-rail","ke-neon-runner"]){
   const rect=document.createElementNS(svgNS,"rect");
   rect.setAttribute("class",klass);
   if(klass==="ke-neon-runner"){
    rect.style.animationDelay=String(-.27*(count%9))+"s";
   }
   svg.appendChild(rect);
  }
  node.appendChild(svg);
  count+=1;
  resizeOutline(node);
  if(resizer)resizer.observe(node);
 }
 function addFrom(root){
  if(root.nodeType!==1)return;
  if(root.matches&&root.matches(boxSelector))decorate(root);
  if(root.querySelectorAll){
   for(const node of root.querySelectorAll(boxSelector))decorate(node);
  }
 }
 addFrom(document.body);
 const monitor=new MutationObserver(changes=>{
  for(const change of changes){
   for(const node of change.addedNodes){
    if(node.nodeType===1&&!node.classList?.contains("ke-border-overlay"))
     addFrom(node);
   }
  }
 });
 monitor.observe(document.body,{subtree:true,childList:true});
 // A cap on pixel ratio and frame rate keeps animation independent of
 // model-training and live-trading API/network workloads.
 const canvas=document.getElementById("ke-live-canvas");
 if(!canvas)return;
 const ctx=canvas.getContext("2d",{alpha:true});
 if(!ctx)return;
 const reduced=window.matchMedia("(prefers-reduced-motion: reduce)");
 let w=0,h=0,scale=1,frameId=0,lastPaint=-1;
 function hash(n){const v=Math.sin(n*113.47)*43758.545;return v-Math.floor(v);}
 const sparks=Array.from({length:66},(_,i)=>({
  x:hash(i+2),y:hash(i+211),p:hash(i+733)*6.283,
  velocity:.25+hash(i+47)*.85,radius:.7+hash(i+181)*1.8
 }));
 function resizeCanvas(){
  w=Math.max(1,window.innerWidth);
  h=Math.max(1,window.innerHeight);
  scale=Math.min(window.devicePixelRatio||1,1.5);
  canvas.width=Math.round(w*scale);
  canvas.height=Math.round(h*scale);
  ctx.setTransform(scale,0,0,scale,0,0);
 }
 function paint(ms){
  const t=ms*.001;
  ctx.clearRect(0,0,w,h);
  // Drifting electric currents form visible moving lines in the scene.
  for(let k=0;k<3;k++){
   ctx.beginPath();
   for(let x=0;x<=w+24;x+=24){
    const y=h*(.62+k*.12)
      +Math.sin(x/127+t*(.55+k*.11)+k)*24
      +Math.sin(x/63-t*.32+k*2)*8;
    if(x===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);
   }
   ctx.strokeStyle=k===1?"rgba(47,188,255,.29)":"rgba(34,246,174,.30)";
   ctx.lineWidth=k===0?2:1.4;
   ctx.shadowBlur=11;ctx.shadowColor="#17eeb4";ctx.stroke();
  }
  ctx.shadowBlur=0;
  // Bright moving photons travel across the scene behind the panels.
  for(const s of sparks){
   let x=(s.x*w+t*s.velocity*37)%(w+35);
   let y=(s.y*h-t*s.velocity*14)%(h+35);
   if(y<0)y+=h+35;
   const twinkle=.43+.57*Math.abs(Math.sin(t*1.6+s.p));
   ctx.fillStyle="rgba(109,255,203,"+(twinkle*.85).toFixed(3)+")";
   ctx.fillRect(x,y,s.radius,s.radius);
   if(s.radius>1.7){
    ctx.strokeStyle="rgba(45,233,180,"+(twinkle*.38).toFixed(3)+")";
    ctx.beginPath();ctx.moveTo(x-12,y+5);ctx.lineTo(x+2,y);ctx.stroke();
   }
  }
  // Holographic beacons pulse and drift without representing live price data.
  for(let k=0;k<5;k++){
   const x=w*(.08+k*.22)+Math.sin(t*.32+k)*22;
   const y=h*(.31+(k%3)*.20)+Math.cos(t*.46+k*2)*17;
   const a=.2+.2*Math.sin(t*1.3+k);
   const glow=ctx.createRadialGradient(x,y,0,x,y,26);
   glow.addColorStop(0,"rgba(57,255,184,"+a.toFixed(3)+")");
   glow.addColorStop(1,"rgba(57,255,184,0)");
   ctx.fillStyle=glow;ctx.beginPath();ctx.arc(x,y,26,0,Math.PI*2);ctx.fill();
  }
 }
 function tick(ms){
  frameId=requestAnimationFrame(tick);
  if(ms-lastPaint<33)return;
  lastPaint=ms;paint(ms);
 }
 function updatePlayback(){
  if(frameId){cancelAnimationFrame(frameId);frameId=0;}
  resizeCanvas();
  paint(performance.now());
  if(document.visibilityState==="visible"&&!reduced.matches){
   lastPaint=-1;
   frameId=requestAnimationFrame(tick);
  }
 }
 document.addEventListener("visibilitychange",updatePlayback);
 window.addEventListener("resize",updatePlayback,{passive:true});
 if(reduced.addEventListener)reduced.addEventListener("change",updatePlayback);
 updatePlayback();
})();
</script>"""
