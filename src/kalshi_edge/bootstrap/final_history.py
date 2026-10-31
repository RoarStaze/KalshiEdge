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
from urllib.parse import parse_qs, urlsplit
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .final_forecast import FINAL_CHECKPOINT_SECONDS, FinalForecastRecord
from .dashboard_ui import PAGE as _PAGE
from .round_price_ui import integrate_round_price_hero
from .site_pages import render_site_page
from .api_credentials_ui import integrate_api_credentials
from .manual_trade_ui import integrate_manual_trade
from kalshi_edge.trading.vault import CredentialVault, VaultError
from .live_dashboard import read_live_dashboard

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

    # The settled outcome is the only trigger for adding new training cases.
    # Local import avoids coupling the live settlement reader to training.
    from .mistake_learning import enroll_settled_forecasts

    pool = enroll_settled_forecasts(root)
    from .correct_learning import enroll_correct_forecasts
    refresher = enroll_correct_forecasts(root)
    stats["mistake_cases_added"] = pool["new_cases"]
    stats["mistake_cases_eligible"] = pool["eligible"]
    stats["mistake_pool_errors"] = pool["source_errors"]
    stats["correct_cases_added"] = refresher["new_cases"]
    stats["correct_cases_eligible"] = refresher["eligible"]
    stats["correct_pool_errors"] = refresher["errors"]
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


