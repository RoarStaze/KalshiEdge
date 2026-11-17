"""GoldPredict AI experimental pages.

A dedicated read-only gold experience deliberately does not import or render
the Bitcoin predictor, trading widgets, credential form or training JavaScript.
Gold forecasting/trading remain unavailable until independently verified data
and promotion gates are implemented.
"""
from __future__ import annotations

from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.site_pages import (
    STYLE, MARKET_SELECTOR_STYLE, render_market_selector,
)
from kalshi_edge.bootstrap.site_motion import MOTION_STYLE, MOTION_SCRIPT

GOLD_STYLE = r"""
body[data-market="gold"]{--gold:#f2ca80;--gold-muted:#a9a38f;--mint:#e4ba70}
body[data-market="gold"] .site-nav-link.is-active{
 background:rgba(125,88,32,.39);color:#ffe2a8;
 box-shadow:inset 0 0 0 1px rgba(224,168,77,.58)}
body[data-market="gold"] .site-nav{border-color:rgba(221,175,104,.38)!important}
body[data-market="gold"] .site-nav-link:hover{color:#fff1d0}
body[data-market="gold"] .header-right .chip{color:#ffdda3;
 border-color:rgba(237,190,107,.53);background:#302317}
.gp-shell{display:grid;gap:19px;margin:25px auto 0;max-width:1450px}
.gp-heading{display:flex;align-items:end;justify-content:space-between;gap:20px;flex-wrap:wrap}
.gp-kicker{font-size:10px;letter-spacing:2.1px;color:#e7be75;font-weight:850}
.gp-heading h1{font-size:clamp(27px,3.2vw,42px);margin:8px 0 5px;letter-spacing:-1.5px}
.gp-heading p{color:#adbbc1;margin:0;max-width:750px;font-size:13px;line-height:1.6}
.gp-badge{padding:9px 13px;border:1px solid rgba(237,186,102,.42);
 border-radius:99px;background:rgba(80,54,19,.4);color:#ffd998;
 font-size:10px;font-weight:850;letter-spacing:1.1px}
.gp-grid{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);gap:18px}
.gp-panel{--ke-rainbow-panel:linear-gradient(155deg,rgba(7,26,30,.94),rgba(4,13,20,.96));
 border:2px solid transparent!important;
 border-radius:20px;padding:24px;
 background:var(--ke-rainbow-panel) padding-box,
  conic-gradient(from var(--ke-rainbow-angle),
   #31ffcc 0deg,#2edbff 62deg,#8368ff 127deg,
   #ff3abc 192deg,#f6fc67 263deg,#39ffb0 322deg,#31ffcc 360deg)
 border-box!important;
 animation:ke-rainbow-cycle 6s linear infinite;
 box-shadow:0 20px 55px #0009}
.gp-panel h2{font-size:18px;margin:0 0 13px;letter-spacing:-.4px}
.gp-panel p{font-size:13px;line-height:1.65;color:#a7b9bc;margin:8px 0}
.gp-stat-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin:20px 0}
.gp-stat{background:rgba(5,22,27,.65);border:1px solid #42605b68;
 border-radius:13px;padding:17px}
.gp-stat .gp-stat-label{font-size:10px;color:#9fadae;letter-spacing:.8px}
.gp-stat strong{display:block;font-size:31px;margin-top:9px;color:#f2dab0}
.gp-info-rows{display:grid;gap:0;margin-top:14px}
.gp-info-row{display:flex;align-items:center;justify-content:space-between;
 gap:15px;border-bottom:1px solid #53615d35;padding:12px 0;font-size:12px}
.gp-info-row:last-child{border-bottom:0}
.gp-info-row span{color:#b0c2c3}
.gp-info-row strong{color:#ffdfa0;text-align:right;overflow-wrap:anywhere}
.gp-notice{padding:14px 16px;border-radius:12px;border:1px solid #bb874a73;
 background:rgba(89,58,25,.18);color:#f2d9ac;font-size:12px;line-height:1.6}
.gp-feature-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:16px}
.gp-feature{padding:15px;border-radius:13px;background:rgba(8,27,30,.82);
 border:1px solid #40585570;min-height:110px}
.gp-feature strong{font-size:13px;color:#f1e6d0}
.gp-feature p{font-size:11px;margin:7px 0 0}
.gp-step{padding:12px 0;border-bottom:1px solid #4c68533e;display:flex;gap:12px;align-items:start}
.gp-step:last-child{border-bottom:0}
.gp-step-num{display:grid;place-items:center;flex:none;width:26px;height:26px;
 border-radius:50%;background:#4e3b24;color:#ffe1a9;font-size:12px;font-weight:800}
.gp-step strong{display:block;color:#e4ece6;font-size:13px}
.gp-step p{margin:4px 0;font-size:11px}
.gp-footer{padding:16px 0;margin-top:10px;color:#8ea4a9;font-size:11px;border-top:1px solid #ffffff18}
.gp-panel a{color:#f6d18e}
.gp-empty{min-height:165px;display:grid;place-content:center;text-align:center;
 color:#8fa5aa;border:1px dashed #47615a;border-radius:14px;margin-top:14px;padding:20px}
.gp-empty strong{color:#fff0d1;font-size:16px}
.gp-empty p{margin:9px 0 0}
@media(max-width:920px){.gp-grid{grid-template-columns:1fr}.gp-panel{padding:20px}}
@media(max-width:700px){
 .gp-shell{margin-top:15px;gap:14px}.gp-feature-grid{grid-template-columns:1fr}
 .gp-heading h1{font-size:28px}.gp-panel{padding:17px}
}
@media(prefers-reduced-motion:reduce){.gp-panel{animation:none}}
"""

