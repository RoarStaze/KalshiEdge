"""Sell-all browser preview endpoint must never submit an order."""
from http.server import ThreadingHTTPServer
import threading

import httpx

from kalshi_edge.bootstrap import final_history


class Vault:
    def exists(self):
        return True


class Manual:
    def __init__(self):
        self.previews = 0
        self.orders = 0

    def preview_all(self, ticker):
        self.previews += 1
        return {"ticker":ticker,"action":"SELL","outcome":"UP",
                "units":"SHARES","count":"0.11","sell_all":True,
                "preview_id":"test-sell","amount_estimate":"0.0910"}

    def place(self, *_):
        self.orders += 1
        return {"status":"NO_FILL","order_id":"test","fill_count":"0.00"}


def test_sell_preview_is_full_position_read_only_and_takes_no_amount(tmp_path, monkeypatch):
    ticker="KXBTC15M-TEST"
    monkeypatch.setattr(final_history,"read_live_dashboard",
                        lambda _:{"market_ticker":ticker})
    manual=Manual()
    handler=final_history._make_handler(
        tmp_path,manual_trader=manual,credential_vault=Vault())
    server=ThreadingHTTPServer(("127.0.0.1",0),handler)
    runner=threading.Thread(target=server.serve_forever,daemon=True)
    runner.start()
    try:
        origin=f"http://127.0.0.1:{server.server_port}"
        with httpx.Client(timeout=4,trust_env=False) as client:
            resp=client.get(origin+"/api/manual-trade/sell-preview")
            assert resp.status_code==200
            assert resp.json()["sell_all"] is True
            assert resp.json()["count"]=="0.11"
            assert manual.orders==0
            invalid=client.get(origin+"/api/manual-trade/sell-preview?amount=5")
            assert invalid.status_code==409
            assert manual.previews==1
            assert manual.orders==0
    finally:
        server.shutdown()
        server.server_close()
        runner.join(timeout=3)
