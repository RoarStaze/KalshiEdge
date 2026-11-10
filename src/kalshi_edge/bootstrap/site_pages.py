"""Three clean, routed presentations of the existing local KalshiEdge UI.

The current forecast/training/history render functions still expect their DOM
IDs. Keep inactive views mounted but hidden and stop unnecessary refresh loops.
No AutoTrader controls or credential inputs are injected into any route.
"""
from __future__ import annotations


# Only /train receives these helpers. API credential pages must never
# include browser storage writes, even for unrelated non-secret progress data.
TRAIN_STATUS_SESSION_HELPERS = r"""// Navigation uses real page URLs; preserve the last rendered training
// status across page loads in this tab. Restore for display only, then verify.
const TRAIN_STATUS_CACHE_KEY="kalshiedge.train.status.v1";
function saveTrainingSnapshot(info){
 try{
  if(!info||typeof info!=="object"||Array.isArray(info))return;
  sessionStorage.setItem(TRAIN_STATUS_CACHE_KEY,JSON.stringify({
   savedAt:Date.now(),status:info,
  }));
 }catch(err){/* Storage can be disabled; normal server polling still works. */}
}
function restoreTrainingSnapshot(){
 try{
  const raw=sessionStorage.getItem(TRAIN_STATUS_CACHE_KEY);
  if(!raw)return false;
  const snapshot=JSON.parse(raw);
  if(!snapshot||typeof snapshot!=="object"||
     !Number.isFinite(snapshot.savedAt)||
     snapshot.savedAt>Date.now()+60000||
     Date.now()-snapshot.savedAt>12*60*60*1000||
     !snapshot.status||typeof snapshot.status!=="object"||
     Array.isArray(snapshot.status))return false;
  renderTraining(snapshot.status);
  // Cached readiness cannot authorize a new training session.
  el("train-now").disabled=true;
  text("train-feedback","Last known training progress · syncing with the active worker…");
  return true;
 }catch(err){return false;}
}
"""


