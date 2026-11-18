"""As-of macroeconomic headline features for experimental gold research.

A headline is *not* a point-in-time feature merely because its posted date
precedes a past market. Require both reported publication and first-observed
receipt timestamps at/before decision. A local FinBERT score is sentiment,
NOT a forecast of gold direction or an authenticated macro release.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Sequence


def asof_macro_features(
    events: Sequence[dict[str, Any]], decision_ns: int, *,
    lookback_seconds: int = 86400,
) -> dict[str, Any]:
    if type(decision_ns) is not int or decision_ns <= 0:
        raise ValueError("decision timestamp must be positive integer nanoseconds")
    if not 60 <= lookback_seconds <= 7 * 86400:
        raise ValueError("invalid macro lookback interval")
    accepted: list[float] = []
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("headline"), str) or not event["headline"].strip():
            raise ValueError("headline must be a nonempty string")
        published, observed, score = (
            event.get("published_ns"), event.get("first_observed_ns"),
            event.get("score"),
        )
        if (type(published) is not int or type(observed) is not int
                or published <= 0 or observed <= 0 or observed < published):
            raise ValueError("macro event timestamp is invalid or unverified")
        if type(score) not in (int, float) or not math.isfinite(score) or not -1 <= score <= 1:
            raise ValueError("macro sentiment score outside range")
        if published > decision_ns or observed > decision_ns:
            continue
        if decision_ns - max(published, observed) > lookback_seconds * 1_000_000_000:
            continue
        accepted.append(float(score))
    return {
        "available": bool(accepted),
        "count": len(accepted),
        "sentiment": sum(accepted) / len(accepted) if accepted else None,
        "source": "ASOF_FIRST_OBSERVED_NEWS_SENTIMENT_NONCAUSAL_TO_GOLD_PRICE",
    }


def score_finbert_local(
    events: Sequence[dict[str, Any]], *, local_model_path: Path,
) -> list[dict[str, Any]]:
    """Score only with explicitly installed LOCAL model files.

    Never fetch pretrained weights remotely, take secrets, or label a sentence
    as a gold price movement. Caller supplies timestamp evidence; no generated
    text or macro 'news' is fabricated here.
    """
    path = Path(local_model_path).resolve()
    if not path.is_dir():
        raise ValueError("FinBERT local weights directory not found")
    try:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        import torch
    except ImportError as exc:
        raise ValueError("optional local FinBERT requires transformers and torch") from exc
    tokenizer = AutoTokenizer.from_pretrained(
        str(path), local_files_only=True, trust_remote_code=False,
    )
    model = AutoModelForSequenceClassification.from_pretrained(
        str(path), local_files_only=True, trust_remote_code=False,
    )
    model.eval()
    result = []
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("headline"), str):
            raise ValueError("invalid headline")
        encoded = tokenizer(event["headline"], return_tensors="pt", max_length=256, truncation=True)
        with torch.no_grad():
            probabilities = torch.softmax(model(**encoded).logits, dim=1)[0]
        labels = {str(v).lower(): int(i) for i, v in model.config.id2label.items()}
        positive, negative = labels.get("positive"), labels.get("negative")
        if positive is None or negative is None:
            raise ValueError("local FinBERT class labels must include positive and negative")
        score = float(probabilities[positive] - probabilities[negative])
        result.append({**event, "score": score,
                       "sentiment_model": "USER_SUPPLIED_LOCAL_FINBERT"})
    return result
