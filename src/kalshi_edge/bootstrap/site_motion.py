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
/* The rainbow is the card's own ACTUAL CSS border-box background.
   SVG children positioned within the padding/scroll area caused inset rims,
   especially on the scrolling Trade cards. No overlay can clip or drift now. */
@property --ke-rainbow-angle{
 syntax:"<angle>";
 inherits:false;
 initial-value:0deg;
}
body[data-page] .ke-neon-target{
 position:relative;
 --ke-rainbow-panel:linear-gradient(155deg,rgba(3,17,23,.86),rgba(2,11,19,.91) 74%);
 border:2px solid transparent!important;
 border-radius:inherit;
 background:
  var(--ke-rainbow-panel) padding-box,
  conic-gradient(
   from var(--ke-rainbow-angle),
   #31ffcc 0deg,#2edbff 62deg,#8368ff 127deg,
   #ff3abc 192deg,#f6fc67 263deg,#39ffb0 322deg,#31ffcc 360deg
  ) border-box!important;
 animation:ke-rainbow-cycle 5s linear infinite;
}
body[data-page] .site-nav.ke-neon-target{
 --ke-rainbow-panel:linear-gradient(155deg,rgba(3,19,24,.91),rgba(2,13,21,.91));
}
@keyframes ke-rainbow-cycle{
 to{--ke-rainbow-angle:360deg}
}
@media(prefers-reduced-motion:reduce){
 body::before{animation:none!important;transform:scale(1.09);will-change:auto}
 body[data-page] .ke-neon-target{
  animation:ke-rainbow-cycle 9s linear infinite!important;
 }
}
"""

# No artificial particles, beams, grids, canvas or floating moving objects.
MOTION_MARKUP = ""

MOTION_SCRIPT = r"""<script>
(function(){
 "use strict";
 // Only OUTER panels, never child headings, inputs, price panels or controls.
 const boxSelector=[
  ".site-nav",".forecast",".context>.manual-trade",
  ".training-card",".history-view .card",".api-card"
 ].join(",");
 const done=new WeakSet();
 function decorate(node){
  if(!(node instanceof HTMLElement)||done.has(node))return;
  if(node.parentElement?.closest(".ke-neon-target"))return;
  done.add(node);
  node.classList.add("ke-neon-target");
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
})();
</script>"""