STYLE = r"""/* KalshiEdge's routed, minimal three-page experience. */
:root{
 --bg:#080e13;--panel:#11191f;--panel2:#172128;--line:#273640;
 --text:#eff6f4;--muted:#98aab2;--mint:#23d9a7;--pink:#fb728c;
 --cyan:#74d2bb;--amber:#ebbe7d;--radius:20px;
 color-scheme:dark;
}
html{scroll-behavior:smooth}
/* Local panoramic artwork. The dashboard remains legible without the
   optional image: the last gradient is its native offline fallback. */
body{background:#020c10;position:relative;isolation:isolate}
body::before{
 content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
 background-image:
  linear-gradient(180deg,rgba(0,7,11,.03) 0%,rgba(0,8,12,.12) 55%,rgba(0,8,12,.45) 100%),
  url("/assets/kalshiedge-neon-city.webp"),
  radial-gradient(ellipse at 50% 15%,#073f3b 0%,#061d26 54%,#020910 100%);
 background-repeat:no-repeat;
 background-position:center center,center center,center center;
 background-size:cover,cover,cover;
}
body::after{
 content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
 background:linear-gradient(90deg,rgba(0,7,11,.08),rgba(0,10,14,.18) 25%,
  rgba(0,11,15,.24) 50%,rgba(0,10,14,.18) 75%,rgba(0,7,11,.08));
}
.shell{max-width:1510px;margin:auto;padding:0 30px 64px;position:relative;z-index:1}
/* Dark translucent market panels over the cyan/gold artwork. No full-screen blur. */
.site-nav{background:rgba(3,16,19,.91)!important;border-color:#1f756b75!important;
 box-shadow:0 8px 32px #000b,0 0 20px #20caa51b!important}
body[data-page="trade"] .forecast,
body[data-page="trade"] .context>.manual-trade{
 background:linear-gradient(155deg,rgba(2,27,30,.94),rgba(0,13,18,.96) 72%)!important;
 border-color:rgba(39,220,183,.43)!important;
 box-shadow:0 0 24px rgba(12,217,182,.14),0 24px 55px rgba(0,0,0,.6)!important;
}
body[data-page="trade"] .forecast .hero-price-surface{background:rgba(3,23,27,.89)}
body[data-page="train"] .training-card,
body[data-page="history"] .card,
body[data-page="api"] .card{
 background:linear-gradient(150deg,rgba(5,23,28,.94),rgba(5,16,22,.97))!important;
 border-color:rgba(37,164,139,.37)!important;
 box-shadow:0 20px 55px #0009,0 0 19px #0cd8ac10!important;
}
@media(max-width:700px){
 body::before{background-position:center center,center center,center center}
 body::after{background:rgba(0,9,13,.34)}
}
.site-header{display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);
 gap:22px;align-items:center;padding:22px 0;border-bottom:1px solid #ffffff18}
.identity{width:max-content;text-decoration:none;color:inherit}
.identity:hover .mark{transform:translateY(-2px) rotate(-3deg);box-shadow:0 10px 30px #28d6a01e}
.mark{background:linear-gradient(145deg,#24d8a6,#73dac6);border-radius:13px;
 color:#061915;width:42px;height:42px;font-size:23px;box-shadow:0 2px 18px #18bf8820;transition:transform .25s,box-shadow .25s}
.name{font-size:16px;letter-spacing:1.5px;font-weight:850}
.tag{letter-spacing:1.5px;font-size:9px}
.site-nav{display:flex;justify-content:center;gap:6px;padding:5px;background:#111a20;
 border:1px solid #26363d;border-radius:15px;box-shadow:0 8px 22px #0002}
.site-nav-link{min-width:99px;padding:11px 22px;border-radius:10px;text-decoration:none;
 font-weight:700;font-size:13px;letter-spacing:.15px;color:#9daeb5;
 text-align:center;transition:background .2s,color .2s,transform .2s}
.site-nav-link:hover{color:#f4fffa;background:#24343b;transform:translateY(-1px)}
.site-nav-link.is-active{background:#214137;color:#68ecc0;box-shadow:inset 0 0 0 1px #3b7c62a0}
.site-nav-link:focus-visible{outline:2px solid var(--mint)}
.header-right{justify-self:end;gap:10px;color:#a5b9bc;font-size:11px;white-space:nowrap}
.header-right .chip{background:#142c27;border-color:#28594b;color:#8eeac6;font-weight:800;letter-spacing:.7px}
.status-indicator.on{background:var(--mint);box-shadow:0 0 12px #23d9a755}
.section-intro{margin:38px 0 24px}
.eyebrow{color:#68c5ac;font-size:10px;letter-spacing:2px}
h1{font-size:clamp(30px,3.7vw,49px);font-weight:760;letter-spacing:-1.9px;line-height:1.13;margin:11px 0}
.subtitle{font-size:13px;color:#96aab1;line-height:1.75;max-width:780px}
.layout{grid-template-columns:minmax(0,1fr) minmax(0,1.06fr);gap:22px;align-items:stretch}
.card{background:linear-gradient(150deg,#141e24,#0f161c);border:1px solid #29363e;
 box-shadow:0 14px 50px #0000002b;border-radius:20px}
.forecast{min-height:665px;padding:26px 25px 24px;border-top:1px solid #367c6c}
.panel-label{color:#91dfc6}
.forecast .hero-price-surface{background:#0d1920;border:1px solid #273e44;border-radius:15px}
.forecast .hero-clock-panel{background:#222321;border-color:#67513b}
.forecast .lock-banner{background:#25251f;border-color:#6a5a3b;border-left-color:#e4b16b;border-radius:11px}
.forecast .lock-banner.pending{background:#1d2526;border-color:#4c5a57;border-left-color:#7ca69a}
.forecast .lock-banner.locked{background:#12372e;border-color:#316c57;border-left-color:#55c7a0}
.forecast .direction{font-weight:330}
.forecast .direction.up{color:#3fdfaf}.forecast .direction.down{color:#ff7b91}
.forecast .progress{background:#25383a}.forecast .progress>div{background:#46d5a8}
.forecast .record-note{background:#10221f;border-color:#315c50;border-left-color:#55c8a5}
.context{min-width:0;display:flex;flex-direction:column}
.context>.context-header,.context>.health{display:none!important}
.context>.manual-trade{min-height:100%;height:100%;background:linear-gradient(165deg,#15191e,#111519);
 border-color:#30383e;border-radius:20px;box-shadow:0 14px 50px #0000002b}
.manual-trade .mt-top{border-bottom-color:#2a3438}
/* Avoid stray grey hairline seams between rounded header controls and the
   two Trade cards. Keep the surrounding card borders and focus indicators. */
body[data-page="trade"] .site-header{border-bottom:0}
body[data-page="trade"] .manual-trade .mt-top{border-bottom:0}
.manual-trade .mt-pills{border-color:#34413f}
.manual-trade .mt-inputbox{border-color:#384246;background:#10191b}
.manual-trade .mt-action{background:#28d2a0;border:0;outline:0;box-shadow:0 10px 28px #18c98c1b;
 transition:transform .2s,filter .2s,box-shadow .2s}
.manual-trade .mt-action:hover:not(:disabled){transform:translateY(-1px);filter:brightness(1.06);
 box-shadow:0 12px 32px #18c98c30}
.manual-trade .mt-action:disabled{opacity:.48}
.manual-trade .mt-action.sell{background:#e3ece9}
.manual-trade .mt-row .mt-big{color:#eafaf1}
.manual-trade .mt-title{margin-bottom:24px}
.training-card{margin-top:0;border-top:1px solid #317b68}
.training-step{background:#111e22;border-color:#2d4444;border-radius:14px}
.training-step.active{background:#18372e;border-color:#3b9a76}
.training-count{background:#101c20;border-color:#2c4545;border-radius:12px}
.training-pill{background:#15352c;border-color:#36775d}
.train-button{border-radius:12px;background:#39d6a9;transition:filter .2s,transform .2s}
.train-button:hover:not(:disabled){transform:translateY(-1px)}
.history-header{margin:0 0 24px}
.history-header h2{letter-spacing:-.7px}
.kpi{padding:25px 22px}
.kpi-num{font-size:36px;font-weight:760}
.history-section{border-radius:18px}
.filter{background:#172328;border-color:#34484e;border-radius:10px}
th{background:#152329;color:#a8c0c0}
td{border-top-color:#27363d}
tr:hover td{background:#18292d}
footer{color:#81969a;max-width:1080px;margin:27px 0 0;font-size:11px}
body[data-page="trade"] .training-card,
body[data-page="trade"] .history-view,
body[data-page="train"] .layout,
body[data-page="train"] .history-view,
body[data-page="history"] .layout,
body[data-page="history"] .training-card{display:none!important}
body[data-page="history"] .history-view{display:block}
body[data-page="train"] .training-card{display:block}
@media (max-width:1100px){
 .site-header{grid-template-columns:minmax(0,1fr) auto;row-gap:18px}
 .site-nav{grid-column:1/-1;grid-row:2;justify-self:center}
 .header-right{grid-column:2;grid-row:1}
 .layout{grid-template-columns:minmax(0,1fr);gap:18px}
 .forecast{min-height:0}.context>.manual-trade{min-height:590px}
}
@media (max-width:700px){
 .shell{padding:0 15px 45px}
 .site-header{padding:17px 0;gap:15px}
 .identity .tag{display:none}
 .name{font-size:14px}
 .header-right .chip{display:none}
 .header-right{font-size:10px}
 .site-nav{width:100%;justify-self:stretch}
 .site-nav-link{flex:1;min-width:0;padding:12px 8px;font-size:13px}
 .section-intro{margin:26px 0 19px}
 .forecast{padding:19px}
 .training-card{padding:20px 17px}
 .history-header{margin:0 0 19px}
 .kpis{grid-template-columns:repeat(2,minmax(0,1fr))}
 .kpi{padding:17px}
 .kpi-num{font-size:26px}
 .context>.manual-trade{min-height:540px}
}
/* Trade page: zero unnecessary vertical scrolling on desktop. On small
   viewports the page can scroll rather than clip actions or key figures. */
body[data-page="trade"] .section-intro{display:none!important}
body[data-page="trade"] footer{display:none!important}
body[data-page="api"] .layout,
body[data-page="api"] .training-card,
body[data-page="api"] .history-view,
body[data-page="api"] footer{display:none!important}
body[data-page="api"] .section-intro{display:none!important}
@media(min-width:1101px) and (min-height:761px){
 body[data-page="trade"]{height:100dvh;overflow:hidden}
 body[data-page="trade"] .shell{display:flex;flex-direction:column;height:100dvh;min-height:0;
   max-height:100dvh;padding-bottom:13px}
 body[data-page="trade"] .site-header{flex:none;padding:13px 0}
 body[data-page="trade"] .layout{flex:1;min-height:0;gap:16px}
 body[data-page="trade"] .forecast{min-height:0;height:100%;padding:18px 22px 14px;overflow-y:auto;scrollbar-width:thin}
 body[data-page="trade"] .context{min-height:0;overflow:hidden}
 body[data-page="trade"] .context>.manual-trade{min-height:0;height:100%;overflow-y:auto;scrollbar-width:thin}
 body[data-page="trade"] .forecast .round-price-hero{margin-bottom:9px}
 body[data-page="trade"] .forecast .hero-price-grid{padding:12px 16px 10px}
 body[data-page="trade"] .forecast .hero-price-half+.hero-price-half{padding-left:14px;margin-left:12px}
 body[data-page="trade"] .forecast .hero-price-number{font-size:clamp(23px,4.6cqw,46px);margin:7px 0 3px}
 body[data-page="trade"] .forecast .hero-change{font-size:clamp(12px,2.8cqw,18px)}
 body[data-page="trade"] .forecast .hero-meta{font-size:10px}
 body[data-page="trade"] .forecast .hero-clock-panel{padding:10px 15px;margin:3px}
 body[data-page="trade"] .forecast .hero-clock-number{font-size:clamp(25px,5cqw,43px)}
 body[data-page="trade"] .forecast .hero-lock-time{font-size:clamp(14px,2.7cqw,22px)}
 body[data-page="trade"] .forecast .lock-banner{margin:12px 0 13px;padding:11px 13px}
 body[data-page="trade"] .forecast .direction{font-size:clamp(42px,4vw,61px);margin:8px 0}
 body[data-page="trade"] .forecast .direction.waiting{font-size:31px;margin:14px 0}
 body[data-page="trade"] .forecast .confidence-row{margin-top:13px}
 body[data-page="trade"] .forecast .confidence-value{font-size:30px}
 body[data-page="trade"] .forecast .progress{margin:10px 0 9px}
 body[data-page="trade"] .forecast .note{min-height:0;font-size:11px;line-height:1.5}
 body[data-page="trade"] .forecast .record-note{margin-top:13px;padding:10px 12px}
 body[data-page="trade"] .forecast details{margin-top:10px}
 body[data-page="trade"] .manual-trade .mt-top{height:54px}
 body[data-page="trade"] .manual-trade .mt-tab{padding:17px 0}
 body[data-page="trade"] .manual-trade .mt-main{padding:17px 19px}
 body[data-page="trade"] .manual-trade .mt-title{margin-bottom:12px}
 body[data-page="trade"] .manual-trade .mt-pills{margin-bottom:12px}
 body[data-page="trade"] .manual-trade .mt-inputbox{height:49px;margin:12px 0 8px}
 body[data-page="trade"] .manual-trade .mt-sub{margin-bottom:13px}
 body[data-page="trade"] .manual-trade .mt-rows{gap:10px;margin-bottom:15px}
}
@media(max-height:760px){
 body[data-page="trade"]{height:auto;overflow-y:auto}
 body[data-page="trade"] .shell{height:auto;max-height:none}
 body[data-page="trade"] .layout{height:auto;min-height:0}
}
@media(max-width:1100px){
 body[data-page="trade"]{overflow-y:auto}
 body[data-page="trade"] .layout{height:auto}
}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
"""


