"""Standalone, synthetic KalshiEdge website preview -- never touches live state.

This handler intentionally does NOT reuse final_history._make_handler: that
constructor initializes training, credential and trading objects. Preview owns
only its loopback HTTP listener and reads (optionally) one allowlisted WebP.
"""
from __future__ import annotations

import json
import sys
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .dashboard_ui import PAGE
from .manual_trade_ui import integrate_manual_trade
from .round_price_ui import integrate_round_price_hero
from .api_credentials_ui import integrate_api_credentials
from .site_pages import render_site_page

PREVIEW_PORT = 8900
_RESERVED_PORTS = frozenset((8898, 8899))
_ASSET_ROUTE = "/assets/kalshiedge-neon-city.webp"
_PREVIEW_STYLE = """
<style>
body[data-preview="true"] .preview-ribbon{
 position:fixed;bottom:12px;right:12px;z-index:1000;
 padding:9px 13px;border:1px solid #f5cb6e;border-radius:10px;
 color:#ffeab3;background:rgba(37,24,11,.96);
 font:700 11px/1.3 system-ui,sans-serif;letter-spacing:.3px;
 box-shadow:0 6px 25px #0008;pointer-events:none}
body[data-preview="true"] :is(#train-now,#train-pause,#train-resume,
 #manual-submit,#api-save,#api-delete,#api-key-id,#api-private-key,
 .mt-action,.mt-auto-arm){pointer-events:none!important;opacity:.48!important}
</style>
"""
_PREVIEW_SCRIPT = """<script>
(function(){
 "use strict";
 // Capture phase also blocks keyboard-triggered clicks and form submissions.
 // The separate preview HTTP server independently rejects ALL writes.
 document.addEventListener("click",function(e){
  if(e.target.closest("button")){
   e.preventDefault();e.stopImmediatePropagation();
  }
 },true);
 document.addEventListener("submit",function(e){
  e.preventDefault();e.stopImmediatePropagation();
 },true);
 for(const id of ["api-key-id","api-private-key"]){
  const input=document.getElementById(id);
  if(input){input.disabled=true;input.readOnly=true;}
 }
})();
</script>"""


def _pages() -> dict[str, bytes]:
    template = integrate_api_credentials(
        integrate_manual_trade(integrate_round_price_hero(PAGE))
    )
    result: dict[str, bytes] = {}
    for name in ("trade", "train", "history", "api"):
        page = render_site_page(template, name)
        tag = f'<body data-page="{name}">'
        if page.count(tag) != 1:
            raise ValueError("Unexpected preview layout; refusing unsafe injection")
        page = page.replace(tag,
                            f'<body data-preview="true" data-page="{name}">'
                            '<div class="preview-ribbon" role="status">'
                            'TEST PREVIEW · simulated data · no trading or training'
                            '</div>', 1)
        page = page.replace("</head>", _PREVIEW_STYLE + "\n</head>", 1)
        page = page.replace("</body>", _PREVIEW_SCRIPT + "\n</body>", 1)
        result[name] = page.encode("utf-8")
    return result


def _training() -> dict:
    stamp = datetime.now(timezone.utc).isoformat()
    run = {
        "run_id": "PREVIEW-DEMO-NOT-A-REAL-RUN",
        "stage": "FITTING",
        "mode": "baseline",
        "started_utc": "2026-11-11T00:00:00+00:00",
        "updated_utc": stamp,
        "progress_percent": 42,
        "fit_steps_completed": 42,
        "fit_steps_total": 100,
        "fit_step_detail": "SIMULATED: illustrating stage 4 fitting progress",
        "events": [
            {"stage":"BUILDING_DATASET"},
            {"stage":"VERIFYING_DATASET"},
            {"stage":"SPLITTING"},
        ],
        "results": {
            "candidate_69_gate": {"qualified_accuracy":.86,"correct":43,"qualified":50,"coverage":.72},
            "incumbent_69_gate": {"qualified_accuracy":.82,"correct":41,"qualified":50,"coverage":.70},
        },
    }
    return {
        "preview": True, "running": True, "paused": False,
        "pause_requested": False, "status": "PREVIEW",
        "ready": False, "train_available": False, "selected_mode": "baseline",
        "mode": "baseline", "run": run,
        "current_model": {"model_number": 5, "promoted_hash": "a"*64},
        "forward_480": {
            "ready": False, "eligible_480_markets": 80,
            "required_markets": 120, "progress_percent": 66.7,
            "verified_outcome_classes": 2,
            "reason": "DEMO DATA: independent forward evidence would appear here.",
        },
        "baseline_retrain": {
            "ready": False,
            "reason": "SIMULATED preview: no real dataset or model is accessed.",
        },
    }


def _live() -> dict:
    now = time.time_ns()
    return {
        "preview": True, "status": "LIVE",
        "market_ticker": "KXBTC15M-DEMO-PREVIEW-00",
        "market_close_ts_ns": now + 10 * 60 * 1_000_000_000,
        "checkpoint_ts_ns": now + 2 * 60 * 1_000_000_000,
        "strike": 100000.0,
        "final_forecast": None,
        "latest_prediction": {
            "status": "OK", "predicted_side": "ABOVE", "prediction_ts_ns": now,
            "p_yes": .87, "p_no": .13, "confidence": .74,
            "model_version": "DEMO MODEL (not live)",
            "model_hash": "a"*64,
            "btc_reference": 100240.0, "kalshi_prior": .83,
            "volatility_expected_move_pct": .17, "volatility_weight": .55,
        },
    }


