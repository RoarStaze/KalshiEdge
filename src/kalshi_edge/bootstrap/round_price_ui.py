"""High-visibility live price and round timer on the existing dashboard.

Uses the existing /api/live Kalshi BRTI snapshot. This is display-only; do
not alter live.py, settlement rules, protected V5 or frozen V6.1 hashes.
"""
from __future__ import annotations


HERO_STYLE = r"""
/* Approved forecast mockup: prices and orange clock belong INSIDE forecast. */
.layout{grid-template-columns:minmax(0,1fr)}
.layout>.forecast{min-width:0;min-height:0}
.layout>.context{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(270px,1fr);align-items:stretch}
.forecast{padding:clamp(22px,4vw,48px)}
.forecast>.panel-label{font-size:clamp(12px,1.4vw,18px);font-weight:800;letter-spacing:3px;color:#a8c7e7}
.forecast>.lock-banner{margin:27px 0 28px;padding:19px 27px 20px;border-radius:12px;border-width:2px;border-left-width:5px;background:linear-gradient(130deg,#342d29,#292726)}
.forecast .lock-heading{font-size:clamp(14px,1.65vw,23px);letter-spacing:.5px}
.forecast .lock-desc{font-size:clamp(13px,1.45vw,19px);margin-top:10px}
.forecast .round-price-hero{width:100%;min-width:0;margin:0 0 34px}
.hero-price-surface{background:linear-gradient(135deg,#0f2030,#0c1724);border:2px solid #304b62;border-radius:17px;overflow:hidden}
.hero-price-grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:0;padding:28px 38px 23px}
.hero-price-half{min-width:0}
.hero-price-half+.hero-price-half{border-left:1px solid #345165;margin-left:27px;padding-left:35px}
.hero-price-label{font-size:clamp(11px,1.5vw,19px);font-weight:800;letter-spacing:2px;color:#a1bbd8;display:flex;align-items:center;gap:13px}
.hero-info{display:inline-grid;place-items:center;width:24px;height:24px;border-radius:50%;border:2px solid #678099;color:#97b3cc;font-size:14px;letter-spacing:0;font-weight:800}
.hero-price-number{display:block;min-width:0;font-size:clamp(32px,5.7vw,78px);line-height:1.1;margin:20px 0 13px;font-weight:800;letter-spacing:-2.4px;font-variant-numeric:tabular-nums;white-space:nowrap}
.hero-price-number.up{color:#18df99;text-shadow:0 0 28px #18df9920}
.hero-price-number.down{color:#ff667f;text-shadow:0 0 28px #ff667f20}
.hero-price-number.flat{color:#e9f1fa}
.hero-change{font-size:clamp(18px,2.6vw,34px);font-weight:700;letter-spacing:-.6px;font-variant-numeric:tabular-nums;line-height:1.3;min-height:40px;white-space:nowrap}
.hero-change.up{color:#18df99}.hero-change.down{color:#ff667f}.hero-change.flat{color:#a7bed5}
.hero-meta{font-size:clamp(11px,1.4vw,19px);color:#a0bbd7;line-height:1.5}
.hero-meta.source{font-size:11px;margin-top:3px;color:#7fa2bb}
.hero-freshness{font-size:11px;color:#a2bbcf;margin:8px 0 0}
.hero-freshness.fresh{color:#78dbb1}.hero-freshness.stale{color:#f0a676}
.hero-clock-panel{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:4px 2px 2px;padding:22px 35px;background:linear-gradient(130deg,#352c28,#282423);border:2px solid #995b33;border-radius:13px}
.hero-clock-left{display:flex;align-items:center;gap:22px}
.hero-hourglass{width:46px;height:59px;color:#ff8a3c;flex:none}
.hero-clock-label{color:#b1c1d6;letter-spacing:2px;font-size:clamp(11px,1.35vw,17px);font-weight:800}
.hero-clock-number{font-variant-numeric:tabular-nums;font-size:clamp(40px,5.2vw,73px);line-height:1.09;font-weight:850;letter-spacing:1px;color:#ff923e;margin-top:3px}
.hero-clock-right{border-left:1px solid #625047;padding-left:30px;min-width:215px}
.hero-lock-time{color:#f5f3f1;font-size:clamp(22px,2.9vw,39px);font-weight:600;letter-spacing:-.4px;font-variant-numeric:tabular-nums;margin-top:10px}
.forecast .direction{font-size:clamp(66px,7.4vw,110px)}
.forecast .direction.waiting{font-size:clamp(48px,6.2vw,84px)}
.forecast .direction-caption{font-size:clamp(13px,1.45vw,19px)}
.forecast .confidence-row{margin-top:34px}
.forecast .note{font-size:clamp(12px,1.35vw,17px);max-width:950px}
.forecast .record-note{padding:20px 23px;margin-top:26px;border:1px solid #30495d;border-left:4px solid #7397b4;border-radius:10px;background:#112335}
.forecast .record-title{font-size:clamp(13px,1.6vw,20px)}
.forecast .record-details{font-size:clamp(12px,1.35vw,17px)}
@media(max-width:960px){.layout>.context{grid-template-columns:1fr}.hero-price-grid{padding:23px}.hero-price-half+.hero-price-half{margin-left:19px;padding-left:24px}.hero-price-number{font-size:clamp(31px,5.5vw,60px)}}
@media(max-width:700px){.forecast{padding:19px}.forecast>.lock-banner{padding:15px}.hero-price-grid{grid-template-columns:1fr;padding:20px;gap:22px}.hero-price-half+.hero-price-half{border-left:0;border-top:1px solid #345165;margin:0;padding:21px 0 0}.hero-price-number{font-size:clamp(37px,8vw,58px)}.hero-change{font-size:clamp(19px,4vw,27px)}.hero-clock-panel{padding:17px 19px;flex-wrap:wrap}.hero-hourglass{width:37px;height:47px}.hero-clock-right{min-width:0;flex:1}.hero-clock-number{font-size:43px}}
@media(max-width:410px){.hero-price-number{font-size:36px;letter-spacing:-1px}.hero-clock-right{border-left:0;border-top:1px solid #625047;padding:13px 0 0;width:100%;flex:auto}}
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
    anchors = (
        forecast_anchor, old_times, old_clock,
        "function renderLive(data){", "</style>",
        '<article class="card forecast">',
    )
    if any(page.count(anchor) != 1 for anchor in anchors):
        raise ValueError("main forecast markup has changed; refusing to misplace hero")
    page = page.replace("</style>", HERO_STYLE + "\n</style>", 1)
    page = page.replace(forecast_anchor, HERO_MARKUP + "\n" + forecast_anchor, 1)
    page = page.replace(old_times, "", 1)
    page = page.replace(old_clock, "function clock(){updateRoundPriceClock();}", 1)
    page = page.replace(
        "function renderLive(data){",
        HERO_SCRIPT + "\nfunction renderLive(data){\n renderRoundPriceHero(data);",
        1,
    )
    return page
