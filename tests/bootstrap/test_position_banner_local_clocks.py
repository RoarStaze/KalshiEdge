"""Trade header must show actual holding and estimated P/L, not forecast archive status.

All user-facing market timestamps must use the browser's local timezone.
"""
from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.manual_trade_ui import SCRIPT as MANUAL_SCRIPT
from kalshi_edge.bootstrap.round_price_ui import HERO_SCRIPT, integrate_round_price_hero
from kalshi_edge.bootstrap.site_pages import render_site_page


def test_top_forecast_banner_is_position_only_and_reuses_live_market_snapshot():
    # The full active trade template includes manual market account reads.
    from kalshi_edge.bootstrap.manual_trade_ui import integrate_manual_trade
    page = render_site_page(integrate_manual_trade(integrate_round_price_hero(PAGE)), "trade")
    assert 'id="lock-banner"' in page
    assert 'id="lock-heading"' in page and 'id="lock-description"' in page
    assert "8-MIN FINAL FORECAST ARCHIVED · LIVE MONITOR ON" not in page
    assert "LIVE THROUGH MARKET CLOSE" not in page
    assert "8-MIN RECORD PENDING · LIVE MONITOR ON" not in page
    assert "BOUGHT UP" in page
    assert "BOUGHT DOWN" in page
    assert "UNREALIZED P/L" in page
    assert "kalshiedge:position-snapshot" in page
    assert "position_unrealized_pnl_dollars" in page
    assert 'id="scalp-reversal-risk"' in page
    assert 'id="scalp-flip-alert"' in page
    assert 'id="confidence"' in page


def test_position_banner_receives_market_side_and_pnl_from_existing_account_poll():
    assert 'CustomEvent("kalshiedge:position-snapshot"' in MANUAL_SCRIPT
    assert 'detail:{' in MANUAL_SCRIPT
    assert "position_pnl_status" in MANUAL_SCRIPT
    assert "position_unrealized_pnl_dollars" in MANUAL_SCRIPT
    assert "observed_at_ms:Date.now()" in MANUAL_SCRIPT


def test_clock_uses_computers_local_timezone_without_pinned_eastern_time():
    page = integrate_round_price_hero(PAGE)
    assert "America/New_York" not in page
    assert '" ET · market closes"' not in page
    assert '" ET"' not in HERO_SCRIPT
    assert "timeZoneName:\"short\"" in HERO_SCRIPT
    assert "toLocaleTimeString(undefined" in HERO_SCRIPT


def test_position_banner_renders_verified_up_down_and_stale_states_in_node():
    """Behavioral DOM test, including signs and stale-data reset."""
    import shutil
    import subprocess
    import pytest

    if shutil.which("node") is None:
        pytest.skip("node is optional")
    start = PAGE.index("function renderPositionBanner(data){")
    end = PAGE.index("function renderScalpMonitor(data){", start)
    js = PAGE[start:end]
    harness = r'''
const nodes={
  "lock-banner":{className:""},
  "lock-heading":{textContent:""},
  "lock-description":{className:"",textContent:""}
};
const el=id=>nodes[id];
const text=(id,value)=>{nodes[id].textContent=String(value)};
let positionLive=null;
''' + js + r'''
function check(ok,label){if(!ok)throw new Error(label)}
const t=Date.now();
positionLive={
 ticker:"KXBTC15M-TEST",position_yes:"2.0000",
 position_pnl_status:"ESTIMATED",position_unrealized_pnl_dollars:"0.1600",
 observed_at_ms:t
};
renderPositionBanner({market_ticker:"KXBTC15M-TEST"});
check(nodes["lock-heading"].textContent==="BOUGHT UP","UP holding");
check(nodes["lock-description"].textContent.includes("+$0.16"),"positive P/L");
positionLive.position_yes="-2.0000";
positionLive.position_unrealized_pnl_dollars="-0.4300";
renderPositionBanner({market_ticker:"KXBTC15M-TEST"});
check(nodes["lock-heading"].textContent==="BOUGHT DOWN","DOWN holding");
check(nodes["lock-description"].textContent.includes("−$0.43"),"negative P/L");
positionLive.position_pnl_status="INSUFFICIENT_LIQUIDITY";
renderPositionBanner({market_ticker:"KXBTC15M-TEST"});
check(nodes["lock-description"].textContent==="UNREALIZED P/L —","no invented profit");
positionLive.position_pnl_status="ESTIMATED";
positionLive.observed_at_ms=t-20000;
renderPositionBanner({market_ticker:"KXBTC15M-TEST"});
check(nodes["lock-heading"].textContent==="POSITION UPDATING","stale holdings");
'''
    result = subprocess.run(
        ["node", "-"], input=harness, text=True, capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_position_banner_shows_verified_sold_and_signed_realized_pnl():
    import shutil
    import subprocess
    import pytest

    if shutil.which("node") is None:
        pytest.skip("node is optional")
    start = PAGE.index("function renderPositionBanner(data){")
    end = PAGE.index("function renderScalpMonitor(data){", start)
    js = PAGE[start:end]
    harness = r'''
const nodes={"lock-banner":{className:""},"lock-heading":{textContent:""},
             "lock-description":{className:"",textContent:""}};
const el=id=>nodes[id];
const text=(id,value)=>{nodes[id].textContent=String(value)};
let positionLive=null;
''' + js + r'''
const t=Date.now();
positionLive={
 ticker:"KXBTC15M-TEST", position_yes:"0.0000", observed_at_ms:t,
 last_sell:{ticker:"KXBTC15M-TEST",status:"SOLD",
           realized_pnl_dollars:"-0.0074"}
};
renderPositionBanner({market_ticker:"KXBTC15M-TEST"});
if(nodes["lock-heading"].textContent!=="SOLD")throw Error("SOLD state missing");
if(nodes["lock-description"].textContent!=="REALIZED P/L −$0.0074")
 throw Error("signed sale loss missing");
positionLive.last_sell.realized_pnl_dollars="0.2350";
renderPositionBanner({market_ticker:"KXBTC15M-TEST"});
if(nodes["lock-description"].textContent!=="REALIZED P/L +$0.23")
 throw Error("positive sale profit missing");
positionLive.last_sell.ticker="KXBTC15M-OTHER";
renderPositionBanner({market_ticker:"KXBTC15M-TEST"});
if(nodes["lock-heading"].textContent!=="NO POSITION")
 throw Error("cross-ticker sale leaked");
'''
    result = subprocess.run(["node", "-"], input=harness, text=True,
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
