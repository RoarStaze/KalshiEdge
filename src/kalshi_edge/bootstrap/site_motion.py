"""Quiet cinematic background with precise animated rainbow panel perimeters.

Purely presentational: no network requests, trading interactions, data
pollers, credential access, timers for backdrop particles, or dependencies.
"""
from __future__ import annotations


MOTION_STYLE = r"""
/* Retain the user's neon-city artwork but let the actual UI take priority. */
body::before{
 filter:blur(8px) brightness(.57) saturate(.83);
 transform:scale(1.09);
 transform-origin:center;
 animation:ke-cinematic-pan 32s ease-in-out infinite alternate;
 will-change:transform;
}
body::after{
 background:
  radial-gradient(ellipse at 50% 45%,rgba(0,10,16,.22),transparent 76%),
  linear-gradient(180deg,rgba(0,7,13,.18),rgba(0,7,13,.30));
}
@keyframes ke-cinematic-pan{
 from{transform:scale(1.09) translate3d(-.65%,.35%,0)}
 to{transform:scale(1.14) translate3d(.65%,-.35%,0)}
}
/* Only the large, meaningful surfaces get the floating-glass treatment.
   Text, controls and their contrast never get blurred. */
body[data-page] :is(
 .site-nav,.forecast,.context>.manual-trade,
 .training-card,.history-view .card,.api-card
){
 background:linear-gradient(155deg,rgba(3,17,23,.86),rgba(2,11,19,.91) 74%)!important;
 border-color:rgba(42,166,149,.34)!important;
 box-shadow:0 20px 56px rgba(0,0,0,.56),0 0 18px rgba(20,186,147,.09)!important;
 backdrop-filter:blur(12px) saturate(1.08);
 -webkit-backdrop-filter:blur(12px) saturate(1.08);
}
body[data-page] .site-nav{
 background:rgba(2,17,24,.82)!important;
}
/* Every decoration occupies the exact border box; no phantom rectangles
   across content headings. ViewBox is updated whenever a card resizes. */
.ke-neon-target{position:relative}
.ke-border-overlay{
 position:absolute!important;top:0;left:0;width:100%;height:100%;
 overflow:hidden;z-index:8;display:block;
 pointer-events:none!important;background:transparent!important;
 border:0!important;box-shadow:none!important;
}
.ke-neon-runner{
 fill:none;stroke-opacity:1;stroke-width:2.7;
 /* A continuous 100% perimeter, not the previous short dash segment. */
 stroke-dasharray:none;
 vector-effect:non-scaling-stroke;
 animation:ke-rainbow-cycle 5s linear infinite;
}
@keyframes ke-rainbow-cycle{
 from{filter:hue-rotate(0deg)
   drop-shadow(0 0 3px rgba(80,255,220,.82))
   drop-shadow(0 0 7px rgba(180,76,255,.48))}
 to{filter:hue-rotate(360deg)
   drop-shadow(0 0 3px rgba(80,255,220,.82))
   drop-shadow(0 0 7px rgba(180,76,255,.48))}
}
.ke-neon-target>.ke-border-overlay,
.ke-neon-target>.ke-border-overlay *{
 pointer-events:none!important;
}
/* Accessible reduced-motion users keep a soft colorful frame, not a runner. */
@media(prefers-reduced-motion:reduce){
 body::before{animation:none!important;transform:scale(1.09);will-change:auto}
 /* Keep the user-requested full-border color cycle, but slow its
    transitions when reduced-motion is enabled. No moving dash exists. */
 .ke-neon-target>.ke-border-overlay .ke-neon-runner{
  animation:ke-rainbow-cycle 9s linear infinite!important;
  stroke-dasharray:none;stroke-opacity:.84;stroke-width:2.2;
 }
}
"""

# No artificial particles, beams, grids, canvas or floating moving objects.
MOTION_MARKUP = ""

