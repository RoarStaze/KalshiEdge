"""High-visibility live price and round timer on the existing dashboard.

Uses the existing /api/live Kalshi BRTI snapshot. This is display-only; do
not alter live.py, settlement rules, protected V5 or frozen V6.1 hashes.
"""
from __future__ import annotations


HERO_STYLE = r"""
/* Prominent live prices and orange round clock, based on the approved mockup. */
.round-price-hero{padding:24px 28px 22px;margin-bottom:18px;border-top:2px solid var(--cyan)}
.hero-heading-row{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-bottom:20px;flex-wrap:wrap}
.hero-heading-row h2{font-size:21px;letter-spacing:-.55px;margin:6px 0 0}
.hero-freshness{font-size:11px;letter-spacing:.6px;color:var(--muted);padding:7px 10px;border:1px solid var(--line);border-radius:18px}
.hero-freshness.fresh{color:#27e5ac;border-color:#245849;background:#122f2c}
.hero-freshness.stale{color:#ffbd87;border-color:#66503a;background:#322920}
.hero-price-grid{display:grid;grid-template-columns:1fr 1fr;border:1px solid #30495d;border-radius:14px;padding:24px 28px;background:linear-gradient(135deg,#0d1928,#112131);gap:0}
.hero-price-half{min-width:0}
.hero-price-half+ .hero-price-half{padding-left:32px;border-left:1px solid #304354;margin-left:30px}
.hero-price-label{font-size:12px;font-weight:800;letter-spacing:2px;color:#9db5ca;display:flex;align-items:center;gap:8px}
.hero-info{display:inline-grid;place-items:center;width:17px;height:17px;border-radius:50%;border:1.5px solid #667f93;color:#91b0ca;font-size:11px;letter-spacing:0;font-weight:750}
.hero-price-number{display:block;font-size:clamp(30px,4vw,57px);line-height:1.14;margin:15px 0 9px;font-weight:800;letter-spacing:-1.4px;font-variant-numeric:tabular-nums;white-space:nowrap}
.hero-price-number.up{color:#21dfa5;text-shadow:0 0 20px #21dfa515}
.hero-price-number.down{color:#ff7489;text-shadow:0 0 20px #ff748915}
.hero-price-number.flat{color:#dcebf6}
.hero-change{font-size:clamp(18px,2.25vw,29px);font-weight:650;letter-spacing:-.45px;font-variant-numeric:tabular-nums;line-height:1.3;min-height:35px}
.hero-change.up{color:#21dfa5}.hero-change.down{color:#ff7489}.hero-change.flat{color:#aabdd0}
.hero-meta{font-size:12px;color:#9db4c7;line-height:1.55}
.hero-meta.source{margin-top:7px;font-size:11px}
.hero-clock-panel{display:flex;align-items:center;justify-content:space-between;gap:18px;border-radius:12px;margin-top:14px;padding:17px 23px;background:linear-gradient(125deg,#302923,#211e20);border:1px solid #965e38}
.hero-clock-left{display:flex;align-items:center;gap:17px}
.hero-hourglass{width:34px;height:43px;color:#ff8d45;flex:none}
.hero-clock-label{color:#adc0ce;letter-spacing:1.7px;font-size:11px;font-weight:750}
.hero-clock-number{font-variant-numeric:tabular-nums;font-size:clamp(35px,4vw,51px);line-height:1.12;font-weight:800;letter-spacing:1px;color:#ff913e;margin-top:3px}
.hero-clock-right{border-left:1px solid #53463e;padding-left:25px;min-width:180px}
.hero-lock-time{color:#f5f6f7;font-size:24px;font-weight:600;letter-spacing:-.3px;font-variant-numeric:tabular-nums;margin-top:7px}
@media(max-width:1100px){.hero-price-grid{padding:20px}.hero-price-half+.hero-price-half{padding-left:22px;margin-left:22px}.hero-price-number{font-size:clamp(27px,4.1vw,48px)}}
@media(max-width:720px){.round-price-hero{padding:20px 17px}.hero-price-grid{grid-template-columns:1fr;gap:20px;padding:20px}.hero-price-half+.hero-price-half{border-left:0;border-top:1px solid #304354;margin-left:0;padding:19px 0 0}.hero-price-number{font-size:clamp(34px,8vw,50px)}.hero-clock-panel{padding:16px;flex-wrap:wrap}.hero-clock-right{min-width:0;flex:1}.hero-clock-number{font-size:38px}}
@media(max-width:420px){.hero-clock-panel{align-items:stretch}.hero-clock-right{border-left:0;border-top:1px solid #53463e;padding:13px 0 0;width:100%;flex:auto}.hero-price-label{font-size:11px}.hero-price-number{font-size:35px}}
"""

