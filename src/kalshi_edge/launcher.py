from __future__ import annotations

"""One-terminal supervisor for the read-only KalshiEdge local application."""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import IO

from .bootstrap.config import BootstrapSettings
from .bootstrap.live import load_default_bundle

LOG = logging.getLogger(__name__)
DASHBOARD_PORT = 8899
HEALTH_ENDPOINT = "/api/health"
STARTUP_TIMEOUT_SECONDS = 25


def _dashboard_healthy(port: int) -> bool:
    """Accept only our own dashboard; an unrelated service must not be reused."""
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{port}{HEALTH_ENDPOINT}")
        with urllib.request.urlopen(request, timeout=0.5) as response:
            if response.status != 200:
                return False
            data = json.load(response)
        return data.get("app") == "kalshiedge" and data.get("service") == "unified-dashboard"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError):
        return False


def _tcp_busy(port: int) -> bool:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _tail_log(path: Path, limit: int = 14) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - 12000))
            text = stream.read().decode("utf-8", errors="replace")
        return "\n".join(text.splitlines()[-limit:])
    except OSError:
        return "(log not accessible)"


def _spawn_service(args: list[str], root: Path, log: IO[bytes]) -> subprocess.Popen:
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen(
        [sys.executable, "-m", "kalshi_edge.cli", *args],
        cwd=str(root),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
        start_new_session=os.name != "nt",
    )


def _stop(service: subprocess.Popen | None) -> None:
    if service is None or service.poll() is not None:
        return
    # The dashboard can own a long-running Train Now child process. Stopping
    # only the HTTP server process on Windows leaves that child orphaned.
    # Stop the entire process tree, and never touch unrelated process IDs.
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(service.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False, timeout=15,
            )
            if result.returncode == 0:
                service.wait(timeout=5)
                return
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        import signal
        try:
            os.killpg(service.pid, signal.SIGTERM)
            service.wait(timeout=8)
            return
        except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
            pass
    service.terminate()
    try:
        service.wait(timeout=8)
    except subprocess.TimeoutExpired:
        service.kill()
        service.wait(timeout=5)


class SingleStarter:
    """Best-effort portable, process-held lock against duplicate one-command launches."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.file: IO[bytes] | None = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        self.file.seek(0)
        self.file.write(b"0")
        self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            self.file.close()
            self.file = None
            raise RuntimeError("KalshiEdge is already running through 'kalshiedge start'.") from exc
        return self

    def __exit__(self, *_exc):
        if self.file is not None:
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            self.file.close()
            self.file = None


def start(*, port: int = DASHBOARD_PORT, open_browser: bool = True) -> int:
    settings = BootstrapSettings()
    root = Path.cwd().resolve()
    data_root = settings.bootstrap_dir.resolve()
    log_dir = data_root / "diagnostics"
    log_dir.mkdir(parents=True, exist_ok=True)

    try:
        bundle = load_default_bundle(data_root)
    except Exception as exc:
        print(f"Cannot start KalshiEdge: promoted model verification failed: {exc}", file=sys.stderr)
        return 2

    print("=== KALSHIEDGE LOCAL APPLICATION ===", flush=True)
    print(f"Model: {bundle.model_version}  {bundle.bundle_sha256}", flush=True)
    print("Read-only: no automatic order placement.", flush=True)

    existing_dashboard = _dashboard_healthy(port)
    if not existing_dashboard and _tcp_busy(port):
        print(f"Port {port} is occupied by a different service. Stop it or select --port.", file=sys.stderr)
        return 2

    dashboard_url = f"http://127.0.0.1:{port}/"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    history_log = log_dir / f"app-history-{timestamp}.log"
    predictor_log = log_dir / f"app-predictor-{timestamp}.log"
    history: subprocess.Popen | None = None
    predictor: subprocess.Popen | None = None

    try:
        with SingleStarter(log_dir / "app-start.lock"), ExitStack() as logs:
            if not existing_dashboard:
                history = _spawn_service(
                    ["final-history", "--port", str(port)],
                    root,
                    logs.enter_context(history_log.open("ab")),
                )
                deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
                while time.monotonic() < deadline:
                    if _dashboard_healthy(port):
                        break
                    if history.poll() is not None:
                        break
                    time.sleep(0.25)
                if not _dashboard_healthy(port):
                    print("History dashboard did not start. Log:", history_log, file=sys.stderr)
                    print(_tail_log(history_log), file=sys.stderr)
                    return 2
                print(f"History server PID: {history.pid}", flush=True)
            else:
                print(f"Reusing existing KalshiEdge history server on port {port}.", flush=True)

            predictor = _spawn_service(
                ["predict-live"],
                root,
                logs.enter_context(predictor_log.open("ab")),
            )
            # Give immediate configuration failures a chance to surface before
            # opening the dashboard. Delayed feed discovery is normal.
            time.sleep(0.8)
            if predictor.poll() is not None:
                print(f"Predictor exited with status {predictor.returncode}. Log: {predictor_log}", file=sys.stderr)
                print(_tail_log(predictor_log), file=sys.stderr)
                return 2

            print(f"Live predictor PID: {predictor.pid}", flush=True)
            print("Dashboard:", dashboard_url, flush=True)
            print("Predictor log:", predictor_log, flush=True)
            print("Settlement/history log:", history_log if history is not None else "(existing server)", flush=True)
            print("Keep this terminal open. Press Ctrl+C to stop.", flush=True)

            if open_browser:
                try:
                    opened = webbrowser.open_new_tab(dashboard_url)
                    if not opened:
                        print("Browser did not open automatically. Open the dashboard URL above.", flush=True)
                except (OSError, webbrowser.Error) as exc:
                    print(f"Browser could not open automatically: {exc}", flush=True)

            while True:
                if predictor.poll() is not None:
                    print(f"Live predictor stopped (exit {predictor.returncode}). Log: {predictor_log}", file=sys.stderr)
                    print(_tail_log(predictor_log), file=sys.stderr)
                    return 2
                if history is not None and history.poll() is not None:
                    print(f"History service stopped (exit {history.returncode}). Log: {history_log}", file=sys.stderr)
                    print(_tail_log(history_log), file=sys.stderr)
                    return 2
                if history is None and not _dashboard_healthy(port):
                    print("Previously running history server is no longer available.", file=sys.stderr)
                    return 2
                time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopping KalshiEdge services...", flush=True)
        return 0
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    finally:
        _stop(predictor)
        _stop(history)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kalshiedge", description="KalshiEdge local application launcher")
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("start", help="start predictor, settlement tracking and unified local dashboard")
    command.add_argument("--no-browser", action="store_true", help="do not open the dashboard browser tab")
    command.add_argument("--port", type=int, default=DASHBOARD_PORT, help="dashboard TCP port (default 8899)")
    args = parser.parse_args(argv)

    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    return start(port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    raise SystemExit(main())
