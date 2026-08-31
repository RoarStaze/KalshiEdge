from __future__ import annotations

from collections import defaultdict
from typing import Sequence

from pydantic import BaseModel, ConfigDict

from .types import FeatureRow


_FORBIDDEN_TOKENS = ("settlement", "result", "outcome", "future", "label")


class LeakageFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    detail: str


class LeakageAuditReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    passed: bool
    finding_count: int
    findings: tuple[LeakageFinding, ...]


def audit_feature_row(row: FeatureRow) -> list[LeakageFinding]:
    findings: list[LeakageFinding] = []
    for source, ts_ns in sorted(row.source_max_ts_ns.items()):
        if ts_ns > row.checkpoint_ts_ns:
            findings.append(
                LeakageFinding(
                    code="FUTURE_SOURCE_TIMESTAMP",
                    detail=f"{source} timestamp {ts_ns} exceeds checkpoint {row.checkpoint_ts_ns}",
                )
            )
    for feature_name in sorted(row.features):
        lower = feature_name.lower()
        if any(token in lower for token in _FORBIDDEN_TOKENS):
            findings.append(
                LeakageFinding(
                    code="FORBIDDEN_FEATURE",
                    detail=f"feature {feature_name!r} contains label/future information",
                )
            )
    return findings


def audit_dataset_rows(rows: Sequence[FeatureRow]) -> LeakageAuditReport:
    findings: list[LeakageFinding] = []
    groups: dict[str, list[FeatureRow]] = defaultdict(list)
    seen_checkpoints: set[tuple[str, int]] = set()

    for row in rows:
        findings.extend(audit_feature_row(row))
        group = row.split_group_id or row.market_ticker
        groups[group].append(row)
        key = (row.market_ticker, row.checkpoint_ts_ns)
        if key in seen_checkpoints:
            findings.append(
                LeakageFinding(
                    code="DUPLICATE_CHECKPOINT",
                    detail=f"duplicate checkpoint for {row.market_ticker} at {row.checkpoint_ts_ns}",
                )
            )
        seen_checkpoints.add(key)

    for group, group_rows in sorted(groups.items()):
        tickers = {row.market_ticker for row in group_rows}
        dates = {row.market_date for row in group_rows}
        labels = {row.label_yes for row in group_rows}
        declared_groups = {row.split_group_id or row.market_ticker for row in group_rows}
        if len(tickers) != 1 or len(dates) != 1 or len(labels) != 1 or declared_groups != {group}:
            findings.append(
                LeakageFinding(
                    code="GROUP_INTEGRITY",
                    detail=f"split group {group!r} mixes market identity/date/label metadata",
                )
            )

    ordered = tuple(sorted(findings, key=lambda item: (item.code, item.detail)))
    return LeakageAuditReport(passed=not ordered, finding_count=len(ordered), findings=ordered)
