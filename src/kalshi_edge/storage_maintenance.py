"""Read-only disk inventory and opt-in removal of disposable Python caches.

Durable KalshiEdge data, raw exchange evidence, models, SQLite databases,
training checkpoints and Git history are never deletion candidates.
"""
from __future__ import annotations

import argparse
import heapq
import json
import os
from pathlib import Path
import shutil
import stat
import sys


CACHE_NAMES = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})


def _is_link(path: Path) -> bool:
    """Detect symbolic links and Windows junctions/reparse-point directories."""
    return path.is_symlink() or bool(getattr(os.path, "isjunction", lambda _: False)(path))


def _root(path: Path | str) -> Path:
    raw = Path(path).expanduser()
    if _is_link(raw):
        raise ValueError("project root cannot be a symbolic link or junction")
    if not raw.is_dir():
        raise ValueError(f"project root is not a directory: {raw}")
    return raw.resolve()


def scan_storage(root: Path | str, *, top: int = 15) -> dict[str, object]:
    """Measure apparent file bytes without reading file contents or following links."""
    base = _root(root)
    if top < 1:
        raise ValueError("top must be >= 1")
    first: dict[str, int] = {}
    second: dict[str, int] = {}
    deep: dict[str, int] = {}
    caches: dict[str, int] = {}
    largest: list[tuple[int, str]] = []
    failures: list[str] = []
    file_count = 0
    link_count = 0
    total = 0

    def onerror(error: OSError) -> None:
        failures.append(str(error))

    for parent, dirs, files in os.walk(base, topdown=True, followlinks=False, onerror=onerror):
        current = Path(parent)
        safe_dirs: list[str] = []
        for name in dirs:
            candidate = current / name
            try:
                if _is_link(candidate):
                    link_count += 1
                else:
                    safe_dirs.append(name)
            except OSError as error:
                failures.append(str(error))
        dirs[:] = safe_dirs
        for name in files:
            file = current / name
            try:
                info = file.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    link_count += 1
                    continue
                size = info.st_size
            except OSError as error:
                failures.append(str(error))
                continue
            relative = file.relative_to(base)
            parts = relative.parts
            file_count += 1
            total += size
            first_key = parts[0] if len(parts) > 1 else "[root files]"
            first[first_key] = first.get(first_key, 0) + size
            if len(parts) >= 3:
                second_key = "/".join(parts[:2])
                second[second_key] = second.get(second_key, 0) + size
                for depth in range(3, min(len(parts), 8)):
                    deeper = "/".join(parts[:depth])
                    deep[deeper] = deep.get(deeper, 0) + size
            # Exclude Git internals entirely. Only named, regenerateable
            # cache directories qualify, not files merely ending in .tmp.
            if ".git" not in parts:
                for index, part in enumerate(parts[:-1]):
                    if part in CACHE_NAMES:
                        cache_path = "/".join(parts[: index + 1])
                        caches[cache_path] = caches.get(cache_path, 0) + size
                        break
            item = (size, relative.as_posix())
            if len(largest) < top:
                heapq.heappush(largest, item)
            elif item > largest[0]:
                heapq.heapreplace(largest, item)
    return {
        "root": str(base),
        "total_bytes": total,
        "files": file_count,
        "skipped_links": link_count,
        "errors": failures,
        "first_level_bytes": dict(sorted(first.items(), key=lambda item: -item[1])),
        "second_level_bytes": dict(sorted(second.items(), key=lambda item: -item[1])),
        "deep_folder_bytes": dict(sorted(deep.items(), key=lambda item: -item[1])),
        "largest_files": [
            {"path": path, "bytes": size}
            for size, path in sorted(largest, reverse=True)
        ],
        "cache_bytes": sum(caches.values()),
        "cache_directories": dict(sorted(caches.items(), key=lambda item: -item[1])),
    }


def clean_caches(root: Path | str, *, apply: bool = False) -> dict[str, object]:
    """Preview or remove only recognized local Python cache directories.

    We intentionally never delete raw inputs, normalized Parquet, journals,
    .venv itself, lockboxes, active model bundles or training checkpoints.
    """
    report = scan_storage(root)
    base = Path(report["root"])
    candidates: dict[str, int] = report["cache_directories"]
    result: dict[str, object] = {
        "root": str(base),
        "applied": apply,
        "candidate_bytes": report["cache_bytes"],
        "candidate_directories": candidates,
        "deleted_bytes": 0,
        "deleted_directories": [],
        "errors": list(report["errors"]),
    }
    if not apply:
        return result
    if report["errors"]:
        raise ValueError("scan had errors; refusing to delete any files")
    # Cache paths are derived exclusively from the on-disk scan, never from
    # wildcard globs, and are checked again before each destructive operation.
    for relative, measured in candidates.items():
        candidate = base / relative
        if (not candidate.is_dir() or _is_link(candidate)
                or not candidate.resolve().is_relative_to(base)
                or candidate.name not in CACHE_NAMES):
            result["errors"].append(f"unsafe or changed path skipped: {relative}")
            continue
        try:
            shutil.rmtree(candidate)
        except OSError as error:
            result["errors"].append(f"could not remove {relative}: {error}")
            continue
        result["deleted_bytes"] += measured  # measured apparent bytes, not disk blocks
        result["deleted_directories"].append(relative)
    return result



