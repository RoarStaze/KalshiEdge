from __future__ import annotations

"""Optional desktop control center for the KalshiEdge bootstrap workflow.

Progress is intentionally conservative: the pipeline bar counts verified stage
completions. A stage-specific bar is indeterminate unless the worker emits a
structured KALSHI_EDGE_PROGRESS event. This avoids invented percentages.
"""

import json
import os
import subprocess
import sys
import ctypes
from datetime import datetime, timezone
from pathlib import Path

try:
    from PySide6.QtCore import QProcess, QTimer, Qt
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import (
        QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow,
        QMessageBox, QPushButton, QProgressBar, QPlainTextEdit, QVBoxLayout,
        QWidget, QListWidget, QListWidgetItem
    )
except ImportError as exc:  # pragma: no cover - exercised on GUI launch
    raise SystemExit(
        "The KalshiEdge desktop UI is optional. Install it with: "
        "python -m pip install -e .[gui]"
    ) from exc


APP_TITLE = "KalshiEdge Control Center"
STATE_SCHEMA = 1
STAGES = [
    ("backfill", "Collect historical data", "Download missing official Kalshi and Binance history.",
     ["bootstrap-backfill", "--source", "all"]),
    ("dataset", "Build prediction dataset", "Construct point-in-time features and run the leakage audit.",
     ["bootstrap-build-dataset"]),
    ("verify", "Verify dataset", "Verify the feature file against its SHA-256 manifest.",
     ["__verify_dataset__"]),
    ("train", "Train an experiment", "Train a development-only candidate. This does not promote it.",
     ["bootstrap-train"]),
    ("evaluate", "Evaluate and promotion gate", "Evaluate the untouched lockbox. A failed gate must remain failed.",
     ["bootstrap-evaluate"]),
]
COLORS = {
    "bg": "#02090c", "panel": "#031116", "panel2": "#04191d",
    "line": "#07564f", "text": "#9cefff", "muted": "#53b9c6",
    "blue": "#61d9ff", "green": "#00f5ad", "amber": "#f5cf62",
    "red": "#ff687d", "lime": "#76ff78",
}


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").exists() and (candidate / "src" / "kalshi_edge").is_dir():
            return candidate
    raise FileNotFoundError("Open KalshiEdge Control Center from inside the KalshiEdge repository.")


