"""High-visibility live price and round timer on the existing dashboard.

Uses the existing /api/live Kalshi BRTI snapshot. This is display-only; do
not alter live.py, settlement rules, protected V5 or frozen V6.1 hashes.
"""
from __future__ import annotations


HERO_STYLE = r"""
/* Reference-image styling is confined to the ORIGINAL forecast card.
   Never override .layout columns, .context or the outer card's padding/width. */
.forecast{container-type:inline-size}
.forecast .round-price-hero{width:100%;min-width:0;margin:0 0 19px}
.forecast .hero-price-surface{border:1px solid #345269;border-radius:12px;background:linear-gradient(135deg,#0d1e2e,#0d1927);overflow:hidden}
.forecast .hero-price-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));padding:15px 13px 13px;gap:0}
.forecast .hero-price-half{min-width:0}
.forecast .hero-price-half+.hero-price-half{border-left:1px solid #345269;padding-left:12px;margin-left:10px}
.forecast .hero-price-label{display:flex;align-items:center;gap:5px;font-size:clamp(9px,2.4cqw,16px);line-height:1.35;font-weight:800;letter-spacing:.6px;color:#a3bdd9;white-space:nowrap}
.forecast .hero-info{display:inline-grid;place-items:center;width:15px;height:15px;flex:none;border:1.5px solid #6984a1;border-radius:50%;color:#93b1ce;font-size:10px;font-weight:800;letter-spacing:0}
.forecast .hero-price-number{display:block;margin:11px 0 9px;font-size:clamp(18px,6.15cqw,72px);font-weight:800;line-height:1.12;letter-spacing:-.9px;font-variant-numeric:tabular-nums;white-space:nowrap}
.forecast .hero-price-number.up{color:#18dfa0;text-shadow:0 0 20px #18dfa01a}
.forecast .hero-price-number.down{color:#ff647e;text-shadow:0 0 20px #ff647e1a}
.forecast .hero-price-number.flat{color:#f5f9fc}
.forecast .hero-change{min-height:20px;line-height:1.4;font-weight:700;font-size:clamp(11px,3cqw,30px);white-space:nowrap;letter-spacing:-.2px;font-variant-numeric:tabular-nums}
.forecast .hero-change.up{color:#18dfa0}.forecast .hero-change.down{color:#ff647e}.forecast .hero-change.flat{color:#acc0d5}
.forecast .hero-meta{font-size:clamp(10px,2.15cqw,17px);color:#9db8d5;line-height:1.45}
.forecast .hero-meta.source{font-size:10px;color:#819fb6;margin-top:2px}
.forecast .hero-freshness{font-size:9px;line-height:1.3;color:#9bb6cc;margin-top:3px}
.forecast .hero-freshness.fresh{color:#81e4bd}.forecast .hero-freshness.stale{color:#f6ad79}
.forecast .hero-clock-panel{display:grid;grid-template-columns:minmax(0,1.12fr) minmax(0,.88fr);align-items:center;gap:6px;background:linear-gradient(120deg,#342b27,#282424);border:1px solid #a46539;border-radius:9px;padding:12px 12px;margin:3px 2px 2px;min-width:0}
.forecast .hero-clock-left{display:flex;gap:9px;min-width:0;align-items:center}
.forecast .hero-hourglass{width:clamp(18px,5cqw,47px);height:clamp(26px,6.6cqw,64px);color:#ff8b40;flex:none}
.forecast .hero-clock-label{color:#afc1d4;letter-spacing:.6px;font-size:clamp(8px,2cqw,16px);font-weight:800;white-space:nowrap}
.forecast .hero-clock-number{color:#ff923f;font-size:clamp(26px,7cqw,67px);line-height:1.08;font-weight:850;letter-spacing:.1px;margin-top:3px;font-variant-numeric:tabular-nums;white-space:nowrap}
.forecast .hero-clock-right{border-left:1px solid #614c43;padding-left:10px;min-width:0}
.forecast .hero-lock-time{color:#f5f4f2;font-size:clamp(13px,3.65cqw,34px);font-weight:600;white-space:nowrap;font-variant-numeric:tabular-nums;letter-spacing:-.4px;margin-top:7px}
.forecast .record-note{border:1px solid #345167;border-left:3px solid #7da1bd;border-radius:8px;background:#112334;padding:13px 14px;margin-top:18px}
.forecast .record-title{font-size:12px}
@container (min-width:700px){
 .forecast .hero-price-grid{padding:30px 35px 23px}
 .forecast .hero-price-half+.hero-price-half{padding-left:35px;margin-left:30px}
 .forecast .hero-price-number{letter-spacing:-1.7px;margin:19px 0 10px}
 .forecast .hero-clock-panel{padding:21px 32px;margin:4px}
 .forecast .hero-clock-left{gap:20px}
 .forecast .hero-clock-right{padding-left:27px}
 .forecast .hero-price-label{letter-spacing:1.6px}
 .forecast .round-price-hero{margin-bottom:30px}
}
@container (max-width:335px){
 .forecast .hero-price-grid{grid-template-columns:1fr;gap:15px}
 .forecast .hero-price-half+.hero-price-half{border-left:0;border-top:1px solid #345269;padding:13px 0 0;margin:0}
 .forecast .hero-price-number{font-size:clamp(29px,9cqw,45px)}
 .forecast .hero-clock-panel{grid-template-columns:1fr;gap:10px}
 .forecast .hero-clock-right{border-left:0;border-top:1px solid #614c43;padding:9px 0 0}
}
"""

