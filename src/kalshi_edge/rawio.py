from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


def iter_segment_paths(root: str | Path, *, source: str | None = None) -> list[Path]:
    root_path = Path(root)
    source_pattern = "source=*" if source is None else f"source={source}"
    return sorted(root_path.glob(f"raw/{source_pattern}/date=*/hour=*/segment-*.jsonl"))


def iter_raw_events(
    root: str | Path,
    *,
    source: str | None = None,
    skip_malformed: bool = False,
) -> Iterator[dict[str, Any]]:
    for path in iter_segment_paths(root, source=source):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    if skip_malformed:
                        continue
                    raise
                if not isinstance(event, dict):
                    if skip_malformed:
                        continue
                    raise ValueError(f"raw event record is not an object: {path}")
                yield event