# Decorative-only motion. The background image remains an optional local asset.
# No JavaScript timers, remote assets or trading/data paths are involved.
LIVE_MOTION_STYLE = r"""
/* The soft-focus backdrop sits behind the original page; UI stays crisp. */
body::before{
 filter:blur(4px) saturate(1.08);
 transform:scale(1.08);
 animation:ke-background-drift 32s ease-in-out infinite alternate;
 will-change:transform;
}
.ke-atmosphere{
 position:fixed;inset:0;z-index:0;overflow:hidden;pointer-events:none;
 contain:layout style paint;
}
.ke-aurora{
 position:absolute;inset:-22%;
 background:
  radial-gradient(ellipse 36% 34% at 18% 43%,rgba(0,233,166,.24),transparent 78%),
  radial-gradient(ellipse 38% 42% at 83% 23%,rgba(17,162,195,.23),transparent 80%),
  radial-gradient(ellipse 54% 26% at 53% 85%,rgba(0,234,178,.16),transparent 80%);
 filter:blur(35px);opacity:.78;
 animation:ke-aurora-drift 25s ease-in-out infinite alternate;
 will-change:transform;
}
.ke-aurora.ke-aurora-alt{
 transform:scale(1.18) rotate(-7deg);opacity:.47;
 animation-direction:alternate-reverse;animation-duration:33s;animation-delay:-14s;
}
.ke-energy-grid{
 position:absolute;left:-20%;width:140%;height:65%;bottom:-30%;
 background-image:
  linear-gradient(90deg,rgba(45,231,183,.18) 1px,transparent 1px),
  linear-gradient(0deg,rgba(45,231,183,.17) 1px,transparent 1px);
 background-size:66px 66px;opacity:.28;
 transform:perspective(520px) rotateX(68deg);
 transform-origin:center bottom;
 animation:ke-grid-glimmer 16s ease-in-out infinite alternate;
}
.ke-particles{
 position:absolute;inset:-12%;
 background-image:
  radial-gradient(circle,rgba(132,255,214,.8) 0 1.3px,transparent 2.8px),
  radial-gradient(circle,rgba(47,213,194,.65) 0 1px,transparent 2.1px),
  radial-gradient(circle,rgba(71,184,228,.7) 0 1.1px,transparent 2.3px);
 background-position:21px 43px,117px 78px,190px 17px;
 background-size:263px 189px,347px 277px,419px 329px;
 opacity:.48;animation:ke-particle-drift 24s ease-in-out infinite alternate;
 will-change:transform;
}
.ke-market-trace{
 position:absolute;top:16%;left:-8%;width:116%;height:66%;
 opacity:.34;overflow:visible;
 filter:drop-shadow(0 0 7px rgba(30,246,191,.38));
}
.ke-market-trace path{
 fill:none;stroke:#36dfb3;stroke-width:1.4;stroke-linejoin:round;
 stroke-linecap:round;stroke-dasharray:10 22;
 animation:ke-market-pulse 17s linear infinite;
}
.ke-market-trace path:nth-child(2){
 stroke:#49a8d7;stroke-width:1;stroke-dasharray:6 26;
 animation-duration:23s;animation-direction:reverse;opacity:.55;
}
@keyframes ke-background-drift{
 from{transform:scale(1.08) translate3d(-.8%,.6%,0)}
 to{transform:scale(1.13) translate3d(1%,-.6%,0)}
}
@keyframes ke-aurora-drift{
 from{transform:translate3d(-3%,2%,0) rotate(-4deg) scale(1.04)}
 to{transform:translate3d(4%,-2%,0) rotate(4deg) scale(1.13)}
}
@keyframes ke-particle-drift{
 from{transform:translate3d(-2%,2%,0)}
 to{transform:translate3d(3%,-3%,0)}
}
@keyframes ke-grid-glimmer{
 from{opacity:.16;background-position:0 0,0 0}
 to{opacity:.32;background-position:33px 0,0 33px}
}
@keyframes ke-market-pulse{
 from{stroke-dashoffset:0}
 to{stroke-dashoffset:-288}
}
/* Each outlined panel gets a single continuously orbiting green light pulse,
   with the center cut away so the contents remain untouched/clickable. */
@property --ke-trace-angle{
 syntax:"<angle>";
 inherits:false;
 initial-value:0deg;
}
:where(
 .site-nav,.card,.manual-trade,.training-step,.training-count,.training-pill,
 .kpi,.history-section,.api-card,.api-status,
 .hero-price-surface,.hero-clock-panel,.lock-banner,.record-note,
 .mt-auto-record,.mt-auto-performance,.mt-inputbox,.mt-pills
){position:relative}
:where(
 .site-nav,.card,.manual-trade,.training-step,.training-count,.training-pill,
 .kpi,.history-section,.api-card,.api-status,
 .hero-price-surface,.hero-clock-panel,.lock-banner,.record-note,
 .mt-auto-record,.mt-auto-performance,.mt-inputbox,.mt-pills
)::before{
 content:"";position:absolute;inset:0;z-index:2;border-radius:inherit;
 border:1.5px solid transparent;pointer-events:none;
 background:conic-gradient(
  from var(--ke-trace-angle),
  transparent 0deg,rgba(29,245,177,.04) 233deg,
  rgba(33,246,177,.3) 268deg,#43ffc7 296deg,
  #a4ffe2 307deg,rgba(29,245,177,.45) 321deg,
  transparent 350deg
 ) border-box;
 -webkit-mask:linear-gradient(#000 0 0) padding-box,linear-gradient(#000 0 0);
 -webkit-mask-composite:xor;
 mask:linear-gradient(#000 0 0) padding-box,linear-gradient(#000 0 0);
 mask-composite:exclude;
 animation:ke-border-orbit 6.4s linear infinite;
}
@keyframes ke-border-orbit{
 to{--ke-trace-angle:360deg}
}
/* Frost the foreground glass, not the text or interactive controls. */
body[data-page] :is(.forecast,.context>.manual-trade,.training-card,
 .history-view .card,.api-card,.site-nav){
 backdrop-filter:blur(13px) saturate(1.14);
 -webkit-backdrop-filter:blur(13px) saturate(1.14);
}
body[data-page] :is(.forecast,.context>.manual-trade,.training-card,
 .history-view .card,.api-card){
 box-shadow:0 20px 65px rgba(0,0,0,.56),0 0 30px rgba(12,232,173,.12)!important;
}
@media(max-width:700px){
 .ke-market-trace{opacity:.22}
 .ke-energy-grid{opacity:.15}
 .ke-aurora{opacity:.5}
}
/* Reduced motion: the backdrop and tracer are visible but entirely static. */
@media (prefers-reduced-motion:reduce){
 body::before{animation:none!important;transform:scale(1.08);will-change:auto}
 .ke-atmosphere *{animation:none!important;will-change:auto}
 :where(.site-nav,.card,.manual-trade,.training-step,.training-count,
 .training-pill,.kpi,.history-section,.api-card,.api-status,
 .hero-price-surface,.hero-clock-panel,.lock-banner,.record-note,
 .mt-auto-record,.mt-auto-performance,.mt-inputbox,.mt-pills)::before{
  animation:none!important;
 }
}
"""

