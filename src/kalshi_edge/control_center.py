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
    "bg": "#0b1220", "panel": "#121d2e", "panel2": "#17263a",
    "line": "#263851", "text": "#f0f5fb", "muted": "#a5b4c8",
    "blue": "#65a9ff", "green": "#55d6a3", "amber": "#f3c969",
    "red": "#ff7e8a",
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
        self.stop_after_stage = False
        self.user_stopping = False
        self.live_process = QProcess(self)
        self.live_process.setWorkingDirectory(str(root))
        self.live_process.readyReadStandardOutput.connect(lambda: self._append_log(bytes(self.live_process.readAllStandardOutput()).decode("utf-8", "replace")))
        self.live_process.readyReadStandardError.connect(lambda: self._append_log(bytes(self.live_process.readAllStandardError()).decode("utf-8", "replace")))
        self.live_process.finished.connect(self._live_finished)

        self.setWindowTitle(APP_TITLE)
        self.resize(1240, 820)
        self.setMinimumSize(980, 680)
        self._build_ui()
        self._refresh()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh_runtime)
        self.timer.start(1500)

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
            QMainWindow, QWidget {{ background: {COLORS['bg']}; color: {COLORS['text']}; font-family: 'Segoe UI'; font-size: 10pt; }}
            QFrame#panel {{ background: {COLORS['panel']}; border: 1px solid {COLORS['line']}; border-radius: 18px; }}
            QLabel#muted {{ color: {COLORS['muted']}; }}
            QLabel#title {{ font-size: 25pt; font-weight: 700; }}
            QLabel#section {{ font-size: 13pt; font-weight: 650; }}
            QPushButton {{ background: {COLORS['panel2']}; border: 1px solid {COLORS['line']}; border-radius: 20px; padding: 13px 19px; font-weight: 650; }}
            QPushButton:hover {{ border-color: {COLORS['blue']}; }}
            QPushButton:disabled {{ color: #66758a; background: #111927; }}
            QPushButton#primary {{ background: #1d5791; border: 1px solid #347fc3; }}
            QPushButton#stop {{ background: #552832; border: 1px solid #87424e; }}
            QProgressBar {{ border: 0; border-radius: 7px; background: #26354a; height: 14px; text-align: center; color: transparent; }}
            QProgressBar::chunk {{ border-radius: 7px; background: {COLORS['green']}; }}
            QListWidget {{ background: transparent; border: 0; outline: 0; }}
            QListWidget::item {{ background: {COLORS['panel2']}; border: 1px solid {COLORS['line']}; border-radius: 13px; padding: 12px; margin: 4px 0; }}
            QListWidget::item:selected {{ border: 1px solid {COLORS['blue']}; background: #1b3049; }}
            QPlainTextEdit {{ background: #080e18; color: #cbd8e8; border: 1px solid {COLORS['line']}; border-radius: 12px; padding: 10px; font-family: Consolas; }}
        """)
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(24, 22, 24, 20)
        outer.setSpacing(16)

        header = QHBoxLayout()
        brand = QVBoxLayout()
        title = QLabel("KalshiEdge")
        title.setObjectName("title")
        subtitle = QLabel("CONTROL CENTER  /  BTC 15-MINUTE PREDICTION PIPELINE")
        subtitle.setObjectName("muted")
        brand.addWidget(title)
        brand.addWidget(subtitle)
        header.addLayout(brand, 1)
        self.health = QLabel("LOCAL WORKSPACE")
        self.health.setStyleSheet(f"color: {COLORS['green']}; background: #143b35; border-radius: 13px; padding: 9px 13px; font-weight: 700;")
        header.addWidget(self.health, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(header)

        top = QGridLayout()
        top.setSpacing(14)
        self.pipeline_panel = self._panel()
        p = QVBoxLayout(self.pipeline_panel)
        p.setContentsMargins(18, 16, 18, 16)
        p.addWidget(self._section_label("Overall pipeline"))
        self.overall_label = QLabel("0 of 5 stages verified")
        self.overall_label.setObjectName("muted")
        p.addWidget(self.overall_label)
        self.overall_bar = QProgressBar()
        self.overall_bar.setRange(0, len(STAGES))
        self.overall_bar.setValue(len(self.state["completed"]))
        self.overall_bar.setTextVisible(False)
        p.addWidget(self.overall_bar)
        self.current_label = QLabel("Ready when you are")
        self.current_label.setWordWrap(True)
        p.addWidget(self.current_label)
        top.addWidget(self.pipeline_panel, 0, 0)

        self.runtime_panel = self._panel()
        r = QVBoxLayout(self.runtime_panel)
        r.setContentsMargins(18, 16, 18, 16)
        r.addWidget(self._section_label("Machine activity"))
        self.runtime_label = QLabel("Waiting for a task")
        self.runtime_label.setObjectName("muted")
        r.addWidget(self.runtime_label)
        self.stage_bar = QProgressBar()
        self.stage_bar.setRange(0, 0)
        self.stage_bar.setTextVisible(False)
        self.stage_bar.hide()
        r.addWidget(self.stage_bar)
        self.resume_note = QLabel("Progress is never guessed. Stages without measurable progress show activity, not a fake percentage.")
        self.resume_note.setObjectName("muted")
        self.resume_note.setWordWrap(True)
        r.addWidget(self.resume_note)
        top.addWidget(self.runtime_panel, 0, 1)
        outer.addLayout(top)

        mid = QHBoxLayout()
        mid.setSpacing(14)
        left = self._panel()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(16, 14, 16, 14)
        left_layout.addWidget(self._section_label("Workflow stages"))
        self.stage_list = QListWidget()
        self.stage_list.currentRowChanged.connect(self._select_stage)
        left_layout.addWidget(self.stage_list, 1)
        mid.addWidget(left, 5)

        right = self._panel()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(18, 16, 18, 16)
        right_layout.addWidget(self._section_label("Selected stage"))
        self.detail_title = QLabel("Select a stage")
        self.detail_title.setStyleSheet("font-size: 17pt; font-weight: 700;")
        right_layout.addWidget(self.detail_title)
        self.detail_text = QLabel("Each stage has a clear purpose and must pass its own checks.")
        self.detail_text.setObjectName("muted")
        self.detail_text.setWordWrap(True)
        right_layout.addWidget(self.detail_text)
        right_layout.addStretch(1)
        buttons = QGridLayout()
        self.run_button = QPushButton("▶  Run / Resume Pipeline")
        self.run_button.setObjectName("primary")
        self.run_button.clicked.connect(self._run_pipeline)
        buttons.addWidget(self.run_button, 0, 0, 1, 2)
        self.run_selected_button = QPushButton("Run selected stage")
        self.run_selected_button.clicked.connect(self._run_selected)
        buttons.addWidget(self.run_selected_button, 1, 0)
        self.stop_button = QPushButton("■  Stop safely")
        self.stop_button.setObjectName("stop")
        self.stop_button.clicked.connect(self._stop_pipeline)
        buttons.addWidget(self.stop_button, 1, 1)
        self.live_button = QPushButton("Start read-only live predictions")
        self.live_button.clicked.connect(self._toggle_live)
        buttons.addWidget(self.live_button, 2, 0, 1, 2)
        right_layout.addLayout(buttons)
        mid.addWidget(right, 6)
        outer.addLayout(mid, 3)

        bottom = self._panel()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(16, 12, 16, 12)
        log_head = QHBoxLayout()
        log_head.addWidget(self._section_label("Activity log"), 1)
        clear = QPushButton("Clear view")
        clear.clicked.connect(lambda: self.log.clear())
        log_head.addWidget(clear)
        bl.addLayout(log_head)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1200)
        self.log.setMinimumHeight(145)
        bl.addWidget(self.log)
        outer.addWidget(bottom, 2)

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
        if self.user_stopping:
            self.state["statuses"][key] = "interrupted; resume restarts this stage"
            self.user_stopping = False
            self._append_log("Stage interrupted. Resume will restart this stage from its beginning.")
        elif self.stop_after_stage:
            self.state["statuses"][key] = "stopped after stage"
            self.stop_after_stage = False
            self._append_log("Pipeline stopped at a safe stage boundary.")
        elif exit_code == 0:
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
        self.stop_button.setEnabled(self.active_stage is not None)
        self.run_button.setEnabled(self.active_stage is None)
        self.run_selected_button.setEnabled(self.active_stage is None)

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
        if self.active_stage is None:
            return
        answer = QMessageBox.question(
            self, "Stop safely?",
            "Stop the current stage now? The last verified completed stages are saved. "
            "The interrupted stage may need to restart from its beginning; exact mid-stage resume is not yet supported by every existing worker.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._pipeline_requested = False
        self.stop_after_stage = False
        self.user_stopping = True
        self.process.terminate()
        if not self.process.waitForFinished(4000):
            self.process.kill()
        if self.active_stage is not None:
            key = STAGES[self.active_stage][0]
            self.state["statuses"][key] = "interrupted; resume restarts this stage"
            self._save_state()
        self._append_log("Stop requested. Verify the stage outputs before resuming.")

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
        if self.active_stage is None:
            self.runtime_label.setText("Idle — ready to resume from saved stage state")
            if not self.stage_bar.isHidden():
                self.stage_bar.hide()
        if self.live_process.state() != QProcess.ProcessState.NotRunning and self.active_stage is None:
            self.runtime_label.setText("Read-only live predictor running")

    def closeEvent(self, event) -> None:
        if self.process.state() != QProcess.ProcessState.NotRunning or self.live_process.state() != QProcess.ProcessState.NotRunning:
            answer = QMessageBox.question(
                self, "Processes are running",
                "Closing the window will stop processes launched by this GUI. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            for process in (self.process, self.live_process):
                if process.state() != QProcess.ProcessState.NotRunning:
                    process.terminate()
                    if not process.waitForFinished(3000):
                        process.kill()
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
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