def _make_handler(root: Path, controller=None, *, paper_trader=None, credential_vault=None, manual_trader=None, auto_trader=None) -> type[BaseHTTPRequestHandler]:
    from .training_cycle import TrainingController, CycleError
    from kalshi_edge.trading.paper import PaperTrader  # lazy: paper imports final_history
    training_controller = controller or TrainingController(root)
    trader = paper_trader if paper_trader is not None else PaperTrader(root)
    try:
        vault = credential_vault if credential_vault is not None else CredentialVault()
    except VaultError:
        # Linux CI and non-Windows hosts have no Windows DPAPI. Keep the
        # read-only/paper dashboard functional, never persist secrets.
        vault = None

    from kalshi_edge.trading.manual_trade import ManualTrader, TradingError
    from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader, AutoTradeError
    try:
        manual = manual_trader if manual_trader is not None else ManualTrader(root, vault=vault)
    except VaultError:
        manual = None
    auto = auto_trader if auto_trader is not None else (
        ArmedAutoTrader(manual, lambda: read_live_dashboard(root),
                        history_path=root / "data" / "autotrade_history.sqlite3")
        if manual is not None else None
    )

    # Build the three local routes once. Do not inject AutoTrader UI;
    # legacy signed API routes remain available to authorized localhost tools.
    site_template = integrate_api_credentials(
        integrate_manual_trade(integrate_round_price_hero(_PAGE)))
    routed_pages = {name: render_site_page(site_template, name).encode("utf-8")
                    for name in ("trade", "train", "history", "api")}

    class HistoryHandler(BaseHTTPRequestHandler):
        def _json(self, status: int, data: dict) -> None:
            content = json.dumps(data, separators=(",", ":"), allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def _auto_get(self) -> None:
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                self._json(403, {"error":"Local host required"})
                return
            if auto is None or vault is None or not vault.exists():
                self._json(503, {"error":"Connect Kalshi API credentials first"})
                return
            self._json(200, auto.history() if self.path == "/api/armed-trade/history" else auto.status())

        def _auto_post(self) -> None:
            expected = f"http://127.0.0.1:{self.server.server_port}"
            if (self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"
                or self.headers.get("Origin") != expected
                or self.headers.get("X-KalshiEdge-Action") != "armed-trade"
                or self.headers.get("Content-Type") != "application/json"
                or self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none")):
                self._json(403, {"error":"Same-origin AutoTrade action required"})
                return
            if auto is None or vault is None or not vault.exists():
                self._json(503, {"error":"Live Kalshi credentials unavailable"})
                return
            try:
                length = int(self.headers.get("Content-Length") or "-1")
                if not 0 <= length <= 160:
                    raise AutoTradeError("Invalid AutoTrade request length")
                payload = json.loads(self.rfile.read(length))
                if self.path == "/api/armed-trade/arm":
                    if not isinstance(payload, dict) or set(payload) != {"amount"} or not isinstance(payload["amount"], str):
                        raise AutoTradeError("Only a dollar amount may be armed")
                    result = auto.arm(payload["amount"], continuous=True)
                elif self.path == "/api/armed-trade/cancel":
                    if payload != {}:
                        raise AutoTradeError("Cancel accepts no parameters")
                    result = auto.cancel()
                else:
                    self._json(404, {"error":"Unknown AutoTrade action"})
                    return
                self._json(200, result)
            except (AutoTradeError, ValueError, TypeError, KeyError) as exc:
                self._json(409, {"error":str(exc)[:180]})
            except Exception:
                LOG.exception("AutoTrade control action failed")
                self._json(503, {"error":"AutoTrade control failed; inspect local logs"})

        def _manual_ticker(self) -> str:
            data = read_live_dashboard(root)
            ticker = data.get("market_ticker")
            if not isinstance(ticker, str) or not SAFE_TICKER.fullmatch(ticker):
                raise TradingError("No active KXBTC15M market from the live predictor")
            return ticker

        def _manual_get(self) -> None:
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                self._json(403, {"error": "local host required"})
                return
            if manual is None or vault is None or not vault.exists():
                self._json(503, {"error": "Connect your Kalshi API credentials on the API page first"})
                return
            try:
                parsed = urlsplit(self.path)
                ticker = self._manual_ticker()
                if parsed.path == "/api/manual-trade/market":
                    result = manual.market(ticker)
                elif parsed.path == "/api/manual-trade/sell-preview":
                    if parsed.query:
                        raise TradingError("Sell All does not accept quantity or overrides")
                    result = manual.preview_all(ticker)
                elif parsed.path == "/api/manual-trade/preview":
                    params = parse_qs(parsed.query, keep_blank_values=True)
                    if set(params) != {"action", "outcome", "units", "amount"} or any(len(items) != 1 for items in params.values()):
                        raise TradingError("Invalid manual quote options")
                    result = manual.preview(
                        ticker, params["action"][0], params["outcome"][0],
                        params["units"][0], params["amount"][0],
                    )
                else:
                    self._json(404, {"error": "Unknown manual trading endpoint"})
                    return
                self._json(200, result)
            except (TradingError, ValueError, KeyError, TypeError) as exc:
                self._json(409, {"error": str(exc)[:180]})
            except httpx.HTTPStatusError as exc:
                self._json(502, {"error": f"Kalshi API error HTTP {exc.response.status_code}"})
            except (httpx.HTTPError, OSError, VaultError):
                self._json(503, {"error": "Could not retrieve live Kalshi account/market data"})

        def _manual_post(self) -> None:
            expected = f"http://127.0.0.1:{self.server.server_port}"
            if (self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"
                or self.headers.get("Origin") != expected
                or self.headers.get("X-KalshiEdge-Action") != "manual-trade"
                or self.headers.get("Content-Type") != "application/json"
                or self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none")):
                self._json(403, {"error": "Same-origin manual trade confirmation required"})
                return
            if manual is None or vault is None or not vault.exists():
                self._json(503, {"error": "Live Kalshi trading credentials unavailable"})
                return
            try:
                length = int(self.headers.get("Content-Length") or "-1")
                if not 0 <= length <= 256:
                    raise TradingError("Invalid order request size")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict) or set(body) != {"preview_id"} or not isinstance(body["preview_id"], str):
                    raise TradingError("Expected a single valid preview ID")
                ticker = self._manual_ticker()
                result = manual.place(body["preview_id"], ticker)
                self._json(200, result)
            except (TradingError, ValueError, TypeError, KeyError) as exc:
                self._json(409, {"error": str(exc)[:180]})
            except httpx.HTTPStatusError as exc:
                self._json(502, {"error": f"Kalshi rejected order HTTP {exc.response.status_code}; check Kalshi order history"})
            except Exception:
                LOG.exception("Manual trade outcome unknown; never automatically retry")
                self._json(503, {"error": "Order outcome unknown: inspect Kalshi account orders before retrying"})

        def _autotrader_post(self) -> None:
            # Localhost Host + Origin checks prevent a malicious remote page
            # from using the local credential/action endpoint.
            origin = f"http://127.0.0.1:{self.server.server_port}"
            if (
                self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}"
                or self.headers.get("Origin") != origin
                or self.headers.get("X-KalshiEdge-Action") != "autotrader"
                or self.headers.get("Content-Type") != "application/json"
                or self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none")
            ):
                self._json(403, {"error": "same-origin AutoTrader action required"})
                return

            try:
                length = int(self.headers.get("Content-Length") or "-1")
                if not 0 <= length <= 18000:
                    raise ValueError("invalid content length")
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("expected JSON object")
            except (ValueError, UnicodeDecodeError):
                self._json(400, {"error": "invalid AutoTrader action payload"})
                return

            if self.path in ("/api/autotrader/paper/enable", "/api/autotrader/paper/disable"):
                if data != {}:
                    self._json(400, {"error": "unexpected paper action options"})
                    return
                trader.enable(self.path.endswith("/enable"))
                self._json(200, {"mode": "PAPER_ONLY", "enabled": trader.enabled()})
                return

            if self.path.startswith("/api/autotrader/credentials") and vault is None:
                self._json(503, {"error": "Windows DPAPI credential storage unavailable"})
                return

            if self.path == "/api/autotrader/credentials/delete":
                if data != {}:
                    self._json(400, {"error": "unexpected delete action options"})
                    return
                try:
                    vault.delete()
                except (OSError, VaultError):
                    self._json(503, {"error": "cannot delete local credentials"})
                    return
                self._json(200, {"saved": False})
                return

            if self.path == "/api/autotrader/credentials":
                if set(data) != {"api_key_id", "private_key_pem"}:
                    self._json(400, {"error": "invalid credential fields"})
                    return
                try:
                    vault.save(data["api_key_id"], data["private_key_pem"])
                except (VaultError, OSError, TypeError):
                    self._json(400, {"error": "invalid credentials or Windows secure storage unavailable"})
                    return
                self._json(200, {"saved": True})
                return

            self._json(404, {"error": "unknown AutoTrader action"})

        def do_POST(self) -> None:
            if self.path.startswith("/api/armed-trade/"):
                self._auto_post()
                return
            if self.path == "/api/manual-trade/order":
                self._manual_post()
                return
            if self.path.startswith("/api/autotrader/"):
                self._autotrader_post()
                return
            if self.path != "/api/training/start":
                self._json(404, {"error": "endpoint not found"})
                return
            # Loopback bind alone does not stop cross-site browser POSTs.
            # Refuse cross-origin forms and requests lacking a non-simple
            # custom action header. No CORS authorization is emitted.
            expected = f"http://127.0.0.1:{self.server.server_port}"
            if (
                self.headers.get("Origin") != expected
                or self.headers.get("X-KalshiEdge-Action") != "train"
                or self.headers.get("Content-Type") != "application/json"
            ):
                self._json(403, {"error": "same-origin Train Now action required"})
                return
            try:
                length = int(self.headers.get("Content-Length") or "-1")
            except ValueError:
                length = -1
            if not 0 <= length <= 256:
                self._json(400, {"error": "invalid action body size"})
                return
            try:
                body = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, ValueError):
                self._json(400, {"error": "invalid JSON"})
                return
            if body != {}:
                self._json(400, {"error": "Train Now accepts no overrides"})
                return
            try:
                result = training_controller.start()
            except CycleError as exc:
                self._json(409, {"error": str(exc)})
                return
            except Exception:
                LOG.exception("Training process could not be started")
                self._json(500, {"error": "training process launch failed; inspect server logs"})
                return
            self._json(202, result)

        def _local_redirect(self, destination: str) -> None:
            # Local-only fixed destinations; never redirect to a user-supplied URL.
            self.send_response(302)
            self.send_header("Location", destination)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _has_saved_credentials(self) -> bool:
            try:
                return vault is not None and vault.exists()
            except (VaultError, OSError):
                return False

        def do_GET(self) -> None:
            if self.path in ("/api/armed-trade/status", "/api/armed-trade/history"):
                self._auto_get()
                return
            if urlsplit(self.path).path.startswith("/api/manual-trade/"):
                self._manual_get()
                return
            if self.path == "/api/autotrader/status":
                if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                    self._json(403, {"error": "invalid local host"})
                    return
                status = trader.status()
                try:
                    if vault is None:
                        raise VaultError("Windows DPAPI credential storage unavailable")
                    status["credentials_saved"] = vault.exists()
                    status["credentials_supported"] = True
                except (VaultError, OSError):
                    status["credentials_saved"] = False
                    status["credentials_supported"] = False
                self._json(200, status)
                return
            path = urlsplit(self.path).path
            if path == "/":
                self._local_redirect("/trade" if self._has_saved_credentials() else "/api")
                return
            if path == "/trade" and not self._has_saved_credentials():
                self._local_redirect("/api")
                return
            if path in ("/trade", "/train", "/history", "/api"):
                content = routed_pages[path[1:]]
                kind = "text/html; charset=utf-8"
            elif self.path == "/api/history":
                content = json.dumps(read_final_history(root), separators=(",", ":"), allow_nan=False).encode("utf-8")
                kind = "application/json; charset=utf-8"
            elif self.path == "/api/live":
                content = json.dumps(read_live_dashboard(root), separators=(",", ":"), allow_nan=False).encode("utf-8")
                kind = "application/json; charset=utf-8"
            elif self.path == "/api/training":
                try:
                    data = training_controller.status()
                except CycleError as exc:
                    self._json(503, {"error": str(exc)})
                    return
                content = json.dumps(data, separators=(",", ":"), allow_nan=False).encode("utf-8")
                kind = "application/json; charset=utf-8"
            elif self.path == "/api/health":
                content = b'{"app":"kalshiedge","service":"unified-dashboard","ready":true}'
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

    from .training_cycle import TrainingController
    controller = TrainingController(root)
    worker = threading.Thread(target=resolve_loop, name="kalshi-final-settlement-reader", daemon=True)
    from kalshi_edge.trading.paper import PaperTrader  # lazy: avoid circular import
    paper_trader = PaperTrader(root)
    from kalshi_edge.trading.manual_trade import ManualTrader
    try:
        credential_vault = CredentialVault()
    except VaultError:
        credential_vault = None
    try:
        manual_trader = ManualTrader(root, vault=credential_vault) if credential_vault is not None else None
    except VaultError:
        manual_trader = None
    from kalshi_edge.trading.armed_autotrade import ArmedAutoTrader
    auto_trader = ArmedAutoTrader(
        manual_trader, lambda: read_live_dashboard(root),
        history_path=root / "data" / "autotrade_history.sqlite3"
    ) if manual_trader else None
    paper_worker = threading.Thread(
        target=paper_trader.run_forever, args=(stop,),
        name="kalshi-paper-autotrader", daemon=True,
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", port),
        _make_handler(root, controller=controller, paper_trader=paper_trader,
                      credential_vault=credential_vault, manual_trader=manual_trader, auto_trader=auto_trader),
    )
    server.daemon_threads = True
    worker.start()
    paper_worker.start()
    print(f"KalshiEdge final history: http://127.0.0.1:{port}/", flush=True)
    print("Read-only local webpage; Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        paper_trader.enable(False)
        controller.stop()
        server.server_close()
        worker.join(timeout=2)
        paper_worker.join(timeout=3)
        paper_trader.market_client.close()
        if auto_trader is not None:
            auto_trader.close()
        if manual_trader is not None:
            manual_trader.close()
    return 0