LIVE_MOTION_MARKUP = r"""<div class="ke-atmosphere" aria-hidden="true">
 <div class="ke-aurora"></div>
 <div class="ke-aurora ke-aurora-alt"></div>
 <div class="ke-energy-grid"></div>
 <div class="ke-particles"></div>
 <svg class="ke-market-trace" viewBox="0 0 1200 500" preserveAspectRatio="none" focusable="false">
  <path d="M0 355 L105 342 L170 372 L238 294 L302 318 L380 270 L462 287 L535 212 L612 250 L698 193 L752 216 L833 153 L918 170 L985 124 L1060 147 L1140 78 L1200 95"/>
  <path d="M0 405 L90 382 L158 390 L226 359 L309 375 L388 320 L460 336 L555 290 L626 308 L698 268 L780 287 L855 246 L936 256 L1018 201 L1093 218 L1200 174"/>
 </svg>
</div>"""

NAV = """<nav class="site-nav" aria-label="Primary navigation">
 <a class="site-nav-link" href="/trade">Trade</a>
 <a class="site-nav-link" href="/train">Train</a>
 <a class="site-nav-link" href="/history">History</a>
 <a class="site-nav-link" href="/api">API</a>
</nav>"""

PAGE_META = {
    "trade": ("TRADE / LIVE MARKETS", "Bitcoin, in real time.",
              "One live round forecast. One manual order panel. Every decision stays yours."),
    "train": ("TRAIN / ADAPTIVE LEARNING", "Built to keep learning.",
              "Review model readiness, learning cycles and controlled manual training."),
    "history": ("HISTORY / VERIFIED PERFORMANCE", "Every call. On the record.",
                "Explore saved final forecasts and settlement-verified performance."),
    "api": ("ACCOUNT / CONNECTION", "Secure API setup.",
            "Encrypted local Kalshi API credentials."),
}


