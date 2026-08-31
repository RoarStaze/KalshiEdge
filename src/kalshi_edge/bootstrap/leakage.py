from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .types import FeatureRow


_FORBIDDEN_TOKENS = ("settlement", "result", "outcome", "future", "label")


class LeakageFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    detail: str


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