HERO_MARKUP = r"""
<section class="card round-price-hero" id="round-price-hero" aria-label="Current Bitcoin price compared with Kalshi price to beat">
 <div class="hero-heading-row">
  <div><div class="eyebrow">THIS ROUND · LIVE PRICE MONITOR</div><h2>Bitcoin price to beat</h2></div>
  <div class="hero-freshness" id="hero-price-freshness" role="status">Waiting for BRTI</div>
 </div>
 <div class="hero-price-grid">
  <div class="hero-price-half">
   <div class="hero-price-label">PRICE TO BEAT <span class="hero-info" title="Published strike for the active 15-minute Kalshi market" aria-label="Published Kalshi strike">i</span></div>
   <strong class="hero-price-number" id="hero-strike">—</strong>
   <div class="hero-meta" id="hero-strike-time">Waiting for the next round</div>
  </div>
  <div class="hero-price-half">
   <div class="hero-price-label">CURRENT PRICE <span class="hero-info" title="Latest observed Kalshi BRTI index; indicative, not an executable trade quote" aria-label="Kalshi BRTI reference index">i</span></div>
   <strong class="hero-price-number flat" id="hero-current">—</strong>
   <div class="hero-change flat" id="hero-change"><span id="hero-delta">—</span> <span id="hero-pct"></span></div>
   <div class="hero-meta source" id="price-source">Kalshi BRTI · reference index, not an executable price</div>
  </div>
 </div>
 <div class="hero-clock-panel">
  <div class="hero-clock-left">
   <svg class="hero-hourglass" viewBox="0 0 32 40" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 2h22M5 38h22M8 3v7c0 7 8 10 8 10S24 17 24 10V3M8 37v-7c0-7 8-10 8-10s8 3 8 10v7M11 12h10M16 27l-5 7h10z"/></svg>
   <div><div class="hero-clock-label">ROUND CLOSES IN</div><div class="hero-clock-number" id="hero-countdown">—</div></div>
  </div>
  <div class="hero-clock-right">
   <div class="hero-clock-label" id="hero-lock-label">FORECAST LOCKS AT</div>
   <div class="hero-lock-time" id="hero-lock-at">—</div>
  </div>
 </div>
</section>
"""