# This marker is written BEFORE raw trade deletion. Dataset rebuilding must
# fail closed until historical sources have been fully restored and verified.
PRUNED_TRADE_MARKER = Path("storage") / "raw_trade_pruned.json"


def is_historical_trade_history_pruned(bootstrap_root: Path | str) -> bool:
    return (Path(bootstrap_root) / PRUNED_TRADE_MARKER).exists()


def verify_retained_training_dataset(bootstrap_root: Path | str) -> str:
    """Authenticate the retained derived dataset, not its 129 GiB raw inputs.

    This is intentionally O(size of features.parquet), approximately 0.17 GiB
    in the reported installation, rather than hashing all historical trades.
    """
    from .bootstrap.provenance import verify_artifact

    base = Path(bootstrap_root)
    if not (base / "models" / "default.json").is_file():
        raise ValueError("promoted model pointer missing; refusing historical trade pruning")
    data = base / "derived" / "features.parquet"
    manifest = base / "manifests" / "dataset" / "features.parquet.manifest.json"
    provenance = base / "derived" / "features.provenance.json"
    if not all(p.is_file() for p in (data, manifest, provenance)):
        raise ValueError("verified feature dataset or provenance missing; refusing trade pruning")
    if not verify_artifact(data, manifest):
        raise ValueError("feature dataset verification failed; refusing trade pruning")
    try:
        manifest_json = json.loads(manifest.read_text(encoding="utf-8"))
        provenance_json = json.loads(provenance.read_text(encoding="utf-8"))
        digest = str(manifest_json["sha256"]).lower()
        inputs = provenance_json["inputs"]
        if (len(digest) != 64
                or provenance_json["dataset_sha256"].lower() != digest
                or not isinstance(inputs, list) or not inputs):
            raise ValueError("feature dataset provenance verification failed")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValueError("feature dataset provenance verification failed") from exc
    return digest


def _assert_local_directory(base: Path, target: Path) -> None:
    """Reject any symlink/junction in the ancestor chain to this deletion root."""
    try:
        parts = target.relative_to(base).parts
    except ValueError as exc:
        raise ValueError("deletion target is outside the selected project") from exc
    path = base
    for part in parts:
        path /= part
        if _is_link(path):
            raise ValueError(f"link or junction in trade deletion path: {path}")
    if not target.resolve().is_relative_to(base):
        raise ValueError("trade deletion path resolves outside project")


