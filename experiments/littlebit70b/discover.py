"""Read-only Hugging Face checkpoint discovery. Never downloads weights."""
import argparse
import json
import urllib.parse
import urllib.request

API = "https://huggingface.co/api/models"
MAX_RESULTS = 100

def get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "littlebit70b-research/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)

def inspect_model(model):
    siblings = model.get("siblings") or []
    filenames = [f.get("rfilename", "") for f in siblings]
    weight_files = [n for n in filenames if n.endswith((".safetensors", ".bin"))]
    config = any(n == "config.json" for n in filenames)
    littlebit_config = any(n == "littlebit_config.json" for n in filenames)
    text = " ".join([model.get("id", ""), " ".join(model.get("tags") or [])]).lower()
    possible_70b = any(x in text for x in ("70b", "72b"))
    return {
        "id": model.get("id"),
        "url": "https://huggingface.co/" + str(model.get("id", "")),
        "possible_70b_by_name": possible_70b,
        "has_weights": bool(weight_files),
        "has_config": config,
        "has_littlebit_config": littlebit_config,
        "weight_file_count": len(weight_files),
        "gated": model.get("gated", False),
        "verified_70b": False,
        "verified_runnable": False,
        "note": "Filename and metadata are not proof of 70B parameters, trained compression, or working inference."
    }

def discover(search_terms=("littlebit",), limit=100):
    if not 1 <= limit <= MAX_RESULTS:
        raise ValueError("limit must be 1..100")
    found = {}
    for term in search_terms:
        query = urllib.parse.urlencode({"search": term, "limit": limit, "full": "true"})
        for model in get_json(API + "?" + query):
            model_id = model.get("id")
            if model_id:
                found[model_id] = model
    results = []
    for model_id, model in sorted(found.items()):
        # Fetch file manifest; failures remain explicit and never count as verification.
        try:
            detail = get_json(API + "/" + urllib.parse.quote(model_id, safe="/"))
            results.append(inspect_model(detail))
        except Exception as exc:
            results.append({"id": model_id, "inspection_error": str(exc), "verified_runnable": False})
    return results

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--search", action="append", default=None)
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--output", default=None)
    args = p.parse_args()
    report = {"checkpoint_candidates": discover(args.search or ["littlebit"], args.limit),
              "ready_to_run_70b": False,
              "reason": "A candidate must pass separate architecture, license, packed-loader and hardware inference validation."}
    result = json.dumps(report, indent=2)
    if args.output:
        from pathlib import Path
        Path(args.output).write_text(result + "\n", encoding="utf-8")
    print(result)

if __name__ == "__main__":
    main()
