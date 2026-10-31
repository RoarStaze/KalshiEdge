"""Three clean, routed presentations of the existing local KalshiEdge UI.

The current forecast/training/history render functions still expect their DOM
IDs. Keep inactive views mounted but hidden and stop unnecessary refresh loops.
No AutoTrader controls or credential inputs are injected into any route.
"""
from __future__ import annotations


STYLE = r"""/* KalshiEdge's routed, minimal three-page experience. */
:root{
 --bg:#080e13;--panel:#11191f;--panel2:#172128;--line:#273640;
 --text:#eff6f4;--muted:#98aab2;--mint:#23d9a7;--pink:#fb728c;
 --cyan:#74d2bb;--amber:#ebbe7d;--radius:20px;
 color-scheme:dark;
}
html{scroll-behavior:smooth}
body{background:radial-gradient(880px 460px at 22% -17%,#15332b77,transparent 70%),
 radial-gradient(800px 500px at 110% 10%,#10253666,transparent 65%),var(--bg);
 background-attachment:fixed}
.shell{max-width:1510px;margin:auto;padding:0 30px 64px}
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
@media (prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
"""

NAV = """<nav class="site-nav" aria-label="Primary navigation">
 <a class="site-nav-link" href="/trade">Trade</a>
 <a class="site-nav-link" href="/train">Train</a>
 <a class="site-nav-link" href="/history">History</a>
</nav>"""

PAGE_META = {
    "trade": ("TRADE / LIVE MARKETS", "Bitcoin, in real time.",
              "One live round forecast. One manual order panel. Every decision stays yours."),
    "train": ("TRAIN / ADAPTIVE LEARNING", "Built to keep learning.",
              "Review model readiness, learning cycles and controlled manual training."),
    "history": ("HISTORY / VERIFIED PERFORMANCE", "Every call. On the record.",
                "Explore saved final forecasts and settlement-verified performance."),
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
    page = page.replace('</style>', STYLE + '\n</style>', 1)
    page = page.replace('<body>', f'<body data-page="{section}">', 1)
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
    page = page.replace('<title>KalshiEdge — Live Forecast &amp; History</title>',
                        f'<title>KalshiEdge · {section.title()}</title>', 1)
    # Only one route is active. The links use real URLs, not in-page fake tabs.
    active = f'<a class="site-nav-link" href="/{section}">{section.title()}</a>'
    page = page.replace(active,
                        f'<a class="site-nav-link is-active" href="/{section}" aria-current="page">{section.title()}</a>', 1)
    # Background GETs for inactive views needlessly waste API budget.
    old_poll = ('refreshLive();refreshHistory();refreshTraining();\n'
                ' setInterval(refreshLive,750);setInterval(refreshHistory,5000);'
                'setInterval(refreshTraining,5000);setInterval(clock,250);')
    new_poll = ('const sitePage=document.body.dataset.page;\n'
                ' if(sitePage==="trade"){refreshLive();setInterval(refreshLive,1000);setInterval(clock,250);}\n'
                ' if(sitePage==="train"){refreshLive();refreshTraining();setInterval(refreshLive,5000);setInterval(refreshTraining,5000);}\n'
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