GOLD_NAV = (
    ("trade", "Forecast", "/gold"),
    ("train", "Train", "/gold/train"),
    ("history", "History", "/gold/history"),
    ("data", "Data", "/gold/data"),
)

GOLD_SECTIONS = {
    "trade": ("Experimental gold forecast",
              "The forecast engine will remain inactive until gold-specific data and settlement checks pass."),
    "train": ("Gold model laboratory",
              "A proposed hybrid research stack, not a trained or deployed financial model."),
    "history": ("Gold forecast history",
                "Only gold predictions and gold settlements will appear here after validation."),
    "data": ("Gold data readiness",
             "Inspect the prerequisites for point-in-time, reproducible KXGOLD15M research."),
}


def _readiness_rows() -> str:
    entries = (
        ("Exact contract and timezone interpretation", "UNVERIFIED"),
        ("Official eight-minute checkpoint timing", "UNVERIFIED"),
        ("Pyth settlement-oracle observations", "UNVERIFIED"),
        ("Pyth OHLC history origin and candle alignment", "UNVERIFIED"),
        ("Point-in-time feature availability", "UNVERIFIED"),
        ("Chronological independent lockbox", "NOT READY"),
        ("Out-of-sample probability calibration", "NOT READY"),
    )
    return "".join(
        f'<div class="gp-info-row"><span>{label}</span><strong>{status}</strong></div>'
        for label, status in entries
    )