HERO_SCRIPT = r"""
/* Observe the same 1-second BRTI stream as the predictor; never synthesize ticks. */
let lastMarketTicker=null;
let lastObservedPrice=null;
let lastObservedTs=null;
let priceMovement=0;
let heroClockCloseMs=null;
let heroClockBaseRemainingMs=null;
let heroClockUpdatedMono=null;

function updateRoundPriceClock(){
 const element=el("hero-countdown");
 if(heroClockBaseRemainingMs===null||heroClockUpdatedMono===null){
   element.textContent="—";return;
 }
 const remaining=Math.max(0,heroClockBaseRemainingMs-(performance.now()-heroClockUpdatedMono));
 const seconds=Math.max(0,Math.ceil(remaining/1000));
 element.textContent=String(Math.floor(seconds/60)).padStart(2,"0")+":"+String(seconds%60).padStart(2,"0");
}

function renderRoundPriceHero(data){
 const prediction=data.latest_prediction;
 const ticker=data.market_ticker;
 const newMarket=ticker!==lastMarketTicker;
 if(newMarket){
   lastMarketTicker=ticker;
   lastObservedPrice=null;
   lastObservedTs=null;
   priceMovement=0;
 }
 const strike=Number(data.strike);
 const validMarket=typeof ticker==="string"&&ticker.startsWith("KXBTC15M-")
    &&Number.isFinite(strike)&&strike>0
    &&Number.isFinite(data.market_close_ts_ns)&&Number.isFinite(data.checkpoint_ts_ns);
 text("hero-strike",validMarket?money(strike):"—");
 text("hero-strike-time",validMarket
    ?new Date(data.market_close_ts_ns/1e6).toLocaleTimeString("en-US",{timeZone:"America/New_York",hour:"numeric",minute:"2-digit"})+" ET · market closes"
    :"Waiting for the next round");
 heroClockCloseMs=validMarket?data.market_close_ts_ns/1e6:null;
 heroClockBaseRemainingMs=validMarket&&Number.isFinite(data.server_time_ns)
    ?Math.max(0,(data.market_close_ts_ns-data.server_time_ns)/1e6):null;
 heroClockUpdatedMono=heroClockBaseRemainingMs===null?null:performance.now();
 updateRoundPriceClock();
 const locked=!!data.final_forecast;
 text("hero-lock-label",locked?"FORECAST LOCKED AT":"FORECAST LOCKS AT");
 text("hero-lock-at",validMarket
    ?new Date(data.checkpoint_ts_ns/1e6).toLocaleTimeString("en-US",{timeZone:"America/New_York",hour:"numeric",minute:"2-digit"})+" ET"
    :"—");

 const price=prediction?.brti_value;
 const observedNs=prediction?.brti_source_ts_ns;
 const sourceAgeMs=Number.isFinite(observedNs)&&Number.isFinite(data.server_time_ns)
    ?(data.server_time_ns-observedNs)/1e6:Infinity;
 const livePrice=validMarket&&prediction?.market_ticker===ticker
    &&typeof price==="number"&&Number.isFinite(price)&&price>0
    &&Number.isFinite(observedNs)&&sourceAgeMs>=-2000&&sourceAgeMs<=6000;
 if(!livePrice){
   text("hero-current","—");
   text("hero-delta","—");
   text("hero-pct","");
   el("hero-current").className="hero-price-number flat";
   el("hero-change").className="hero-change flat";
   const state=el("hero-price-freshness");
   state.className="hero-freshness stale";
   state.textContent=validMarket?"BRTI unavailable or stale":"Waiting for active market";
   lastObservedPrice=null;lastObservedTs=null;priceMovement=0;
   return;
 }
 if(lastObservedTs===null||observedNs>lastObservedTs){
   if(lastObservedPrice!==null){
     priceMovement=price>lastObservedPrice?1:price<lastObservedPrice?-1:0;
   }else{
     priceMovement=0;
   }
   lastObservedPrice=price;
   lastObservedTs=observedNs;
 }
 // Tick movement drives current price color; the dollar/percent gap is
 // ALWAYS computed relative to the immutable Kalshi strike.
 const delta=price-strike;
 const diffSign=delta>0?"+":delta<0?"−":"";
 const diffClass=delta>0?"up":delta<0?"down":"flat";
 const tickClass=priceMovement>0?"up":priceMovement<0?"down":"flat";
 text("hero-current",money(price));
 el("hero-current").className="hero-price-number "+tickClass;
 text("hero-delta",diffSign+"$"+Math.abs(delta).toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2}));
 text("hero-pct","("+diffSign+(Math.abs(delta)/strike*100).toFixed(3)+"%)");
 el("hero-change").className="hero-change "+diffClass;
 const state=el("hero-price-freshness");
 state.className="hero-freshness fresh";
 state.textContent="LIVE BRTI · "+Math.max(0,Math.round(sourceAgeMs/1000))+"s old";
}
"""


def integrate_round_price_hero(page: str) -> str:
    """Add full-width hero without changing existing forecast data or endpoints."""
    insertion = '<section class="layout" aria-label="Current round">'
    if page.count(insertion) != 1 or page.count("</style>") != 1:
        raise ValueError("main dashboard layout or stylesheet anchor changed")
    if page.count("function renderLive(data){") != 1 or page.count("function clock(){") != 1:
        raise ValueError("main dashboard live/clock integration anchor changed")
    page = page.replace("</style>", HERO_STYLE + "\n</style>", 1)
    page = page.replace(insertion, HERO_MARKUP + "\n" + insertion, 1)
    page = page.replace("function renderLive(data){", HERO_SCRIPT + "\nfunction renderLive(data){\n renderRoundPriceHero(data);", 1)
    page = page.replace("function clock(){", "function clock(){updateRoundPriceClock();", 1)
    return page
