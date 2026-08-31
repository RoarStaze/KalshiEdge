from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import secrets

from .protocol import RawEvent


@dataclass(frozen=True)
class FinalizedSegment:
    data_path: Path
    hash_path: Path
    event_count: int
    sha256: str


class RawSegmentWriter:
    def __init__(self, root: str | Path, *, source: str, max_events: int = 1000, fsync_every: int = 1) -> None:
        if max_events < 1 or fsync_every < 1:
            raise ValueError("max_events and fsync_every must be >= 1")
        self.root = Path(root)
        self.source = source
        self.max_events = max_events
        self.fsync_every = fsync_every
        self._file = None
        self._temp_path: Path | None = None
        self._final_path: Path | None = None
        self._count = 0
        self._hasher = sha256()

    def _open(self, event: RawEvent) -> None:
        dt = datetime.fromtimestamp(event.receive_ts_ns / 1_000_000_000, tz=timezone.utc)
        directory = self.root / "raw" / f"source={self.source}" / f"date={dt:%Y-%m-%d}" / f"hour={dt:%H}"
        directory.mkdir(parents=True, exist_ok=True)
        token = secrets.token_hex(6)
        # Start with the full nanosecond receive timestamp so lexical path order is
        # chronological even when segment rotations occur within the same microsecond.
        stem = f"segment-{event.receive_ts_ns:020d}-{dt:%Y%m%dT%H%M%S}Z-{token}"
        self._temp_path = directory / f".{stem}.open"
        self._final_path = directory / f"{stem}.jsonl"
        self._file = self._temp_path.open("xb")

    def append(self, event: RawEvent) -> FinalizedSegment | None:
        if self._file is None:
            self._open(event)
        assert self._file is not None
        encoded = (json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        self._file.write(encoded)
        self._hasher.update(encoded)
        self._count += 1
        if self._count % self.fsync_every == 0:
            self._file.flush()
            os.fsync(self._file.fileno())
        if self._count >= self.max_events:
            return self.finalize()
        return None

    def finalize(self) -> FinalizedSegment:
        if self._file is None or self._temp_path is None or self._final_path is None or self._count == 0:
            raise RuntimeError("no events to finalize")
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()
        self._temp_path.replace(self._final_path)
        digest = self._hasher.hexdigest()
        hash_path = self._final_path.with_suffix(self._final_path.suffix + ".sha256")
        hash_temp = hash_path.with_suffix(hash_path.suffix + ".open")
        with hash_temp.open("x", encoding="utf-8") as manifest:
            manifest.write(f"{digest}  {self._final_path.name}\n")
            manifest.flush()
            os.fsync(manifest.fileno())
        hash_temp.replace(hash_path)
        result = FinalizedSegment(self._final_path, hash_path, self._count, digest)
        self._file = None
        self._temp_path = None
        self._final_path = None
        self._count = 0
        self._hasher = sha256()
        return result


def verify_segment(data_path: str | Path, hash_path: str | Path) -> bool:
    data = Path(data_path)
    try:
        parts = Path(hash_path).read_text(encoding="utf-8").split()
        if not parts:
            return False
        expected = parts[0]
        hasher = sha256()
        with data.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
    except OSError:
        return False
    return hasher.hexdigest() == expected