def _forecast() -> str:
    return """<div class="gp-grid">
<article class="gp-panel">
 <div class="gp-kicker">GOLD / 15-MINUTE CONTRACT</div>
 <h2>GoldPredict AI · Forecast</h2>
 <div class="gp-notice">No active gold forecast. This experimental model cannot currently predict, train, or place orders.</div>
 <div class="gp-stat-grid">
  <div class="gp-stat"><div class="gp-stat-label">PROBABILITY · UP</div><strong>—</strong></div>
  <div class="gp-stat"><div class="gp-stat-label">PROBABILITY · DOWN</div><strong>—</strong></div>
 </div>
 <div class="gp-info-rows">
  <div class="gp-info-row"><span>Market series</span><strong>KXGOLD15M</strong></div>
  <div class="gp-info-row"><span>Settlement reference</span><strong>Metal.Index.1OZGOLD/USD</strong></div>
  <div class="gp-info-row"><span>Research stage</span><strong>DATA VALIDATION</strong></div>
  <div class="gp-info-row"><span>Trading and automated orders</span><strong>DISABLED</strong></div>
 </div>
</article>
<article class="gp-panel">
 <h2>Prediction intelligence</h2>
 <p>The planned system combines independent gold market forecasts using a trainable, calibrated fusion layer. No unvalidated numerical signal is presented as a prediction.</p>
 <div class="gp-feature-grid">
  <div class="gp-feature"><strong>XGBoost</strong><p>Price dynamics, liquidity and engineered microstructure features.</p></div>
  <div class="gp-feature"><strong>Time-series model</strong><p>Gold price trajectories, volatility and temporal relationships.</p></div>
  <div class="gp-feature"><strong>Financial language model</strong><p>Timestamped macroeconomic context and economic releases.</p></div>
  <div class="gp-feature"><strong>Regime detection</strong><p>Conditions affecting volatility and model reliability.</p></div>
 </div>
</article></div>"""


def _training() -> str:
    return """<div class="gp-grid">
<article class="gp-panel">
 <div class="gp-kicker">RESEARCH / MODEL STACK</div><h2>GoldPredict AI — Experimental V1</h2>
 <div class="gp-notice">Training is disabled: a verified point-in-time gold dataset and independently checked contract labels are not yet available.</div>
 <div class="gp-step"><span class="gp-step-num">1</span><div><strong>Gold data collection and audits</strong><p>Reuse existing KXGOLD15M inventory, rule inspection and candle diagnostics.</p></div></div>
 <div class="gp-step"><span class="gp-step-num">2</span><div><strong>Independent forecasting baselines</strong><p>XGBoost, price-only and time-series model candidates, trained strictly on past data.</p></div></div>
 <div class="gp-step"><span class="gp-step-num">3</span><div><strong>Language and regime features</strong><p>Only event inputs demonstrably available at the prediction timestamp.</p></div></div>
 <div class="gp-step"><span class="gp-step-num">4</span><div><strong>Out-of-fold fusion and calibration</strong><p>Train on out-of-sample model predictions, then evaluate on a frozen chronological lockbox.</p></div></div>
 <div class="gp-step"><span class="gp-step-num">5</span><div><strong>Prospective paper evaluation</strong><p>Test predicted probabilities, coverage, fees, liquidity and hypothetical executable edge.</p></div></div>
</article>
<article class="gp-panel">
 <h2>Training readiness</h2>
 <div class="gp-info-rows">""" + _readiness_rows() + """</div>
 <p>No gold model weights, training jobs, or promotion controls have been activated.</p>
</article></div>"""


def _history() -> str:
    return """<div class="gp-grid">
<article class="gp-panel">
 <h2>Verified gold forecasts</h2>
 <div class="gp-empty"><strong>No verified gold forecasts yet</strong>
 <p>History will be gold-only. Bitcoin predictions and Bitcoin performance are never inserted here.</p></div>
</article>
<article class="gp-panel">
 <h2>Evaluation standards</h2>
 <div class="gp-info-rows">
  <div class="gp-info-row"><span>Markets</span><strong>KXGOLD15M ONLY</strong></div>
  <div class="gp-info-row"><span>Chronological holdout</span><strong>NOT READY</strong></div>
  <div class="gp-info-row"><span>Out-of-sample accuracy</span><strong>—</strong></div>
  <div class="gp-info-row"><span>Calibrated Brier score</span><strong>—</strong></div>
  <div class="gp-info-row"><span>Net executable P&amp;L</span><strong>—</strong></div>
 </div><p>Blank metrics are intentionally not simulated or derived from Bitcoin.</p>
</article></div>"""