def render_site_page(page: str, section: str) -> str:
    """Present Trade / Train / History as real local routes, preserving API IDs."""
    if section not in PAGE_META:
        raise ValueError("Unknown page route")
    checks = (
        '<body>', '</style>', '<header>', '</header>',
        '<div class="section-intro">', '<section aria-label="Final Forecast history">',
        'id="manual-trade"', 'id="train-now"', 'id="history-rows"',
        '<span class="chip">MANUAL TRADING</span>',
        'refreshLive();refreshHistory();refreshTraining();',
        'setInterval(refreshLive,750);setInterval(refreshHistory,5000);setInterval(refreshTraining,5000);setInterval(clock,250);',
        '<script>\n(function(){\n const byId=id=>document.getElementById(id);',
    )
    if any(page.count(value) != 1 for value in checks):
        raise ValueError("Dashboard structure changed: cannot safely route pages")
    if section == "train":
        base_helpers = ('function saveTrainingSnapshot(info){}\n'
                        'function restoreTrainingSnapshot(){return false;}\n')
        if page.count(base_helpers) != 1:
            raise ValueError("Train cache hook missing from dashboard")
        page = page.replace(base_helpers, TRAIN_STATUS_SESSION_HELPERS, 1)
    page = page.replace('</style>', STYLE + LIVE_MOTION_STYLE + '\n</style>', 1)
    page = page.replace('<body>', f'<body data-page="{section}">\n' + LIVE_MOTION_MARKUP, 1)
    page = page.replace('<header>', '<header class="site-header">', 1)
    # The AutoTrader UI is intentionally not installed on this page. Remove the
    # old launcher too, while preserving the signed order and train endpoints.
    old_link = '<a href="http://127.0.0.1:8898/" title="Open local paper-only AutoTrader (start it separately)">AutoTrader (paper)</a>'
    if page.count(old_link) != 1:
        raise ValueError("AutoTrader header link not found")
    page = page.replace(old_link, '', 1)
    # Insert the three route links between brand and feed status.
    page = page.replace('<div class="header-right">', NAV + '\n <div class="header-right">', 1)
    page = page.replace('<span class="chip">MANUAL TRADING</span>',
                        '<span class="chip">LIVE ACCOUNT</span>', 1)
    eyebrow, heading, sub = PAGE_META[section]
    original_intro = ('<div class="section-intro"><div class="eyebrow">LIVE PREDICTION ENGINE</div>'
                      '<h1>Bitcoin market outlook</h1>\n'
                      '<p class="subtitle">Live model estimates, one immutable final forecast per market, '
                      'and settlement-verified history. User-initiated real orders; no automatic trades.</p></div>')
    if page.count(original_intro) != 1:
        raise ValueError("Intro layout changed; refusing incomplete redesign")
    new_intro = (f'<div class="section-intro"><div class="eyebrow">{eyebrow}</div>'
                 f'<h1>{heading}</h1><p class="subtitle">{sub}</p></div>')
    page = page.replace(original_intro, new_intro, 1)
    page = page.replace('<section aria-label="Final Forecast history">',
                        '<section class="history-view" aria-label="Final Forecast history">', 1)
    label = "API" if section == "api" else section.title()
    page = page.replace('<title>KalshiEdge — Live Forecast &amp; History</title>',
                        f'<title>KalshiEdge · {label}</title>', 1)
    # Show the real controls/results immediately; these top-of-page marketing
    # intros duplicate information already supplied by the navigation and cards.
    # API onboarding retains its distinct heading and explanation.
    if section in ("trade", "train", "history"):
        page = page.replace(new_intro, "", 1)
    if section == "history":
        # Preserve the live history-status element, which the JS updates,
        # while removing the second redundant title/description block.
        redundant_history_heading = (
            '<div class="history-header"><div><div class="eyebrow">'
            'VERIFIED PERFORMANCE</div><h2>Final forecast history</h2>'
            '<p class="subtitle">Official Kalshi settlements only · '
            'Accuracy = correct ÷ (correct + incorrect)</p></div>'
            '<span id="history-status" class="subtitle">Loading history…</span></div>'
        )
        if page.count(redundant_history_heading) != 1:
            raise ValueError("History header structure changed: refusing partial removal")
        page = page.replace(
            redundant_history_heading,
            '<div class="history-header">'
            '<span id="history-status" class="subtitle">Loading history…</span>'
            '</div>',
            1,
        )
    # Only one route is active. The links use real URLs, not in-page fake tabs.
    active = f'<a class="site-nav-link" href="/{section}">{label}</a>'
    page = page.replace(active,
                        f'<a class="site-nav-link is-active" href="/{section}" aria-current="page">{label}</a>', 1)
    # Replace the legacy read-only disclaimer with route-accurate copy.
    footer_start = '<footer>Read-only research tool.'
    footer_end = '</footer>'
    start = page.find(footer_start)
    end = page.find(footer_end, start)
    if start < 0 or end < 0:
        raise ValueError("Legacy footer not found")
    disclaimers = {
        "trade": "Trading is manual and uses real money. Prices, fees and displayed payouts can change until the exchange confirms your fill.",
        "train": "Models train only when requested. Promotions require the existing independent evaluation checks.",
        "history": "Accuracy includes settled correct and incorrect directional forecasts only. Pending and SKIP results are excluded.",
        "api": "Keys are encrypted locally with Windows DPAPI. Do not share your private key.",
    }
    page = page[:start] + '<footer>' + disclaimers[section] + footer_end + page[end + len(footer_end):]
    # Background GETs for inactive views needlessly waste API budget.
    old_poll = ('refreshLive();refreshHistory();refreshTraining();\n'
                'setInterval(refreshLive,750);setInterval(refreshHistory,5000);'
                'setInterval(refreshTraining,5000);setInterval(clock,250);')
    new_poll = ('const sitePage=document.body.dataset.page;\n'
                ' if(sitePage==="trade"){refreshLive();setInterval(refreshLive,1000);setInterval(clock,250);}\n'
                ' if(sitePage==="train"){refreshLive();restoreTrainingSnapshot();refreshTraining();setInterval(refreshLive,5000);setInterval(refreshTraining,5000);setInterval(refreshTrainingProgress,750);}\n'
                ' if(sitePage==="history"){refreshLive();refreshHistory();setInterval(refreshLive,5000);setInterval(refreshHistory,5000);}')
    if page.count(old_poll) != 1:
        raise ValueError("Refresh loops changed")
    page = page.replace(old_poll, new_poll, 1)
    # The order card should never initialize account polling on Train/History.
    page = page.replace(
        '<script>\n(function(){\n const byId=id=>document.getElementById(id);',
        '<script>\n(function(){\n if(document.body.dataset.page !== "trade")return;\n const byId=id=>document.getElementById(id);',
        1,
    )
    return page
