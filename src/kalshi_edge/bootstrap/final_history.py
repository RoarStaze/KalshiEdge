from __future__ import annotations

"""Official Kalshi settlement tracking and local read-only final forecast history.

Final forecasts are immutable. Separately persisted Kalshi results are used
solely for post-settlement scoring; they are never used by live inference.
"""

import json
import logging
import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .final_forecast import FINAL_CHECKPOINT_SECONDS, FinalForecastRecord

LOG = logging.getLogger(__name__)
NS = 1_000_000_000
SAFE_TICKER = re.compile(r"^KXBTC15M-[A-Z0-9-]+$")
SETTLEMENT_WAIT_NS = 60 * NS
POLL_SECONDS = 30


class SettlementIntegrityError(ValueError):
    """A purported official settlement does not match the forecasted market."""


class OfficialSettlement(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_ticker: str
    market_close_ts_ns: int = Field(gt=0)
    result: str
    actual_side: str
    source: str = "kalshi-public-market-api"
    fetched_ts_ns: int = Field(gt=0)
    market_status: str


def _close_ns(forecast: FinalForecastRecord) -> int:
    return forecast.scheduled_ts_ns + FINAL_CHECKPOINT_SECONDS * NS


def _close_iso(forecast: FinalForecastRecord) -> str:
    return datetime.fromtimestamp(_close_ns(forecast) / NS, tz=timezone.utc).isoformat()


def _settlement_path(root: Path, forecast: FinalForecastRecord) -> Path:
    if not SAFE_TICKER.fullmatch(forecast.market_ticker):
        raise SettlementIntegrityError("unsafe KXBTC15M market ticker")
    day = datetime.fromtimestamp(_close_ns(forecast) / NS, tz=timezone.utc).date().isoformat()
    return root / "final_outcomes" / day / f"{forecast.market_ticker}.json"


def _forecasts(root: Path) -> list[FinalForecastRecord]:
    path = root / "final_forecasts"
    if not path.is_dir():
        return []
    records: dict[str, FinalForecastRecord] = {}
    for item in sorted(path.glob("*/*.json")):
        try:
            record = FinalForecastRecord.model_validate_json(item.read_text(encoding="utf-8"))
            if not SAFE_TICKER.fullmatch(record.market_ticker) or item.name != f"{record.market_ticker}.json":
                raise SettlementIntegrityError("filename/ticker mismatch")
            if record.market_ticker in records:
                raise SettlementIntegrityError("duplicate final forecast for ticker")
            records[record.market_ticker] = record
        except (OSError, ValueError) as exc:
            LOG.warning("Ignoring invalid final forecast %s: %s", item, exc)
    return sorted(records.values(), key=lambda forecast: (_close_ns(forecast), forecast.market_ticker), reverse=True)


def _settlement_from_payload(payload: dict[str, Any], forecast: FinalForecastRecord, *, now_ns: int) -> OfficialSettlement | None:
    raw = payload.get("market", payload)
    if not isinstance(raw, dict):
        raise SettlementIntegrityError("Kalshi response did not contain a market object")
    if raw.get("ticker") != forecast.market_ticker:
        raise SettlementIntegrityError("Kalshi result ticker does not match final forecast")
    if raw.get("is_provisional") is True:
        return None

    status = str(raw.get("status") or "").lower()
    result = str(raw.get("result") or "").lower()
    if status not in {"finalized", "settled"} or result not in {"yes", "no"}:
        return None

    close_text = raw.get("close_time")
    if not isinstance(close_text, str):
        raise SettlementIntegrityError("settled market lacks a close timestamp")
    try:
        dt = datetime.fromisoformat(close_text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("missing timezone")
        close = int(dt.timestamp() * NS)
    except (ValueError, OverflowError) as exc:
        raise SettlementIntegrityError("settled market close timestamp is invalid") from exc
    if abs(close - _close_ns(forecast)) > NS:
        raise SettlementIntegrityError("official close timestamp does not match forecast")

    strike_type = str(raw.get("strike_type") or "").lower()
    if strike_type not in {"greater", "greater_or_equal"}:
        raise SettlementIntegrityError("market strike direction is not a supported ABOVE contract")
    strike_raw = raw.get("floor_strike", raw.get("floor_strike_dollars"))
    try:
        strike = float(strike_raw)
        if not math.isfinite(strike) or abs(strike - forecast.strike) > 0.011:
            raise ValueError("strike mismatch")
    except (ValueError, TypeError) as exc:
        raise SettlementIntegrityError("official strike does not match final forecast") from exc

    return OfficialSettlement(
        market_ticker=forecast.market_ticker,
        market_close_ts_ns=_close_ns(forecast),
        result=result,
        actual_side="ABOVE" if result == "yes" else "BELOW",
        fetched_ts_ns=now_ns,
        market_status=status,
    )


def _load_saved_settlement(root: Path, forecast: FinalForecastRecord) -> OfficialSettlement | None:
    path = _settlement_path(root, forecast)
    if not path.exists():
        return None
    try:
        record = OfficialSettlement.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SettlementIntegrityError(f"recorded settlement is damaged: {path}") from exc
    if (
        record.market_ticker != forecast.market_ticker
        or record.market_close_ts_ns != _close_ns(forecast)
        or record.result not in {"yes", "no"}
        or record.actual_side != ("ABOVE" if record.result == "yes" else "BELOW")
        or record.source != "kalshi-public-market-api"
        or record.market_status not in {"settled", "finalized"}
    ):
        raise SettlementIntegrityError("recorded settlement does not pass integrity checks")
    return record


def _save_settlement(root: Path, forecast: FinalForecastRecord, settlement: OfficialSettlement) -> Path | None:
    path = _settlement_path(root, forecast)
    if settlement.market_ticker != forecast.market_ticker or settlement.market_close_ts_ns != _close_ns(forecast):
        raise SettlementIntegrityError("settlement cannot be saved for a different forecast")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(settlement.model_dump(mode="json"), handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        _load_saved_settlement(root, forecast)
        return None
    return path


class PublicKalshiMarketClient:
    """Read public market data only, without a private key or Kalshi API auth."""

    def __init__(self, base_url: str, *, http: httpx.Client) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = http

    def fetch(self, ticker: str) -> dict[str, Any]:
        if not SAFE_TICKER.fullmatch(ticker):
            raise SettlementIntegrityError("invalid market ticker")
        paths = (f"/markets/{ticker}", f"/historical/markets/{ticker}")
        for index, path in enumerate(paths):
            response = self.http.get(f"{self.base_url}{path}")
            if response.status_code == 404 and index == 0:
                continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise SettlementIntegrityError("Kalshi market response is not an object")
            return payload
        raise SettlementIntegrityError("market lookup did not return a response")


def sync_final_settlements(
    root: Path,
    base_url: str,
    *,
    now_ns: int | None = None,
    max_fetches: int = 20,
    http: httpx.Client | None = None,
) -> dict[str, int]:
    """Resolve completed forecasts, leaving unresolved/error outcomes pending."""

    root = root.resolve()
    now_ns = time.time_ns() if now_ns is None else now_ns
    stats = {"forecast_count": 0, "pending": 0, "already_resolved": 0, "newly_resolved": 0, "errors": 0, "requests": 0}
    # Prioritize the newest completed market so stale archived requests cannot
    # starve current forecasts under the per-poll request budget.
    forecasts = _forecasts(root)
    stats["forecast_count"] = len(forecasts)

    owns_http = http is None
    client = http or httpx.Client(timeout=8.0)
    try:
        api = PublicKalshiMarketClient(base_url, http=client)
        for forecast in forecasts:
            try:
                if _load_saved_settlement(root, forecast) is not None:
                    stats["already_resolved"] += 1
                    continue
            except SettlementIntegrityError as exc:
                stats["errors"] += 1
                LOG.error("Saved settlement integrity failure for %s: %s", forecast.market_ticker, exc)
                continue

            if now_ns < _close_ns(forecast) + SETTLEMENT_WAIT_NS:
                stats["pending"] += 1
                continue
            if stats["requests"] >= max_fetches:
                stats["pending"] += 1
                continue

            stats["requests"] += 1
            try:
                response = api.fetch(forecast.market_ticker)
                settlement = _settlement_from_payload(response, forecast, now_ns=now_ns)
                if settlement is None:
                    stats["pending"] += 1
                elif _save_settlement(root, forecast, settlement) is not None:
                    stats["newly_resolved"] += 1
                else:
                    stats["already_resolved"] += 1
            except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
                stats["errors"] += 1
                stats["pending"] += 1
                LOG.warning("Cannot resolve market %s yet: %s", forecast.market_ticker, exc)
    finally:
        if owns_http:
            client.close()
    return stats


def read_final_history(root: Path) -> dict[str, Any]:
    """Only settled, non-SKIP final decisions enter directional accuracy."""

    root = root.resolve()
    rows: list[dict[str, Any]] = []
    correct = incorrect = skipped = settled_skips = pending = 0

    for forecast in _forecasts(root):
        try:
            outcome = _load_saved_settlement(root, forecast)
        except SettlementIntegrityError as exc:
            LOG.error("Outcome integrity error for %s: %s", forecast.market_ticker, exc)
            outcome = None

        is_skip = forecast.forecast == "SKIP"
        if is_skip:
            skipped += 1
            if outcome is not None:
                settled_skips += 1

        if outcome is None:
            pending += 1
            verdict = "SKIP" if is_skip else "PENDING"
        elif is_skip:
            verdict = "SKIP"
        elif forecast.forecast == outcome.actual_side:
            correct += 1
            verdict = "CORRECT"
        else:
            incorrect += 1
            verdict = "INCORRECT"

        rows.append({
            "market_ticker": forecast.market_ticker,
            "market_close": _close_iso(forecast),
            "forecast": forecast.forecast,
            "confidence": forecast.confidence,
            "directional_probability": forecast.directional_probability,
            "p_yes": forecast.p_yes,
            "p_no": forecast.p_no,
            "strike": forecast.strike,
            "seconds_remaining": forecast.seconds_remaining,
            "reason": forecast.reason,
            "verdict": verdict,
            "settlement_status": "SETTLED" if outcome is not None else "PENDING",
            "actual_side": outcome.actual_side if outcome is not None else None,
            "model_version": forecast.model_version,
        })

    decided = correct + incorrect
    total_resolved = decided + settled_skips
    return {
        "summary": {
            "total_final_forecasts": len(rows),
            "correct": correct,
            "incorrect": incorrect,
            "settled_directional_forecasts": decided,
            "accuracy": correct / decided if decided else None,
            "skipped": skipped,
            "settled_skips": settled_skips,
            "pending_settlement": pending,
            "settled_decision_coverage": decided / total_resolved if total_resolved else None,
        },
        "forecasts": rows,
    }


_PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>KalshiEdge | Final Forecast History</title>
<style>
:root{color-scheme:dark;font-family:Inter,"Segoe UI",system-ui,sans-serif;background:#080c16;color:#eef2fc}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at 8% 0%,#122346 0%,#080c16 48%);min-height:100vh}
main{max-width:1280px;margin:auto;padding:38px 28px 80px}
.kicker{font-size:11px;letter-spacing:2.3px;color:#72a6ff;font-weight:800}
h1{margin:8px 0;font-size:clamp(26px,4vw,40px);letter-spacing:-1px}
.sub{color:#9ba8c2;margin:0 0 28px;font-size:14px}
.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}
.card{border:1px solid #26344e;background:linear-gradient(160deg,#151f35,#0d1525);border-radius:15px;padding:20px}
.label{font-size:12px;color:#a1aec8;font-weight:600}.value{font-size:30px;font-weight:800;margin:9px 0 4px;font-variant-numeric:tabular-nums}
.hint{font-size:11px;color:#7787a5}.good{color:#56d9a6}.bad{color:#fa8a94}.warn{color:#f4c46d}
.toolbar{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:34px 0 14px}
h2{font-size:18px;letter-spacing:-.3px;margin:0}
#status{color:#a1aec8;font-size:12px}
.table-wrap{border:1px solid #26344e;background:#101827;border-radius:15px;overflow:auto}
table{border-collapse:collapse;width:100%;min-width:870px;font-size:13px}
th{text-align:left;background:#18233a;color:#a7b7d2;font-weight:700;font-size:11px;text-transform:uppercase;letter-spacing:.8px;padding:14px}
td{padding:15px 14px;border-top:1px solid #25314a;color:#e3e9f6;white-space:nowrap;font-variant-numeric:tabular-nums}
tr:hover td{background:#18243a}
.pill{display:inline-block;border:1px solid #48536b;border-radius:7px;padding:5px 9px;font-size:11px;font-weight:800;letter-spacing:.4px}
.pill.CORRECT,.pill.ABOVE{border-color:#236b5d;color:#58ddb0;background:#102d2c}
.pill.INCORRECT,.pill.BELOW{border-color:#744451;color:#ffa3af;background:#311b27}
.pill.SKIP{border-color:#6d5738;color:#eec987;background:#29231c}
.pill.PENDING{border-color:#445575;color:#a0bff4;background:#1b2941}
.muted{color:#8e9bb5}.foot{margin-top:18px;color:#7586a5;font-size:12px;line-height:1.6}
@media(max-width:760px){main{padding:25px 14px}.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.card{padding:15px}.value{font-size:25px}}
</style></head><body><main>
<div class="kicker">KALSHIEDGE / READ-ONLY RESEARCH</div>
<h1>Final forecast history</h1>
<p class="sub">One locked forecast per 15-minute BTC market · Official Kalshi settlement outcomes · No order execution</p>
<div class="kpis">
<div class="card"><div class="label">Settled directional accuracy</div><div id="accuracy" class="value good">—</div><div id="accuracy-detail" class="hint">No settled predictions yet</div></div>
<div class="card"><div class="label">Correct / Incorrect</div><div id="results" class="value">0 / 0</div><div class="hint">Settled ABOVE / BELOW only</div></div>
<div class="card"><div class="label">SKIP decisions</div><div id="skips" class="value warn">0</div><div class="hint">Excluded from accuracy</div></div>
<div class="card"><div class="label">Pending settlement</div><div id="pending" class="value">0</div><div class="hint">Not scored before Kalshi finalizes</div></div>
</div>
<div class="toolbar"><h2>Recorded forecasts</h2><span id="status">Connecting…</span></div>
<div class="table-wrap"><table><thead><tr><th>Market close (local)</th><th>Market</th><th>Forecast</th><th>Probability</th><th>Confidence</th><th>Official outcome</th><th>Result</th></tr></thead>
<tbody id="records"><tr><td colspan="7" class="muted">Loading recorded forecasts…</td></tr></tbody></table></div>
<p class="foot">Accuracy = correct ÷ (correct + incorrect), using only officially settled directional decisions. SKIP and pending markets are excluded. The dashboard refreshes every 5 seconds; settlement polling happens every 30 seconds. Percentages reflect measured outcomes only, not guaranteed performance.</p>
</main><script>
const $=id=>document.getElementById(id);
const pct=n=>n==null?"—":(100*n).toFixed(2)+"%";
const field=v=>v==null?"—":String(v);
function pill(label){let span=document.createElement("span");span.className="pill "+label;span.textContent=label;return span}
function cell(tr,v){let td=document.createElement("td");if(v instanceof Node)td.appendChild(v);else td.textContent=field(v);tr.appendChild(td)}
async function refresh(){
 try{
  const response=await fetch("/api/history",{cache:"no-store"});
  if(!response.ok)throw Error("HTTP "+response.status);
  const data=await response.json(),s=data.summary;
  $("accuracy").textContent=pct(s.accuracy);
  $("accuracy-detail").textContent=s.settled_directional_forecasts+" settled directional decisions";
  $("results").textContent=s.correct+" / "+s.incorrect;
  $("skips").textContent=s.skipped;
  $("pending").textContent=s.pending_settlement;
  $("status").textContent=s.total_final_forecasts+" forecasts · Updated "+new Date().toLocaleTimeString();
  const body=$("records");body.replaceChildren();
  if(!data.forecasts.length){let tr=document.createElement("tr");cell(tr,"No final forecasts recorded yet.");body.appendChild(tr)}
  for(const f of data.forecasts){
   let tr=document.createElement("tr");
   cell(tr,new Date(f.market_close).toLocaleString());
   cell(tr,f.market_ticker);
   cell(tr,pill(f.forecast));
   cell(tr,pct(f.directional_probability));
   cell(tr,pct(f.confidence));
   cell(tr,f.actual_side??"Awaiting settlement");
   cell(tr,pill(f.verdict));
   body.appendChild(tr);
  }
 }catch(e){$("status").textContent="Refresh error: "+e.message}
}
refresh();setInterval(refresh,5000);
</script></body></html>
"""


def _make_handler(root: Path) -> type[BaseHTTPRequestHandler]:
    class HistoryHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/":
                content = _PAGE.encode("utf-8")
                kind = "text/html; charset=utf-8"
            elif self.path == "/api/history":
                content = json.dumps(read_final_history(root), separators=(",", ":"), allow_nan=False).encode("utf-8")
                kind = "application/json; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    return HistoryHandler


def run_final_history(root: Path, base_url: str, *, port: int = 8899, once: bool = False) -> int:
    """One-shot settlement sync or persistent localhost dashboard + settlement polling."""

    root = root.resolve()
    if once:
        result = sync_final_settlements(root, base_url)
        print(json.dumps({"sync": result, **read_final_history(root)}, indent=2))
        return 0 if result["errors"] == 0 else 2

    stop = threading.Event()

    def resolve_loop() -> None:
        while not stop.is_set():
            try:
                result = sync_final_settlements(root, base_url)
                if result["newly_resolved"]:
                    LOG.info("Saved %s new official Kalshi settlements", result["newly_resolved"])
            except Exception:
                LOG.exception("Settlement polling failed; pending forecasts remain unscored")
            if stop.wait(POLL_SECONDS):
                return

    worker = threading.Thread(target=resolve_loop, name="kalshi-final-settlement-reader", daemon=True)
    server = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(root))
    server.daemon_threads = True
    worker.start()
    print(f"KalshiEdge final history: http://127.0.0.1:{port}/", flush=True)
    print("Read-only local webpage; Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        worker.join(timeout=2)
    return 0
