from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import websockets

from .auth import create_auth_headers, load_private_key
from .config import CollectorSettings
from .discovery import extract_market_tickers, fetch_market_payload, fetch_open_events_payload, fetch_series_payload
from .integrity import SequenceStatus, SequenceTracker
from .protocol import RawEvent, build_subscriptions, normalize_control_payload, normalize_rest_payload, normalize_ws_frame, normalize_ws_message
from .runtime import build_runtime_snapshot
from .storage import RawSegmentWriter


logger = logging.getLogger(__name__)


class SequenceIntegrityError(RuntimeError):
    pass


class SequenceGapError(SequenceIntegrityError):
    pass


class ProtocolMessageError(RuntimeError):
    pass


class TargetMarketLifecycleChange(RuntimeError):
    pass


def is_target_lifecycle_event(payload: dict[str, Any], series_ticker: str) -> bool:
    if payload.get("type") != "market_lifecycle_v2":
        return False
    msg = payload.get("msg") or {}
    ticker = str(msg.get("market_ticker", ""))
    return ticker.startswith(f"{series_ticker}-") and msg.get("event_type") in {
        "created",
        "activated",
        "deactivated",
        "close_date_updated",
        "determined",
        "settled",
        "price_level_structure_updated",
        "metadata_updated",
    }


@dataclass
class MessageProcessor:
    writer: RawSegmentWriter
    connection_id: str = "unknown"

    def __post_init__(self) -> None:
        self.sequences = SequenceTracker()

    def _accept(self, event: RawEvent) -> RawEvent:
        self.writer.append(event)
        if event.message_type == "malformed_frame":
            raise ProtocolMessageError("malformed WebSocket frame persisted; reconnecting fail-closed")
        if event.sid is None or event.seq is None:
            return event
        status = self.sequences.observe(event.sid, event.seq)
        if status is SequenceStatus.GAP:
            raise SequenceGapError(f"sequence gap sid={event.sid} seq={event.seq}")
        if status in {SequenceStatus.DUPLICATE, SequenceStatus.OUT_OF_ORDER}:
            raise SequenceIntegrityError(f"invalid sequence sid={event.sid} seq={event.seq} status={status.value}")
        return event

    def process(self, payload: dict[str, Any], *, receive_ts_ns: int) -> RawEvent:
        return self._accept(normalize_ws_message(payload, receive_ts_ns=receive_ts_ns, connection_id=self.connection_id))

    def process_frame(self, raw: str | bytes, *, receive_ts_ns: int) -> RawEvent:
        return self._accept(normalize_ws_frame(raw, receive_ts_ns=receive_ts_ns, connection_id=self.connection_id))


class KalshiCollector:
    def __init__(self, settings: CollectorSettings) -> None:
        self.settings = settings

    async def run_forever(self) -> None:
        delay = self.settings.reconnect_initial_seconds
        while True:
            try:
                events_payload = await fetch_open_events_payload(
                    base_url=self.settings.rest_base_url,
                    series_ticker=self.settings.series_ticker,
                )
                markets = extract_market_tickers(events_payload, self.settings.series_ticker)
                if not markets:
                    logger.warning(
                        "no open %s market found; staying connected to BRTI/lifecycle while awaiting creation",
                        self.settings.series_ticker,
                    )
                await self._run_connection(markets, events_payload)
                delay = self.settings.reconnect_initial_seconds
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("collector connection ended; reconnecting: %s", exc)
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.settings.reconnect_max_seconds)

    async def _run_connection(self, markets: list[str], events_payload: dict[str, Any]) -> None:
        key_id, private_key_path = self.settings.require_credentials()
        private_key = load_private_key(private_key_path)
        headers = create_auth_headers(
            key_id=key_id,
            private_key=private_key,
            method="GET",
            path="/trade-api/ws/v2",
        )
        writer = RawSegmentWriter(
            self.settings.data_dir,
            source="kalshi_ws",
            max_events=self.settings.segment_max_events,
            fsync_every=self.settings.fsync_every,
        )
        rest_writer = RawSegmentWriter(
            self.settings.data_dir,
            source="kalshi_rest",
            max_events=max(10, self.settings.segment_max_events),
            fsync_every=1,
        )
        control_writer = RawSegmentWriter(
            self.settings.data_dir,
            source="collector_control",
            max_events=10,
            fsync_every=1,
        )
        connection_id = uuid.uuid4().hex
        processor = MessageProcessor(writer, connection_id=connection_id)
        try:
            control_writer.append(normalize_control_payload(
                build_runtime_snapshot(self.settings),
                message_type="collector_session_snapshot",
                receive_ts_ns=time.time_ns(),
                session_id=connection_id,
            ))
            series_payload = await fetch_series_payload(
                base_url=self.settings.rest_base_url,
                series_ticker=self.settings.series_ticker,
            )
            rest_writer.append(normalize_rest_payload(
                series_payload,
                message_type="series_snapshot",
                receive_ts_ns=time.time_ns(),
                request_id=connection_id,
            ))
            rest_writer.append(normalize_rest_payload(
                events_payload,
                message_type="open_events_snapshot",
                receive_ts_ns=time.time_ns(),
                request_id=connection_id,
            ))
            async with websockets.connect(
                self.settings.ws_url,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=20,
                max_queue=4096,
            ) as ws:
                for message in build_subscriptions(markets):
                    await ws.send(json.dumps(message, separators=(",", ":")))
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=self.settings.stale_after_seconds)
                    event = processor.process_frame(raw, receive_ts_ns=time.time_ns())
                    payload = event.payload
                    if is_target_lifecycle_event(payload, self.settings.series_ticker):
                        msg = payload.get("msg") or {}
                        if msg.get("event_type") in {"determined", "settled"} and msg.get("market_ticker"):
                            market_payload = await fetch_market_payload(
                                base_url=self.settings.rest_base_url,
                                market_ticker=str(msg["market_ticker"]),
                            )
                            rest_writer.append(normalize_rest_payload(
                                market_payload,
                                message_type="market_settlement_snapshot",
                                receive_ts_ns=time.time_ns(),
                                request_id=connection_id,
                            ))
                        raise TargetMarketLifecycleChange("target market lifecycle changed; refresh subscriptions")
        finally:
            for segment_writer in (writer, rest_writer, control_writer):
                try:
                    segment_writer.finalize()
                except RuntimeError:
                    pass