def _write_pruned_marker(path: Path, digest: str) -> None:
    from datetime import datetime, timezone
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "raw-kalshi-trades-pruned-v1",
        "dataset_sha256": digest,
        "pruned_at_utc": datetime.now(timezone.utc).isoformat(),
        "warning": (
            "Historical raw Kalshi trades were intentionally deleted. Do not rebuild "
            "features.parquet from incomplete raw history. Full historical rebuilding "
            "requires restoring verified trades and their manifests first."
        ),
    }
    fd, filename = tempfile.mkstemp(prefix="raw-trade-prune-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(payload, out, sort_keys=True)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(filename, path)
    finally:
        if Path(filename).exists():
            Path(filename).unlink()


def prune_historical_trades(root: Path | str, *, apply: bool = False) -> dict[str, object]:
    """Fast optional reclaim: delete ONLY old Kalshi raw trade JSON + paired manifests.

    No full-disk scan; no read of raw trade bytes. Current model, verified
    feature dataset, market labels, forecast/settlement evidence and raw WS
    streams remain untouched. This permanently removes full source
    reproducibility until the official archives are re-fetched.
    """
    project = _root(root)
    bootstrap = project / "data" / "bootstrap"
    trades = bootstrap / "raw" / "kalshi" / "trades"
    manifests = bootstrap / "manifests" / "kalshi" / "trades"
    marker = bootstrap / PRUNED_TRADE_MARKER
    for location in (trades, manifests, marker):
        _assert_local_directory(project, location)

    digest = verify_retained_training_dataset(bootstrap)
    already_pruned = marker.exists()
    if already_pruned:
        try:
            state = json.loads(marker.read_text(encoding="utf-8"))
            if (state.get("schema") != "raw-kalshi-trades-pruned-v1"
                    or state.get("dataset_sha256") != digest):
                raise ValueError("retained dataset changed since raw trade pruning")
        except (OSError, json.JSONDecodeError, AttributeError) as exc:
            raise ValueError("stored raw-trade prune marker is unreadable") from exc
    elif trades.is_dir() != manifests.is_dir():
        raise ValueError("historical trade manifest directory is missing or incomplete; refusing pruning")
    elif not trades.is_dir():
        raise ValueError("historical trade and manifest directories are absent; nothing to prune")

    report: dict[str, object] = {
        "root": str(project),
        "applied": apply,
        "already_pruned": already_pruned,
        "verified_dataset": True,
        "dataset_sha256": digest,
        "targets": [str(trades), str(manifests)],
        "reclaimed_bytes": 0,
        "risk": (
            "Original historical trade JSON and manifests will be permanently deleted. "
            "Live operation and training from the existing verified dataset are retained; "
            "rebuilding or expanding historical source data requires restoring the deleted "
            "trade records from Kalshi before rebuilding the dataset."
        ),
    }
    if not apply:
        return report

    # Refuse while a supervised local dashboard or authenticated worker is live.
    from .launcher import _dashboard_healthy
    if _dashboard_healthy(8899):
        raise ValueError("stop 'kalshiedge start' before pruning historical trade files")
    state_path = bootstrap / "training_runs" / "current.json"
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raise ValueError("training state unreadable; cannot safely prune")
        pid, identity = state.get("worker_pid"), state.get("worker_identity")
        if type(pid) is int and isinstance(identity, str):
            from .bootstrap.training_process_control import is_alive
            if is_alive(pid, identity):
                raise ValueError("active training worker detected; stop or checkpoint training first")

    before_free = shutil.disk_usage(project).free
    if not already_pruned:
        _write_pruned_marker(marker, digest)
    # The on-disk marker prevents all future unguarded dataset builds, even
    # if deletion is interrupted between directories. Re-run to resume.
    for directory in (trades, manifests):
        if directory.is_dir():
            shutil.rmtree(directory)
    after_free = shutil.disk_usage(project).free
    report["reclaimed_bytes"] = max(0, after_free - before_free)
    return report


def _gib(amount: int) -> str:
    return f"{amount / (1024 ** 3):,.3f} GiB"


def _present(report: dict[str, object], *, mode: str) -> None:
    print(f"KalshiEdge storage — {mode.upper()}")
    print(f"Root: {report['root']}")
    if mode == "scan":
        print(f"Measured: {_gib(report['total_bytes'])} apparent bytes across {report['files']:,} files")
        print("\nLargest top-level folders:")
        for name, size in report["first_level_bytes"].items():
            print(f"  {_gib(size):>14}  {name}")
        print("\nLargest second-level folders:")
        for name, size in list(report["second_level_bytes"].items())[:20]:
            print(f"  {_gib(size):>14}  {name}")
        print("\nLargest deeper folders (up to 7 levels):")
        for name, size in list(report["deep_folder_bytes"].items())[:30]:
            print(f"  {_gib(size):>14}  {name}")
        print("\nLargest files:")
        for entry in report["largest_files"]:
            print(f"  {_gib(entry['bytes']):>14}  {entry['path']}")
        print(f"\nDisposable Python caches: {_gib(report['cache_bytes'])}")
        print("Nothing was deleted. Historical raw data, Parquet, models and training state are protected.")
    elif mode == "reclaim-trades":
        print("Verified retained dataset:", report["dataset_sha256"])
        print("Targets: historical Kalshi raw trades and matching manifests ONLY")
        print("Warning:", report["risk"])
        if report["applied"]:
            print("Historical raw trades pruned; existing features.parquet preserved.")
            print("Newly available disk space:", _gib(report["reclaimed_bytes"]))
        else:
            print("PREVIEW ONLY; nothing deleted. Use 'reclaim-trades --apply' to opt in.")
    else:
        print(f"Candidate disposable caches: {_gib(report['candidate_bytes'])}")
        print(f"Estimated removed apparent bytes: {_gib(report['deleted_bytes'])}")
        if not report["applied"]:
            print("PREVIEW ONLY; nothing was deleted. Use 'clean --apply' after stopping KalshiEdge.")
        else:
            print(f"Cache directories removed: {len(report['deleted_directories'])}")
    if report.get("errors"):
        print(f"Warnings ({len(report['errors'])}):")
        for problem in report["errors"][:10]:
            print(f"  {problem}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit KalshiEdge storage; optionally prune only disposable Python caches")
    parser.add_argument("mode", nargs="?", choices=("scan", "clean", "reclaim-trades"), default="scan")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--top", type=int, default=15, help="number of largest files to report")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    parser.add_argument("--apply", action="store_true", help="permanently execute the selected cleanup")
    args = parser.parse_args(argv)
    if args.apply and args.mode not in ("clean", "reclaim-trades"):
        parser.error("--apply is valid only for clean or reclaim-trades")
    try:
        result = (scan_storage(args.root, top=args.top) if args.mode == "scan"
                  else prune_historical_trades(args.root, apply=args.apply) if args.mode == "reclaim-trades"
                  else clean_caches(args.root, apply=args.apply))
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _present(result, mode=args.mode)
    return 2 if result.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
