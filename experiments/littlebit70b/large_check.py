"""Read-only scout for >=27B Samsung-LittleBit-family releases.

Searches public Hub *metadata*, not model weights. Names never establish
architecture, quantization fidelity, quality, or runtime support.
"""
import argparse
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HF_API = "https://huggingface.co/api/models"
MIN_PARAMETERS_B = 27
MAX_RESULTS = 100
TIMEOUT_SECONDS = 15
WEIGHT_SUFFIXES = (".safetensors", ".bin", ".pt", ".pth")
# Assessed from the uploader's model card, NOT inferred from a name.
UNTRAINED_27B = {
    "Haverbex/Qwen3.8-27B-LittleBit-0.1bit",
    "Haverbex/Qwen3.8-27B-LittleBit-0.55bit",
    "Haverbex/Qwen3.8-27B-LittleBit-1bit",
}


def parameter_billions(model_id):
    """Infer a candidate size from a model ID; deliberately NOT verification."""
    # Accept 70B, 72B, 27B; ignore decimal components such as '3.8'.
    sizes = [int(v) for v in re.findall(r"(?<![\d.])(\d{1,3})[bB](?![A-Za-z0-9])", model_id)]
    return max(sizes) if sizes else None


def classify_model(model, min_parameters_b=MIN_PARAMETERS_B):
    """Return a cautious candidate report, or None if outside the scope."""
    model_id = model.get("id") or ""
    if not isinstance(model_id, str):
        return None
    tags = [t.lower() for t in (model.get("tags") or []) if isinstance(t, str)]
    # Do not confuse 'Little-Bitch' model names or branded GGUF with Samsung LittleBit.
    if "littlebit" not in model_id.lower() and "littlebit" not in tags:
        return None
    size_b = parameter_billions(model_id)
    if size_b is None or size_b < min_parameters_b:
        return None
    siblings = model.get("siblings") or []
    names = [s.get("rfilename", "") for s in siblings if isinstance(s, dict)]
    weights = [s for s in siblings if isinstance(s, dict) and
               (s.get("rfilename") or "").lower().endswith(WEIGHT_SUFFIXES)]
    sizes = [s.get("size") for s in weights]
    stored = sum(sizes) if weights and all(isinstance(s, int) and s >= 0 for s in sizes) else None
    has_config = "config.json" in names
    has_lb_config = "littlebit_config.json" in names
    known_untrained = model_id in UNTRAINED_27B
    status = "REJECTED_PUBLISHED_QUALITY" if known_untrained else "UNVERIFIED"
    reasons = []
    if known_untrained:
        reasons += ["Uploader reports zero recovery-training steps and degraded task performance.",
                    "Uploader's loader expands packed factors; no efficient packed inference kernel demonstrated."]
    else:
        reasons.append("Independent proof of preserved quality and a working packed runtime is missing.")
    if not (weights and has_config and has_lb_config):
        reasons.append("A complete Samsung-compatible packed checkpoint manifest is not established.")
    if stored is None:
        reasons.append("Model weight file sizes are not established by the metadata returned.")
    return {
        "id": model_id,
        "url": "https://huggingface.co/" + urllib.parse.quote(model_id, safe="/"),
        "model_size_billions_inferred_from_name": size_b,
        "parameters_verified": False,
        "checkpoint_files_present": bool(weights and has_config and has_lb_config),
        "stored_weight_bytes": stored,
        "status": status,
        "training_quality_verified": False,
        "packed_runtime_verified": False,
        "ready_to_run": False,
        "reasons": reasons,
    }


def scan_models(models, min_parameters_b=MIN_PARAMETERS_B):
    candidates = []
    for model in models:
        report = classify_model(model, min_parameters_b)
        if report is not None:
            candidates.append(report)
    candidates.sort(key=lambda c: (-c["model_size_billions_inferred_from_name"], c["id"]))
    return {
        "scope": f"Samsung LittleBit candidates >= {min_parameters_b}B by repository-name claim",
        "candidates": candidates,
        "ready_to_run_large_model": False,
        "warning": "No candidate is certified by its name or file metadata. Never download automatically.",
    }


def get_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "LittleBit-LargeScout/1.0"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return json.load(response)


def discover_online():
    """Check two Hub searches and inspect candidates without downloading weights."""
    found_ids = set()
    errors = []
    for query_params in ({"search": "littlebit", "limit": MAX_RESULTS},
                         {"filter": "littlebit", "limit": MAX_RESULTS}):
        try:
            listing = get_json(HF_API + "?" + urllib.parse.urlencode(query_params))
            for record in listing:
                if isinstance(record, dict) and isinstance(record.get("id"), str):
                    found_ids.add(record["id"])
        except (urllib.error.URLError, ValueError, OSError) as exc:
            errors.append(f"Hub search unavailable: {type(exc).__name__}: {exc}")
    details = []
    for model_id in sorted(found_ids):
        if parameter_billions(model_id) is None or parameter_billions(model_id) < MIN_PARAMETERS_B:
            continue
        if "littlebit" not in model_id.lower():
            continue
        try:
            url = HF_API + "/" + urllib.parse.quote(model_id, safe="/") + "?blobs=true"
            details.append(get_json(url))
        except (urllib.error.URLError, ValueError, OSError) as exc:
            errors.append(f"Unable to inspect {model_id}: {type(exc).__name__}: {exc}")
            details.append({"id": model_id})
    return details, errors


def render_text(report):
    lines = ["LITTLEBIT LARGE-MODEL CHECK (no weights downloaded)"]
    for c in report["candidates"]:
        size = "unknown" if c["stored_weight_bytes"] is None else f"{c['stored_weight_bytes'] / 1e9:.2f} GB"
        lines.append(f"- {c['id']}: {c['status']}; file weight bytes: {size}")
        lines.extend(f"    {reason}" for reason in c["reasons"])
    if not report["candidates"]:
        lines.append("No >=27B LittleBit candidates were returned by this search.")
    for err in report.get("search_errors", []):
        lines.append("SEARCH ERROR: " + err)
    lines.append("RESULT: no verified runnable >=27B LittleBit model. No model downloaded.")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--output", type=Path, help="Optionally save the report as JSON")
    args = parser.parse_args()
    models, errors = discover_online()
    report = scan_models(models)
    report["search_errors"] = errors
    if args.output is not None:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2) if args.json else render_text(report))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