HERO_MARKUP = r"""
<section class="round-price-hero" id="round-price-hero" aria-label="Current Bitcoin price compared with Kalshi price to beat">
 <div class="hero-price-surface">
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
    <div class="hero-meta source" id="price-source">Kalshi BRTI · reference index</div>
    <div class="hero-freshness" id="hero-price-freshness" role="status">Waiting for BRTI</div>
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
    """Place approved visual directly inside the original full-width forecast.

    Keep every existing model/feed/lock API untouched. Do not duplicate the
    clock beneath the model confidence, and preserve all AutoTrader inserts.
    """
    forecast_anchor = '  <div class="direction waiting" id="direction">WAITING</div>'
    old_times = """  <div class="two-times">
   <div><div class="time-label">Round closes in</div><div class="time-value" id="round-countdown">—</div></div>
   <div class="right"><div class="time-label" id="lock-time-label">Forecast locks at</div><div class="time-value" id="lock-time">—</div></div>
  </div>
"""
    old_clock = 'function clock(){if(!live||!live.market_close_ts_ns)return;text("round-countdown",countdown(live.market_close_ts_ns/1e6))}'
    old_lock_render = """ if(data.market_close_ts_ns){
   text("lock-time",local(data.checkpoint_ts_ns/1e6));
   text("lock-time-label",locked?"Locked at":"Forecast locks at");
 }else{text("lock-time","—");text("lock-time-label","Forecast locks at")}
 clock();"""
    anchors = (
        forecast_anchor, old_times, old_clock, old_lock_render,
        "function renderLive(data){", "</style>",
        '<article class="card forecast">',
    )
    if any(page.count(anchor) != 1 for anchor in anchors):
        raise ValueError("main forecast markup has changed; refusing to misplace hero")
    page = page.replace("</style>", HERO_STYLE + "\n</style>", 1)
    page = page.replace(forecast_anchor, HERO_MARKUP + "\n" + forecast_anchor, 1)
    page = page.replace(old_times, "", 1)
    page = page.replace(old_clock, "function clock(){updateRoundPriceClock();}", 1)
    page = page.replace(old_lock_render, " clock();", 1)
    page = page.replace(
        "function renderLive(data){",
        HERO_SCRIPT + "\nfunction renderLive(data){\n renderRoundPriceHero(data);",
        1,
    )
    return page
