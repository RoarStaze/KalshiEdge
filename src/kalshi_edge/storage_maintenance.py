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
    else:
        print(f"Candidate disposable caches: {_gib(report['candidate_bytes'])}")
        print(f"Estimated removed apparent bytes: {_gib(report['deleted_bytes'])}")
        if not report["applied"]:
            print("PREVIEW ONLY; nothing was deleted. Use 'clean --apply' after stopping KalshiEdge.")
        else:
            print(f"Cache directories removed: {len(report['deleted_directories'])}")
    if report["errors"]:
        print(f"Warnings ({len(report['errors'])}):")
        for problem in report["errors"][:10]:
            print(f"  {problem}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit KalshiEdge storage; optionally prune only disposable Python caches")
    parser.add_argument("mode", nargs="?", choices=("scan", "clean"), default="scan")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--top", type=int, default=15, help="number of largest files to report")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    parser.add_argument("--apply", action="store_true", help="actually remove recognized caches (clean only)")
    args = parser.parse_args(argv)
    if args.apply and args.mode != "clean":
        parser.error("--apply is valid only for clean")
    try:
        result = (scan_storage(args.root, top=args.top) if args.mode == "scan"
                  else clean_caches(args.root, apply=args.apply))
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _present(result, mode=args.mode)
    return 2 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