MOTION_SCRIPT = r"""<script>
(function(){
 "use strict";
 // Only decorate the OUTER panels. No price boxes, labels, inputs,
 // inner training blocks, notices, or buy/sell controls get a rainbow.
 const boxSelector=[
  ".site-nav",".forecast",".context>.manual-trade",
  ".training-card",".history-view .card",".api-card"
 ].join(",");
 const namespace="http://www.w3.org/2000/svg";
 const done=new WeakSet();
 let sequence=0;
 const make=(tag)=>document.createElementNS(namespace,tag);
 const resizeObserver=typeof ResizeObserver==="function"?
  new ResizeObserver(records=>{
   for(const item of records)sync(item.target);
  }):null;
 function sync(node){
  const svg=node.querySelector(":scope > svg.ke-border-overlay");
  if(!svg)return;
  // offsetWidth/Height include scrollbars and borders, unlike clientWidth.
  const w=Math.max(8,node.offsetWidth);
  const h=Math.max(8,node.offsetHeight);
  svg.setAttribute("viewBox","0 0 "+w+" "+h);
  svg.setAttribute("preserveAspectRatio","none");
  const css=getComputedStyle(node);
  const radius=parseFloat(css.borderTopLeftRadius)||0;
  const r=Math.min(Math.max(radius,2),w/2,h/2);
  const grad=svg.querySelector("linearGradient");
  if(grad){
   grad.setAttribute("x2",String(w));
   grad.setAttribute("y2",String(h));
  }
  const inset=2.5;
  for(const rect of svg.querySelectorAll("rect")){
   rect.setAttribute("x",String(inset));
   rect.setAttribute("y",String(inset));
   rect.setAttribute("width",String(Math.max(1,w-2*inset)));
   rect.setAttribute("height",String(Math.max(1,h-2*inset)));
   rect.setAttribute("rx",String(Math.max(0,r-inset)));
   rect.setAttribute("ry",String(Math.max(0,r-inset)));
   rect.setAttribute("pathLength","100");
  }
 }
 function decorate(node){
  if(!(node instanceof HTMLElement)||done.has(node))return;
  // Never nest a rainbow outline inside an already outlined panel.
  if(node.parentElement?.closest(".ke-neon-target"))return;
  done.add(node);
  node.classList.add("ke-neon-target");
  const svg=make("svg");
  svg.setAttribute("class","ke-border-overlay");
  svg.setAttribute("aria-hidden","true");
  svg.setAttribute("focusable","false");
  const defs=make("defs");
  const gradient=make("linearGradient");
  const id="ke-rainbow-gradient-"+(++sequence);
  gradient.setAttribute("id",id);
  gradient.setAttribute("gradientUnits","userSpaceOnUse");
  gradient.setAttribute("x1","0");
  gradient.setAttribute("y1","0");
  const stops=[
   ["0%","#33ffcb"],["20%","#2edbff"],["40%","#8368ff"],
   ["60%","#ff3abc"],["80%","#f6fc67"],["100%","#39ffb0"]
  ];
  for(const [position,color] of stops){
   const stop=make("stop");
   stop.setAttribute("offset",position);
   stop.setAttribute("stop-color",color);
   gradient.appendChild(stop);
  }
  defs.appendChild(gradient);
  svg.appendChild(defs);
  const border=make("rect");
  border.setAttribute("class","ke-neon-runner");
  border.setAttribute("stroke","url(#"+id+")");
  border.style.animationDelay=(-.41*(sequence%9))+"s";
  svg.appendChild(border);
  node.appendChild(svg);
  sync(node);
  if(resizeObserver)resizeObserver.observe(node);
 }
 function scan(root){
  if(!(root instanceof HTMLElement))return;
  if(root.matches(boxSelector))decorate(root);
  root.querySelectorAll(boxSelector).forEach(decorate);
 }
 scan(document.body);
 const mutationObserver=new MutationObserver(records=>{
  for(const record of records){
   for(const child of record.addedNodes){
    if(child instanceof HTMLElement)scan(child);
   }
  }
 });
 mutationObserver.observe(document.body,{subtree:true,childList:true});
 window.addEventListener("resize",()=>{
  for(const node of document.querySelectorAll(".ke-neon-target"))sync(node);
 },{passive:true});
})();
</script>"""