def _history() -> dict:
    return {
        "preview": True,
        "summary": {
            "total_final_forecasts": 3, "correct": 2, "incorrect": 1,
            "settled_directional_forecasts": 3, "accuracy": 2/3,
            "skipped": 0, "settled_skips": 0, "pending_settlement": 0,
        },
        "forecasts": [
            {
                "market_ticker": f"KXBTC15M-DEMO-{index:02d}",
                "market_close": "2026-11-11T02:00:00+00:00",
                "forecast": prediction, "verdict": verdict,
                "actual_side": outcome, "directional_probability": .79,
                "confidence": .76,
            }
            for index, prediction, verdict, outcome in (
                (1,"ABOVE","CORRECT","ABOVE"),
                (2,"BELOW","CORRECT","BELOW"),
                (3,"ABOVE","INCORRECT","BELOW"),
            )
        ],
    }


def make_preview_handler(*, asset_path: Path | None = None) -> type[BaseHTTPRequestHandler]:
    """Return a readonly router with no access to live services or credentials."""
    pages = _pages()
    background = Path(asset_path) if asset_path is not None else None

    class PreviewHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            # No request body/credentials are ever logged.
            return

        def _reply(self, code: int, content: bytes, content_type: str = "application/json; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; style-src 'unsafe-inline'; "
                             "script-src 'unsafe-inline'; connect-src 'self'; img-src 'self'")
            self.end_headers()
            self.wfile.write(content)

        def _json(self, code: int, payload: dict) -> None:
            self._reply(code, json.dumps(payload, allow_nan=False, separators=(",", ":")).encode("utf-8"))

        def _local_host(self) -> bool:
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self) -> None:
            if not self._local_host():
                self._json(403, {"error": "Preview accepts only localhost requests"})
                return
            path = urlsplit(self.path).path
            if path == "/":
                self.send_response(302)
                self.send_header("Location", "/train")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path in ("/trade", "/train", "/history", "/api"):
                self._reply(200, pages[path[1:]], "text/html; charset=utf-8")
                return
            if path == _ASSET_ROUTE:
                if background is None:
                    self._json(404, {"error": "Preview background image unavailable"})
                    return
                try:
                    data = background.read_bytes()
                except OSError:
                    self._json(404, {"error": "Preview background image unavailable"})
                    return
                if not 12 <= len(data) <= 3_000_000 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
                    self._json(415, {"error": "Invalid preview background image"})
                    return
                self._reply(200, data, "image/webp")
                return
            if path == "/api/health":
                self._json(200, {"app": "kalshiedge", "service": "synthetic-preview", "preview": True})
                return
            if path in ("/api/training", "/api/training/progress"):
                data = _training()
                self._json(200, data if path == "/api/training" else {
                    "preview":True,"run":data["run"],"running":data["running"],
                    "paused":data["paused"],"pause_requested":data["pause_requested"],
                })
                return
            if path == "/api/live":
                self._json(200, _live())
                return
            if path == "/api/history":
                self._json(200, _history())
                return
            if path == "/api/autotrader/status":
                self._json(200, {"preview": True,"credentials_saved": False,
                                 "credentials_supported": False,"enabled": False})
                return
            if path.startswith(("/api/manual-trade/", "/api/armed-trade/")):
                self._json(403, {"error": "Preview cannot access real trading or account data"})
                return
            self._json(404, {"error": "Preview endpoint not found"})

        def do_POST(self) -> None:
            # Never parse a request body or invoke any live controller.
            self._json(403, {"error": "Test preview is read-only; training, trading and credentials are disabled"})

        def do_PUT(self) -> None:
            self.do_POST()

        def do_PATCH(self) -> None:
            self.do_POST()

        def do_DELETE(self) -> None:
            self.do_POST()

    return PreviewHandler


def run_test_preview(*, port: int = PREVIEW_PORT, open_browser: bool = True,
                     asset_path: Path | None = None) -> int:
    """Run *only* an independent preview listener; never share the live port."""
    if not 1 <= port <= 65535 or port in _RESERVED_PORTS:
        print(f"Preview port {port} is invalid or reserved for the live application.", file=sys.stderr)
        return 2
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), make_preview_handler(asset_path=asset_path))
    except OSError as exc:
        print(f"Cannot bind preview port {port}; another program may be using it: {exc}", file=sys.stderr)
        return 2
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_port}/train"
    print("KALSHIEDGE TEST PREVIEW — SIMULATED DATA ONLY", flush=True)
    print("No trainer, predictor, orders, credentials, or live state is used.", flush=True)
    print("Preview website:", url, flush=True)
    print("Ctrl+C stops only this preview.", flush=True)
    if open_browser:
        try:
            webbrowser.open_new_tab(url)
        except (OSError, webbrowser.Error) as exc:
            print(f"Browser could not open: {exc}; open {url} manually.", file=sys.stderr)
    try:
        server.serve_forever(poll_interval=.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
