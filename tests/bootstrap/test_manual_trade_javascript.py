"""Regression: all Buy/Sell controls and balances depend on valid JavaScript.

A malformed AUTO status string previously prevented the entire manual panel
script from parsing, leaving Cash/Portfolio blank and buttons inert.
"""
import re
import shutil
import subprocess

from kalshi_edge.bootstrap.dashboard_ui import PAGE
from kalshi_edge.bootstrap.manual_trade_ui import SCRIPT, integrate_manual_trade


def _script_blocks(markup: str) -> list[str]:
    return re.findall(r"<script(?:\s[^>]*)?>([\s\S]*?)</script>", markup, re.I)


def test_manual_trade_script_is_valid_javascript():
    blocks = _script_blocks(SCRIPT)
    assert len(blocks) == 1
    script = blocks[0]

    # This exact line was accidentally changed to an unterminated string.
    assert '+(autoCurrent.message||"checking Kalshi"),' in script

    node = shutil.which("node")
    if node is None:
        # The exact regression assertion above still runs on Python-only machines.
        return
    result = subprocess.run(
        [node, "--check"], input=script, text=True, capture_output=True,
        timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_manual_controls_and_account_ids_still_present_in_rendered_card():
    page = integrate_manual_trade(PAGE)
    assert len(_script_blocks(page)) >= 2
    for identifier in (
        "manual-buy", "manual-sell", "manual-up", "manual-down", "manual-auto",
        "manual-submit", "manual-cash", "manual-portfolio", "manual-amount",
        "manual-auto-arm",
    ):
        assert f'id="{identifier}"' in page
    for action in ("refreshMarket();refreshAuto();", 'addEventListener("click",selectAuto)',
                   'addEventListener("click",armOrCancel)'):
        assert action in page