def _data() -> str:
    return """<div class="gp-grid">
<article class="gp-panel">
 <div class="gp-kicker">DATA INTEGRITY / GOLD</div>
 <h2>Verification gates</h2>
 <div class="gp-notice">A historical inventory is not by itself an eligible training dataset. Contract-time interpretation, oracle labels, and as-of input availability still need independent verification.</div>
 <div class="gp-info-rows">""" + _readiness_rows() + """</div>
</article>
<article class="gp-panel">
 <h2>Existing gold research</h2>
 <p>The current repository contains contract-rule inspection, public market inventory, reported outcome audits and pre-checkpoint candle diagnostics.</p>
 <div class="gp-info-rows">
  <div class="gp-info-row"><span>Contract series</span><strong>KXGOLD15M</strong></div>
  <div class="gp-info-row"><span>Target index</span><strong>Metal.Index.1OZGOLD/USD</strong></div>
  <div class="gp-info-row"><span>Proposed checkpoint baseline</span><strong>480 seconds</strong></div>
 </div>
 <p>V1.2 settlement verification remains BLOCKED. Pyth's one-minute OHLC history requires authenticated access; any offline imported file is marked UNATTESTED and cannot create verified Kalshi labels. Conflicting EST/EDT contract text and candle-label alignment must be independently resolved.</p>
 <p>Original inspections remain available in the gold audit documentation. Neither public market candles nor an API result alone establish oracle-verified, point-in-time ground truth.</p>
 <p><a href="https://github.com/RoarStaze/KalshiEdge/blob/main/docs/GOLD_CANDLE_CHECKPOINT_PROBE_2026-11-17.md">Review gold candle diagnostic</a></p>
 <p><a href="https://github.com/RoarStaze/KalshiEdge/blob/main/docs/GOLD_SETTLEMENT_ORACLE_V12_2026-11-17.md">Review V1.2 oracle verification protocol</a></p>
</article></div>"""


GOLD_CONTENT = {"trade": _forecast, "train": _training, "history": _history, "data": _data}


def render_gold_page(section: str) -> str:
    """Return an isolated read-only gold page with zero Bitcoin data requests."""
    if section not in GOLD_SECTIONS:
        raise ValueError("Unknown GoldPredict section")
    # Reuse the actual dashboard's base styling, routed styling and cinematic
    # background without importing any of its JavaScript or trading controls.
    base_css = PAGE.split("<style>", 1)[1].split("</style>", 1)[0]
    css = "\n".join((base_css, STYLE, MOTION_STYLE, MARKET_SELECTOR_STYLE, GOLD_STYLE))
    link_parts = []
    for key, label, path in GOLD_NAV:
        active_class = " is-active" if key == section else ""
        current = ' aria-current="page"' if key == section else ""
        link_parts.append(
            f'<a class="site-nav-link{active_class}" href="{path}"{current}>{label}</a>'
        )
    links = "".join(link_parts)
    title, description = GOLD_SECTIONS[section]
    selector = render_market_selector("gold")
    content = GOLD_CONTENT[section]()
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#08161b">
<title>KalshiEdge · Gold · {title}</title>
<style>{css}</style></head>
<body data-page="gold" data-market="gold">
<div class="shell"><header class="site-header">
{selector}
<nav class="site-nav" aria-label="Primary navigation">{links}</nav>
<div class="header-right"><span class="chip">RESEARCH ONLY</span></div>
</header>
<main class="gp-shell" data-gold-section="{section}">
<section class="gp-heading" aria-label="GoldPredict AI research">
<div><div class="gp-kicker">KALSHIEDGE / GOLD · EXPERIMENTAL V1</div>
<h1>{title}</h1><p>{description}</p><p class="gp-kicker">SOURCE · KXGOLD15M · Metal.Index.1OZGOLD/USD</p></div>
<span class="gp-badge">GOLDPREDICT AI · EXPERIMENTAL V1</span>
</section>
{content}
</main>
<footer class="gp-footer">GoldPredict AI — Experimental V1 · Gold research only.
No gold forecasts, model fitting, manual orders or automated orders are enabled.</footer>
</div>{MOTION_SCRIPT}</body></html>"""