class ControlCenter(QMainWindow):
    def __init__(self, root: Path):
        super().__init__()
        self.root = root
        self.data_root = root / "data" / "bootstrap"
        self.state_path = self.data_root / "control-center" / "state.json"
        self.state = self._load_state()
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(root))
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)
        self.active_stage: int | None = None
        self.observed_training_pid: int | None = None
        self.observed_training_started: datetime | None = None
        self._last_log_signature: tuple[str, int, int] | None = None
        self.stop_after_stage = False
        self.live_process = QProcess(self)
        self.live_process.setWorkingDirectory(str(root))
        self.live_process.readyReadStandardOutput.connect(lambda: self._append_log(bytes(self.live_process.readAllStandardOutput()).decode("utf-8", "replace")))
        self.live_process.readyReadStandardError.connect(lambda: self._append_log(bytes(self.live_process.readAllStandardError()).decode("utf-8", "replace")))
        self.live_process.finished.connect(self._live_finished)

        # Keep Qt on its ordinary top-level window type; Windows chrome is reinforced
        # after show() so the OS provides a real title bar and resize frame.
        self.setWindowFlags(Qt.WindowType.Window)
        self.setWindowTitle(APP_TITLE)
        self.resize(1480, 940)
        self.setMinimumSize(1120, 760)
        self._build_ui()
        self._observe_external_training()
        self._refresh()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_runtime)
        self.timer.start(1000)

    def _load_state(self) -> dict:
        default = {"schema": STATE_SCHEMA, "completed": [], "statuses": {}, "last_stage": None, "updated_at": None}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if data.get("schema") != STATE_SCHEMA:
                return default
            return {**default, **data}
        except (OSError, json.JSONDecodeError):
            return default

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state["updated_at"] = datetime.now(timezone.utc).isoformat()
        temp = self.state_path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with temp.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(temp, self.state_path)

    def _build_ui(self) -> None:
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background: {COLORS['bg']}; color: {COLORS['text']}; font-family: Consolas, 'Segoe UI'; font-size: 10pt; }}
            QFrame#panel {{ background: {COLORS['panel']}; border: 1px solid {COLORS['line']}; border-radius: 5px; }}
            QLabel#muted {{ color: {COLORS['muted']}; }}
            QLabel#title {{ color: {COLORS['green']}; font-size: 19pt; font-weight: 700; letter-spacing: 1px; }}
            QLabel#section {{ color: {COLORS['green']}; font-size: 11pt; font-weight: 700; }}
            QLabel#hero {{ color: {COLORS['lime']}; font-size: 21pt; font-weight: 700; }}
            QLabel#value {{ color: {COLORS['blue']}; font-size: 12pt; }}
            QPushButton {{ background: {COLORS['panel2']}; border: 1px solid {COLORS['line']}; border-radius: 3px; padding: 8px 12px; color: {COLORS['green']}; }}
            QPushButton:hover {{ border-color: {COLORS['green']}; }}
            QPushButton:disabled {{ color: #38726f; background: #071012; }}
            QPushButton#primary {{ background: #06352e; border: 1px solid {COLORS['green']}; font-weight: 700; }}
            QProgressBar {{ border: 1px solid {COLORS['line']}; background: #031013; height: 13px; text-align: center; color: transparent; }}
            QProgressBar::chunk {{ background: {COLORS['green']}; }}
            QListWidget {{ background: transparent; border: 0; outline: 0; }}
            QListWidget::item {{ background: {COLORS['panel2']}; border: 1px solid {COLORS['line']}; padding: 9px; margin: 3px 0; }}
            QListWidget::item:selected {{ border: 1px solid {COLORS['green']}; background: #062321; }}
            QPlainTextEdit {{ background: #02090c; color: {COLORS['blue']}; border: 0; padding: 8px; font-family: Consolas; font-size: 10pt; selection-background-color: #07564f; }}
        """)
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(22, 14, 22, 12)
        outer.setSpacing(10)

        header = QHBoxLayout()
        brand = QHBoxLayout()
        brand.setSpacing(14)
        mark = QLabel("K")
        mark.setStyleSheet(f"color:{COLORS['green']};font-size:30pt;font-weight:700;")
        brand.addWidget(mark)
        titlebox = QVBoxLayout()
        title = QLabel("KALSHIEDGE  //  TRAINING")
        title.setObjectName("title")
        subtitle = QLabel("BETTER SIGNALS.  SHARPER EDGE.  /  BTC 15-MINUTE RESEARCH")
        subtitle.setObjectName("muted")
        titlebox.addWidget(title)
        titlebox.addWidget(subtitle)
        brand.addLayout(titlebox)
        header.addLayout(brand, 1)
        self.model_badge = QLabel("ENV: LOCAL   |   MODEL: bootstrap-hybrid-v2   |   SCHEMA: v2   |   RULE: KXBTC15M")
        self.model_badge.setStyleSheet(f"border:1px solid {COLORS['line']};padding:9px;color:{COLORS['blue']};")
        header.addWidget(self.model_badge)
        self.health = QLabel("●  IDLE")
        self.health.setStyleSheet(f"color: {COLORS['green']}; padding: 8px 12px; font-weight: 700;")
        header.addWidget(self.health)
        outer.addLayout(header)
        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background:{COLORS['line']};border:0;")
        outer.addWidget(divider)

        top = QGridLayout()
        top.setSpacing(10)
        self.pipeline_panel = self._panel()
        p = QVBoxLayout(self.pipeline_panel)
        p.setContentsMargins(15, 12, 15, 12)
        p.addWidget(self._section_label("›  TRAINING STATUS"))
        hero_row = QHBoxLayout()
        self.hero_status = QLabel("READY")
        self.hero_status.setObjectName("hero")
        hero_row.addWidget(self.hero_status, 1)
        self.overall_label = QLabel("0 / 5 stages")
        self.overall_label.setObjectName("muted")
        hero_row.addWidget(self.overall_label, 0, Qt.AlignmentFlag.AlignRight)
        p.addLayout(hero_row)
        self.overall_bar = QProgressBar()
        self.overall_bar.setRange(0, len(STAGES))
        self.overall_bar.setValue(len(self.state["completed"]))
        self.overall_bar.setTextVisible(False)
        p.addWidget(self.overall_bar)
        self.current_label = QLabel("Ready when you are")
        self.current_label.setWordWrap(True)
        p.addWidget(self.current_label)
        self.model_label = QLabel("Model: bootstrap-hybrid-v2   |   Features: verified dataset   |   Rule: KXBTC15M")
        self.model_label.setObjectName("muted")
        p.addWidget(self.model_label)
        top.addWidget(self.pipeline_panel, 0, 0, 1, 5)

        self.runtime_panel = self._panel()
        r = QVBoxLayout(self.runtime_panel)
        r.setContentsMargins(15, 12, 15, 12)
        r.addWidget(self._section_label("›  SYSTEM MONITOR"))
        self.runtime_label = QLabel("CPU: process monitor idle\nMEMORY: available when reported\nWORKER: no active training process")
        self.runtime_label.setObjectName("value")
        self.runtime_label.setWordWrap(True)
        r.addWidget(self.runtime_label, 1)
        self.stage_bar = QProgressBar()
        self.stage_bar.setRange(0, 0)
        self.stage_bar.setTextVisible(False)
        self.stage_bar.hide()
        r.addWidget(self.stage_bar)
        top.addWidget(self.runtime_panel, 0, 5, 1, 4)

        self.metrics_panel = self._panel()
        m = QVBoxLayout(self.metrics_panel)
        m.setContentsMargins(15, 12, 15, 12)
        m.addWidget(self._section_label("›  LIVE METRICS"))
        self.metric_label = QLabel("Train loss     —\nVal loss        —\nECE (val)       —\nBrier (val)     —\nAUC (val)       —")
        self.metric_label.setObjectName("value")
        m.addWidget(self.metric_label, 1)
        top.addWidget(self.metrics_panel, 0, 9, 1, 3)
        outer.addLayout(top)

        mid = QGridLayout()
        mid.setSpacing(10)
        left = self._panel()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(14, 11, 14, 11)
        log_head = QHBoxLayout()
        log_head.addWidget(self._section_label("›  TRAINING LOG  /  LIVE OUTPUT"), 1)
        clear = QPushButton("Clear view")
        clear.clicked.connect(lambda: self.log.clear())
        log_head.addWidget(clear)
        left_layout.addLayout(log_head)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(800)
        left_layout.addWidget(self.log, 1)
        self.log_footer = QLabel("● LIVE     Output is shown as received; no progress is estimated.")
        self.log_footer.setObjectName("muted")
        left_layout.addWidget(self.log_footer)
        mid.addWidget(left, 0, 0, 3, 8)

        right = self._panel()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(14, 11, 14, 11)
        right_layout.addWidget(self._section_label("›  PIPELINE PROGRESS"))
        self.stage_list = QListWidget()
        self.stage_list.currentRowChanged.connect(self._select_stage)
        right_layout.addWidget(self.stage_list, 1)
        self.detail_title = QLabel("Select a stage")
        self.detail_title.setStyleSheet(f"color:{COLORS['green']};font-weight:700;")
        right_layout.addWidget(self.detail_title)
        self.detail_text = QLabel("Stages run asynchronously. Training output and completion are reported by the worker.")
        self.detail_text.setObjectName("muted")
        self.detail_text.setWordWrap(True)
        right_layout.addWidget(self.detail_text)
        buttons = QGridLayout()
        self.run_button = QPushButton("▶  Run / Resume Pipeline")
        self.run_button.setObjectName("primary")
        self.run_button.clicked.connect(self._run_pipeline)
        buttons.addWidget(self.run_button, 0, 0, 1, 2)
        self.run_selected_button = QPushButton("Run selected stage")
        self.run_selected_button.clicked.connect(self._run_selected)
        buttons.addWidget(self.run_selected_button, 1, 0)
        self.stop_button = QPushButton("Finish stage, then pause")
        self.stop_button.clicked.connect(self._pause_after_stage)
        buttons.addWidget(self.stop_button, 1, 1)
        self.live_button = QPushButton("Start read-only live predictions")
        self.live_button.clicked.connect(self._toggle_live)
        buttons.addWidget(self.live_button, 2, 0, 1, 2)
        right_layout.addLayout(buttons)
        mid.addWidget(right, 0, 8, 3, 4)
        mid.setRowStretch(0, 1)
        outer.addLayout(mid, 1)

        footer = QHBoxLayout()
        footer.addWidget(QLabel("›_  KalshiEdge   /   Research pipeline is read-only; no order controls."), 1)
        self.clock_label = QLabel("")
        self.clock_label.setObjectName("muted")
        footer.addWidget(self.clock_label)
        outer.addLayout(footer)

        self.stage_list.setCurrentRow(self._next_stage_index())

    def _panel(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("panel")
        return frame

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("section")
        return label

    def _append_log(self, text: str) -> None:
        for line in text.splitlines():
            if line.startswith("KALSHI_EDGE_PROGRESS "):
                try:
                    event = json.loads(line.partition(" ")[2])
                    current, total = int(event["completed"]), int(event["total"])
                    if self.active_stage is not None:
                        if event.get("phase") == "finalizing":
                            self.stage_bar.setRange(0, 0)
                            self.runtime_label.setText("Markets processed; validating leakage and writing verified artifacts…")
                        elif total > 0:
                            self.stage_bar.setRange(0, total)
                            self.stage_bar.setValue(max(0, min(current, total)))
                            unit = str(event.get("unit", "items"))
                            item = str(event.get("item", ""))
                            self.runtime_label.setText(f"{current:,} of {total:,} {unit} processed" + (f" · {item}" if item else ""))
                    continue
                except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                    pass
            self.log.appendPlainText(line)

    def _read_stdout(self) -> None:
        self._append_log(bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace"))

    def _read_stderr(self) -> None:
        self._append_log(bytes(self.process.readAllStandardError()).decode("utf-8", "replace"))

    def _process_error(self, _error) -> None:
        self._append_log(f"Process launch error: {self.process.errorString()}")

    def _process_finished(self, exit_code: int, _exit_status) -> None:
        index = self.active_stage
        self.active_stage = None
        self.stage_bar.hide()
        self.stage_bar.setRange(0, 100)
        self.stage_bar.setValue(0)
        if index is None:
            return
        key = STAGES[index][0]
        if exit_code == 0:
            completed = set(self.state.get("completed", []))
            completed.add(key)
            self.state["completed"] = [item[0] for item in STAGES if item[0] in completed]
            self.state["statuses"][key] = "verified command exit; artifacts checked where applicable"
            self.state["last_stage"] = key
            self._append_log(f"Stage completed with exit code 0: {STAGES[index][1]}")
        else:
            self.state["statuses"][key] = f"failed (exit {exit_code})"
            self._append_log(f"Stage failed with exit code {exit_code}; it will not be marked complete.")
            self.stop_after_stage = True
        self._save_state()
        self._refresh()
        if self.stop_after_stage:
            self.stop_after_stage = False
            if exit_code == 0:
                self._append_log("Pipeline paused after the stage completed successfully.")
            else:
                self._append_log("Pipeline halted after the current stage exited with an error.")
        if not self.stop_after_stage and self._pipeline_requested:
            self._start_next_stage()
        else:
            self._pipeline_requested = False
            self._refresh()

    def _live_finished(self, code: int, _status) -> None:
        self.live_button.setText("Start read-only live predictions")
        self._append_log(f"Read-only predictor exited with code {code}.")

    def _refresh(self) -> None:
        selected = self.stage_list.currentRow()
        self.stage_list.blockSignals(True)
        self.stage_list.clear()
        completed = set(self.state.get("completed", []))
        for key, title, description, _cmd in STAGES:
            status = "VERIFIED" if key in completed else self.state.get("statuses", {}).get(key, "NOT STARTED").upper()
            symbol = "✓" if key in completed else ("!" if status.startswith("FAILED") else "○")
            item = QListWidgetItem(f"{symbol}  {title}\n     {status}")
            self.stage_list.addItem(item)
        self.stage_list.setCurrentRow(max(0, min(selected, len(STAGES) - 1)))
        self.stage_list.blockSignals(False)
        self.overall_bar.setValue(len(completed))
        self.overall_label.setText(f"{len(completed)} of {len(STAGES)} stages completed")
        if self.active_stage is not None:
            self.current_label.setText(f"Running: {STAGES[self.active_stage][1]}")
        elif len(completed) == len(STAGES):
            self.current_label.setText("All pipeline commands completed. Check reports and gate decisions before proceeding.")
        else:
            self.current_label.setText(f"Next: {STAGES[self._next_stage_index()][1]}")
        self._select_stage(self.stage_list.currentRow())
        observing = self.observed_training_pid is not None
        self.stop_button.setEnabled(self.active_stage is not None and not observing)
        self.run_button.setEnabled(self.active_stage is None and not observing)
        self.run_selected_button.setEnabled(self.active_stage is None and not observing)
        self.stop_button.setText("Finish stage, then pause")
        if observing:
            self.hero_status.setText("TRAINING ACTIVE")
            self.health.setText("●  TRAINING ACTIVE")
            self.current_label.setText(f"Observing active training worker PID {self.observed_training_pid}. This dashboard will not stop it.")
        elif self.active_stage is not None:
            self.hero_status.setText("TRAINING ACTIVE" if STAGES[self.active_stage][0] == "train" else "PIPELINE ACTIVE")
            self.health.setText("●  TRAINING ACTIVE" if STAGES[self.active_stage][0] == "train" else "●  PIPELINE ACTIVE")
        elif len(completed) == len(STAGES):
            self.hero_status.setText("PIPELINE COMPLETE")
            self.health.setText("●  READY")
        else:
            self.hero_status.setText("READY")
            self.health.setText("●  IDLE")

    def _select_stage(self, row: int) -> None:
        if row < 0 or row >= len(STAGES):
            return
        _key, title, description, _cmd = STAGES[row]
        self.detail_title.setText(title)
        self.detail_text.setText(description + "\n\nResume behavior: completed stages are retained. Dataset construction resumes at the last committed market after validating input hashes. Backfill verifies and skips existing artifacts. Training/evaluation currently restart at the beginning of the interrupted stage.")

    def _next_stage_index(self) -> int:
        completed = set(self.state.get("completed", []))
        for index, stage in enumerate(STAGES):
            if stage[0] not in completed:
                return index
        return len(STAGES) - 1

    def _command(self, index: int) -> tuple[str, list[str]]:
        key, _title, _description, command = STAGES[index]
        if key == "train":
            git_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.root,
                check=True,
                capture_output=True,
                text=True,
            )
            return sys.executable, ["-m", "kalshi_edge.cli", "bootstrap-train", "--git-sha", git_result.stdout.strip()]
        if command[0] == "__verify_dataset__":
            code = (
                "from pathlib import Path; "
                "from kalshi_edge.bootstrap.provenance import verify_artifact; "
                "root=Path('data/bootstrap'); "
                "ok=verify_artifact(root/'derived/features.parquet', "
                "root/'manifests/dataset/features.parquet.manifest.json'); "
                "print({'dataset_verified': ok}); raise SystemExit(0 if ok else 2)"
            )
            return sys.executable, ["-c", code]
        return sys.executable, ["-m", "kalshi_edge.cli", *command]

    def _start_stage(self, index: int) -> None:
        self._observe_external_training()
        if self.observed_training_pid is not None:
            self._append_log(f"A training worker (PID {self.observed_training_pid}) is already active. No second stage was started.")
            self._refresh_runtime()
            return
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return
        if index == 2 and not (self.data_root / "derived" / "features.parquet").exists():
            QMessageBox.warning(self, "Dataset required", "Build the dataset before verifying it.")
            return
        if index >= 3 and "verify" not in set(self.state.get("completed", [])):
            QMessageBox.warning(self, "Verified dataset required", "Run the dataset verification stage successfully before training.")
            return
        if index == 4 and not (self.data_root / "models" / "experiments").exists():
            QMessageBox.warning(self, "Experiment required", "Train an experiment before evaluation.")
            return
        self.active_stage = index
        self.stop_after_stage = False
        self.state["statuses"][STAGES[index][0]] = "running"
        self.state["last_stage"] = STAGES[index][0]
        self._save_state()
        self.stage_bar.setRange(0, 0)
        self.stage_bar.show()
        self.runtime_label.setText("Working… exact item progress appears when supported by the command.")
        program, args = self._command(index)
        self._append_log(f"\n[{datetime.now().strftime('%H:%M:%S')}] START: {STAGES[index][1]}")
        self._append_log("$ " + " ".join([program, *args]))
        self.process.start(program, args)
        self._refresh()

    def _run_pipeline(self) -> None:
        if self.active_stage is not None:
            return
        self._pipeline_requested = True
        self._start_next_stage()

    def _start_next_stage(self) -> None:
        completed = set(self.state.get("completed", []))
        for index, stage in enumerate(STAGES):
            if stage[0] not in completed:
                self._start_stage(index)
                return
        self._pipeline_requested = False
        self._append_log("All configured pipeline stages have completed. Review the evaluation decision manually.")

    def _run_selected(self) -> None:
        row = self.stage_list.currentRow()
        if row < 0:
            return
        self._pipeline_requested = False
        self._start_stage(row)

    def _stop_pipeline(self) -> None:
        # Kept as an internal compatibility hook; the dashboard never interrupts a worker.
        self._pause_after_stage()

    def _pause_after_stage(self) -> None:
        if self.active_stage is None:
            return
        self._pipeline_requested = False
        self.stop_after_stage = True
        self.stop_button.setEnabled(False)
        self.stop_button.setText("Pausing after this stage…")
        self._append_log("Pause queued. The current stage will finish normally; no worker was stopped.")

    def _toggle_live(self) -> None:
        if self.live_process.state() != QProcess.ProcessState.NotRunning:
            self.live_process.terminate()
            if not self.live_process.waitForFinished(3000):
                self.live_process.kill()
            return
        pointer = self.data_root / "models" / "default.json"
        if not pointer.exists():
            QMessageBox.warning(self, "Promotion required", "Read-only live predictions require a promoted default model. Run evaluation and verify the promotion gate first.")
            return
        answer = QMessageBox.question(
            self, "Start read-only predictions?",
            "Start the read-only predictor? This does not place orders. The Phase 1 scheduled collector will not be touched.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.live_process.start(sys.executable, ["-m", "kalshi_edge.cli", "predict-live"])
        self.live_button.setText("■  Stop read-only predictions")
        self._append_log("Starting read-only live inference. No trading actions are available in this GUI.")

    def _refresh_runtime(self) -> None:
        self.clock_label.setText(datetime.now().astimezone().strftime("%Y-%m-%d  %H:%M:%S %Z"))
        self._observe_external_training()
        if self.active_stage is None:
            if not self.stage_bar.isHidden():
                self.stage_bar.hide()
        else:
            stage_key, stage_title = STAGES[self.active_stage][0], STAGES[self.active_stage][1]
            self.hero_status.setText("TRAINING ACTIVE" if stage_key == "train" else "PIPELINE ACTIVE")
            self.health.setText("●  TRAINING ACTIVE" if stage_key == "train" else "●  PIPELINE ACTIVE")
            self.runtime_label.setText(f"CPU: not sampled\nMEMORY: not sampled\nWORKER: {stage_title}")
        if self.active_stage is None and self.observed_training_pid is None:
            self.runtime_label.setText("CPU: not sampled\nMEMORY: not sampled\nWORKER: idle — ready for next stage")
            self.hero_status.setText("READY")
            self.health.setText("●  IDLE")
        if self.observed_training_pid is not None:
            elapsed = ""
            if self.observed_training_started:
                seconds = max(0, int((datetime.now().astimezone() - self.observed_training_started).total_seconds()))
                elapsed = f"\nELAPSED: {seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
            self.runtime_label.setText(f"CPU: not sampled\nMEMORY: not sampled\nWORKER PID: {self.observed_training_pid}{elapsed}")
            self.hero_status.setText("TRAINING ACTIVE")
            self.health.setText("●  TRAINING ACTIVE")
            self.current_label.setText("Training is active in an existing worker. The dashboard is observing it; controls will not stop it.")
            self.run_button.setEnabled(False)
            self.run_selected_button.setEnabled(False)
            self.stop_button.setEnabled(False)
            self._tail_observed_training_log()
        elif self.live_process.state() != QProcess.ProcessState.NotRunning and self.active_stage is None:
            self.runtime_label.setText("CPU: not sampled\nMEMORY: not sampled\nWORKER: read-only predictor active")

    @staticmethod
    def _pid_is_running(pid: int) -> bool:
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
            kernel32.GetExitCodeProcess.restype = ctypes.c_int
            kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
            kernel32.CloseHandle.restype = ctypes.c_int
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)) and exit_code.value == 259)
            finally:
                kernel32.CloseHandle(handle)
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _observe_external_training(self) -> None:
        if self.active_stage is not None or self.process.state() != QProcess.ProcessState.NotRunning:
            self.observed_training_pid = None
            return
        logs = self.data_root / "logs"
        candidates = sorted(logs.glob("training-v2-*.pid"), key=lambda item: item.stat().st_mtime, reverse=True)
        observed = None
        for pid_file in candidates:
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                continue
            if self._pid_is_running(pid):
                observed = (pid, datetime.fromtimestamp(pid_file.stat().st_mtime).astimezone())
                break
        self.observed_training_pid = observed[0] if observed else None
        self.observed_training_started = observed[1] if observed else None

    def _tail_observed_training_log(self) -> None:
        if self.observed_training_pid is None:
            return
        logs = self.data_root / "logs"
        for pid_file in sorted(logs.glob("training-v2-*.pid"), key=lambda item: item.stat().st_mtime, reverse=True):
            try:
                if int(pid_file.read_text(encoding="utf-8").strip()) != self.observed_training_pid:
                    continue
            except (OSError, ValueError):
                continue
            prefix = pid_file.with_suffix("")
            found_output = False
            for suffix in (".stdout.log", ".stderr.log"):
                path = Path(str(prefix) + suffix)
                try:
                    stat = path.stat()
                    if stat.st_size == 0:
                        continue
                    found_output = True
                    signature = (str(path), stat.st_mtime_ns, stat.st_size)
                    if signature == self._last_log_signature:
                        continue
                    with path.open("rb") as stream:
                        stream.seek(max(0, stat.st_size - 65536))
                        content = stream.read().decode("utf-8", "replace")
                    self._last_log_signature = signature
                    if content.strip():
                        self.log.setPlainText(content[-30000:])
                        self.log.moveCursor(self.log.textCursor().MoveOperation.End)
                    return
                except OSError:
                    continue
            if not found_output:
                self.log.setPlainText("Training process is active. Its current log files have not emitted output yet.\n\nThe worker remains untouched; this panel refreshes when new log output is written.")
            break

    def closeEvent(self, event) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.information(self, "Stage still running", "Keep the dashboard open until this stage finishes. Closing the window could interrupt the worker.")
            event.ignore()
            return
        if self.live_process.state() != QProcess.ProcessState.NotRunning:
            answer = QMessageBox.question(
                self, "Processes are running",
                "Stop the read-only predictor and close the dashboard?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.live_process.terminate()
        event.accept()


def main() -> int:
    try:
        root = find_project_root()
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    window = ControlCenter(root)
    # Let Qt's Windows platform plugin create and manage the non-client frame.
    # CustomizeWindowHint makes every requested caption control explicit.
    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
    window.setWindowFlags(
        Qt.WindowType.Window
        | Qt.WindowType.CustomizeWindowHint
        | Qt.WindowType.WindowTitleHint
        | Qt.WindowType.WindowSystemMenuHint
        | Qt.WindowType.WindowMinimizeButtonHint
        | Qt.WindowType.WindowMaximizeButtonHint
        | Qt.WindowType.WindowCloseButtonHint
    )
    window.show()
    window.raise_()
    window.activateWindow()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
