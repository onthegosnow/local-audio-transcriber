"""PySide6 desktop GUI for the Local Audio Transcriber."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt, QSettings, QThread, Signal
from PySide6.QtGui import QAction, QFont, QGuiApplication, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from transcriber import engine, llm, ollama_setup, __version__

# Where updates are published. `update.sh` pulls new releases from here too.
GITHUB_REPO = "onthegosnow/local-audio-transcriber"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"


def _version_tuple(v: str) -> tuple:
    """Loose semver -> comparable tuple; non-numeric parts count as 0."""
    out = []
    for part in v.strip().lstrip("v").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)

AUDIO_FILTER = (
    "Audio/Video (*.mp3 *.wav *.m4a *.flac *.ogg *.opus *.aac *.wma "
    "*.mp4 *.mkv *.mov *.webm *.avi);;All files (*)"
)

# Common languages; "Auto-detect" maps to None.
LANGUAGES = [
    ("Auto-detect", None),
    ("English", "en"),
    ("Spanish", "es"),
    ("French", "fr"),
    ("German", "de"),
    ("Italian", "it"),
    ("Portuguese", "pt"),
    ("Dutch", "nl"),
    ("Russian", "ru"),
    ("Chinese", "zh"),
    ("Japanese", "ja"),
    ("Korean", "ko"),
    ("Arabic", "ar"),
    ("Hindi", "hi"),
]


# --------------------------------------------------------------------------- #
# Worker threads                                                              #
# --------------------------------------------------------------------------- #
class TranscribeThread(QThread):
    status = Signal(str)
    progress = Signal(int)
    segment = Signal(object)          # engine.Segment
    done = Signal(object)             # engine.TranscriptResult
    error = Signal(str)

    def __init__(self, params: "engine.TranscribeParams"):
        super().__init__()
        self.params = params
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            result = engine.transcribe(
                self.params,
                on_status=self.status.emit,
                on_progress=self.progress.emit,
                on_segment=self.segment.emit,
                is_cancelled=lambda: self._cancel,
            )
            self.done.emit(result)
        except engine.Cancelled:
            self.error.emit("__cancelled__")
        except Exception as e:  # surface any failure to the UI
            self.error.emit(str(e))


class LLMThread(QThread):
    token = Signal(str)
    done = Signal(str)
    error = Signal(str)

    def __init__(self, text: str, model: str, instruction: str):
        super().__init__()
        self.text = text
        self.model = model
        self.instruction = instruction
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            out = llm.process(
                self.text,
                self.model,
                self.instruction,
                on_token=self.token.emit,
                is_cancelled=lambda: self._cancel,
            )
            self.done.emit(out)
        except Exception as e:
            self.error.emit(str(e))


class OllamaSetupThread(QThread):
    """Installs/starts Ollama for the AI-summary feature (no root)."""

    status = Signal(str)
    progress = Signal(int)
    done = Signal(bool, str)  # (ok, error_message)

    def __init__(self, full: bool, model: str = "llama3.2"):
        super().__init__()
        self.full = full  # full setup vs. just start an already-installed server
        self.model = model

    def run(self):
        try:
            if self.full:
                ollama_setup.setup(
                    self.model,
                    on_status=self.status.emit,
                    on_progress=self.progress.emit,
                )
            else:
                ollama_setup.start_server(on_status=self.status.emit)
            self.done.emit(True, "")
        except Exception as e:
            self.done.emit(False, str(e))


class UpdateCheckThread(QThread):
    # (update_available, latest_version, error_message)
    result = Signal(bool, str, str)

    def run(self):
        try:
            import requests

            r = requests.get(
                LATEST_RELEASE_API,
                timeout=8,
                headers={"Accept": "application/vnd.github+json"},
            )
            if r.status_code == 404:
                self.result.emit(False, "", "No releases have been published yet.")
                return
            r.raise_for_status()
            tag = (r.json().get("tag_name") or "").strip()
            if not tag:
                self.result.emit(False, "", "Could not read the latest version.")
                return
            available = _version_tuple(tag) > _version_tuple(__version__)
            self.result.emit(available, tag.lstrip("v"), "")
        except Exception as e:
            self.result.emit(False, "", str(e))


# --------------------------------------------------------------------------- #
# Main window                                                                 #
# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Local Audio Transcriber")
        self.resize(1040, 720)
        self.setAcceptDrops(True)

        self.audio_path: str | None = None
        self.transcribe_thread: TranscribeThread | None = None
        self.llm_thread: LLMThread | None = None

        self._build_ui()
        self._load_settings()
        self._refresh_ollama_models()
        self._maybe_autostart_ollama()

    # ---- UI construction -------------------------------------------------- #
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        # --- File row / drop zone ---
        self.drop_label = QLabel("Drag an audio file here, or click Browse")
        self.drop_label.setAlignment(Qt.AlignCenter)
        self.drop_label.setObjectName("dropzone")
        self.drop_label.setMinimumHeight(70)
        self.drop_label.setStyleSheet(
            "#dropzone { border: 2px dashed #888; border-radius: 10px; color: #888; }"
        )
        root.addWidget(self.drop_label)

        file_row = QHBoxLayout()
        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.clicked.connect(self.on_browse)
        self.file_line = QLineEdit()
        self.file_line.setPlaceholderText("No file selected")
        self.file_line.setReadOnly(True)
        file_row.addWidget(self.browse_btn)
        file_row.addWidget(self.file_line, 1)
        root.addLayout(file_row)

        # --- Options grid ---
        opts = QGridLayout()
        opts.setHorizontalSpacing(12)
        opts.setVerticalSpacing(8)

        opts.addWidget(QLabel("Model:"), 0, 0)
        self.model_combo = QComboBox()
        for m in engine.MODEL_SIZES:
            self.model_combo.addItem(f"{m}  —  {engine.MODEL_NOTES.get(m, '')}", m)
        self.model_combo.setCurrentIndex(engine.MODEL_SIZES.index("small"))
        opts.addWidget(self.model_combo, 0, 1)

        opts.addWidget(QLabel("Device:"), 0, 2)
        self.device_combo = QComboBox()
        self.device_combo.addItem("CPU", "cpu")
        self.device_combo.addItem("NVIDIA GPU (CUDA)", "cuda")
        opts.addWidget(self.device_combo, 0, 3)

        opts.addWidget(QLabel("Language:"), 1, 0)
        self.lang_combo = QComboBox()
        for name, code in LANGUAGES:
            self.lang_combo.addItem(name, code)
        opts.addWidget(self.lang_combo, 1, 1)

        self.ts_check = QCheckBox("Include timestamps")
        opts.addWidget(self.ts_check, 1, 2)
        self.vad_check = QCheckBox("Skip silences (VAD)")
        self.vad_check.setChecked(True)
        opts.addWidget(self.vad_check, 1, 3)

        root.addLayout(opts)

        # --- Transcribe controls ---
        ctl_row = QHBoxLayout()
        self.transcribe_btn = QPushButton("Transcribe")
        self.transcribe_btn.setMinimumHeight(36)
        self.transcribe_btn.clicked.connect(self.on_transcribe)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.on_cancel_transcribe)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        ctl_row.addWidget(self.transcribe_btn)
        ctl_row.addWidget(self.cancel_btn)
        ctl_row.addWidget(self.progress, 1)
        root.addLayout(ctl_row)

        # --- Split: transcript | LLM panel ---
        splitter = QSplitter(Qt.Horizontal)

        # Left: transcript
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.addWidget(QLabel("<b>Transcript</b>"))
        header.addStretch(1)
        self.copy_btn = QPushButton("Copy")
        self.copy_btn.clicked.connect(lambda: self._copy(self.transcript_edit))
        self.save_btn = QPushButton("Save…")
        self.save_btn.clicked.connect(lambda: self._save(self.transcript_edit, "transcript"))
        header.addWidget(self.copy_btn)
        header.addWidget(self.save_btn)
        lv.addLayout(header)
        self.transcript_edit = QTextEdit()
        self.transcript_edit.setPlaceholderText("Transcript will appear here…")
        self.transcript_edit.setFont(QFont("monospace"))
        lv.addWidget(self.transcript_edit, 1)
        splitter.addWidget(left)

        # Right: LLM post-processing
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(QLabel("<b>Local LLM (Ollama)</b>"))

        llm_row = QGridLayout()
        llm_row.addWidget(QLabel("Task:"), 0, 0)
        self.task_combo = QComboBox()
        for name in llm.TASKS:
            self.task_combo.addItem(name)
        self.task_combo.addItem("Custom…")
        self.task_combo.currentTextChanged.connect(self._on_task_changed)
        llm_row.addWidget(self.task_combo, 0, 1)

        llm_row.addWidget(QLabel("Model:"), 1, 0)
        self.ollama_combo = QComboBox()
        self.refresh_models_btn = QPushButton("↻")
        self.refresh_models_btn.setFixedWidth(32)
        self.refresh_models_btn.setToolTip("Refresh installed Ollama models")
        self.refresh_models_btn.clicked.connect(self._refresh_ollama_models)
        model_row = QHBoxLayout()
        model_row.addWidget(self.ollama_combo, 1)
        model_row.addWidget(self.refresh_models_btn)
        llm_row.addLayout(model_row, 1, 1)
        rv.addLayout(llm_row)

        # Shown only when Ollama isn't set up yet — one-click, no-root install.
        self.setup_btn = QPushButton("Enable AI summaries…")
        self.setup_btn.setToolTip(
            "Download and set up local AI (Ollama) — runs on your computer, no cloud."
        )
        self.setup_btn.clicked.connect(self.on_setup_ai)
        self.setup_btn.setVisible(False)
        rv.addWidget(self.setup_btn)

        self.setup_progress = QProgressBar()
        self.setup_progress.setVisible(False)
        rv.addWidget(self.setup_progress)

        self.custom_prompt = QLineEdit()
        self.custom_prompt.setPlaceholderText("Custom instruction (used when Task = Custom…)")
        self.custom_prompt.setEnabled(False)
        rv.addWidget(self.custom_prompt)

        proc_row = QHBoxLayout()
        self.process_btn = QPushButton("Process with LLM")
        self.process_btn.clicked.connect(self.on_process)
        self.llm_cancel_btn = QPushButton("Cancel")
        self.llm_cancel_btn.setEnabled(False)
        self.llm_cancel_btn.clicked.connect(self.on_cancel_llm)
        self.llm_copy_btn = QPushButton("Copy")
        self.llm_copy_btn.clicked.connect(lambda: self._copy(self.llm_edit))
        self.llm_save_btn = QPushButton("Save…")
        self.llm_save_btn.clicked.connect(lambda: self._save(self.llm_edit, "llm-output"))
        proc_row.addWidget(self.process_btn)
        proc_row.addWidget(self.llm_cancel_btn)
        proc_row.addStretch(1)
        proc_row.addWidget(self.llm_copy_btn)
        proc_row.addWidget(self.llm_save_btn)
        rv.addLayout(proc_row)

        self.llm_edit = QTextEdit()
        self.llm_edit.setPlaceholderText("LLM output will appear here…")
        rv.addWidget(self.llm_edit, 1)
        splitter.addWidget(right)

        splitter.setSizes([560, 440])
        root.addWidget(splitter, 1)

        # Status bar
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready.")

        # Menu
        self._build_menu()

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("&File")
        open_act = QAction("&Open audio…", self)
        open_act.triggered.connect(self.on_browse)
        quit_act = QAction("&Quit", self)
        quit_act.triggered.connect(self.close)
        file_menu.addAction(open_act)
        file_menu.addSeparator()
        file_menu.addAction(quit_act)

        help_menu = self.menuBar().addMenu("&Help")
        upd_act = QAction("Check for &updates…", self)
        upd_act.triggered.connect(self._check_updates)
        help_menu.addAction(upd_act)
        about_act = QAction("&About", self)
        about_act.triggered.connect(self._about)
        help_menu.addAction(about_act)

    # ---- Drag & drop ------------------------------------------------------ #
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            self._set_audio(urls[0].toLocalFile())

    # ---- File selection --------------------------------------------------- #
    def on_browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select audio file", "", AUDIO_FILTER)
        if path:
            self._set_audio(path)

    def _set_audio(self, path: str):
        if not path or not os.path.isfile(path):
            return
        self.audio_path = path
        self.file_line.setText(path)
        self.drop_label.setText(os.path.basename(path))
        self.statusBar().showMessage(f"Loaded: {os.path.basename(path)}")

    # ---- Transcription ---------------------------------------------------- #
    def on_transcribe(self):
        if not self.audio_path:
            QMessageBox.warning(self, "No file", "Please choose an audio file first.")
            return

        params = engine.TranscribeParams(
            audio_path=self.audio_path,
            model_size=self.model_combo.currentData(),
            device=self.device_combo.currentData(),
            language=self.lang_combo.currentData(),
            vad_filter=self.vad_check.isChecked(),
            include_timestamps=self.ts_check.isChecked(),
        )

        self.transcript_edit.clear()
        self.progress.setValue(0)
        self._set_transcribing(True)

        self.transcribe_thread = TranscribeThread(params)
        self.transcribe_thread.status.connect(self.statusBar().showMessage)
        self.transcribe_thread.progress.connect(self.progress.setValue)
        self.transcribe_thread.segment.connect(self._append_segment)
        self.transcribe_thread.done.connect(self._transcribe_done)
        self.transcribe_thread.error.connect(self._transcribe_error)
        self.transcribe_thread.start()

    def on_cancel_transcribe(self):
        if self.transcribe_thread and self.transcribe_thread.isRunning():
            self.statusBar().showMessage("Cancelling…")
            self.transcribe_thread.cancel()

    def _append_segment(self, seg):
        if self.ts_check.isChecked():
            self.transcript_edit.append(
                f"[{engine._format_timestamp(seg.start)} -> "
                f"{engine._format_timestamp(seg.end)}] {seg.text}"
            )
        else:
            cursor_text = self.transcript_edit.toPlainText()
            sep = " " if cursor_text and not cursor_text.endswith(" ") else ""
            self.transcript_edit.moveCursor(QTextCursor.End)
            self.transcript_edit.insertPlainText(sep + seg.text)

    def _transcribe_done(self, result):
        self._set_transcribing(False)
        # Normalize final text (handles spacing/timestamps consistently).
        self.transcript_edit.setPlainText(result.text)
        msg = f"Done — {len(result.segments)} segments"
        if result.language:
            msg += f", language: {result.language}"
        if result.duration:
            msg += f", {result.duration:.0f}s audio"
        self.statusBar().showMessage(msg)

    def _transcribe_error(self, message: str):
        self._set_transcribing(False)
        if message == "__cancelled__":
            self.statusBar().showMessage("Cancelled.")
            return
        self.statusBar().showMessage("Transcription failed.")
        QMessageBox.critical(self, "Transcription error", _friendly_error(message))

    def _set_transcribing(self, running: bool):
        self.transcribe_btn.setEnabled(not running)
        self.cancel_btn.setEnabled(running)
        self.browse_btn.setEnabled(not running)
        self.model_combo.setEnabled(not running)
        self.device_combo.setEnabled(not running)

    # ---- LLM post-processing --------------------------------------------- #
    def _refresh_ollama_models(self):
        self.ollama_combo.clear()
        if llm.is_available():
            try:
                models = llm.list_models()
            except Exception:
                models = []
            if models:
                self.setup_btn.setVisible(False)
                self.ollama_combo.setEnabled(True)
                self.process_btn.setEnabled(True)
                for m in models:
                    self.ollama_combo.addItem(m)
            else:
                # Ollama running but no model installed — offer to pull one.
                self.ollama_combo.addItem("No AI model installed")
                self.ollama_combo.setEnabled(False)
                self.process_btn.setEnabled(False)
                if ollama_setup.supported():
                    self.setup_btn.setText("Download AI model…")
                    self.setup_btn.setVisible(True)
                    self.setup_btn.setEnabled(True)
            return
        # Ollama not reachable.
        self.ollama_combo.setEnabled(False)
        self.process_btn.setEnabled(False)
        if ollama_setup.supported():
            self.ollama_combo.addItem("AI summaries not set up")
            self.setup_btn.setText("Enable AI summaries…")
            self.setup_btn.setVisible(True)
            self.setup_btn.setEnabled(True)
        else:
            self.ollama_combo.addItem("Ollama not running")
            self.setup_btn.setVisible(False)

    def on_setup_ai(self):
        resp = QMessageBox.question(
            self,
            "Enable AI summaries",
            "Set up local AI summaries?\n\n"
            "This downloads Ollama and an AI model (a few GB) into your home "
            "folder and runs entirely on your computer — no account, no cloud, "
            "and no password needed.\n\nContinue?",
        )
        if resp != QMessageBox.Yes:
            return
        self.setup_btn.setEnabled(False)
        self.setup_progress.setValue(0)
        self.setup_progress.setVisible(True)
        self.statusBar().showMessage("Setting up AI summaries…")
        self._setup_thread = OllamaSetupThread(full=True)
        self._setup_thread.status.connect(self.statusBar().showMessage)
        self._setup_thread.progress.connect(self.setup_progress.setValue)
        self._setup_thread.done.connect(self._on_setup_done)
        self._setup_thread.start()

    def _on_setup_done(self, ok: bool, err: str):
        self.setup_progress.setVisible(False)
        if ok:
            self.statusBar().showMessage("AI summaries are ready.")
            self._refresh_ollama_models()
            QMessageBox.information(
                self, "AI summaries", "All set — AI summaries are ready to use."
            )
        else:
            self.setup_btn.setEnabled(True)
            self.statusBar().showMessage("AI setup did not finish.")
            QMessageBox.warning(self, "AI setup", f"Setup didn't finish:\n\n{err}")

    def _maybe_autostart_ollama(self):
        # If Ollama is installed but not running (e.g. after a reboot), start it
        # quietly in the background so the panel just works next time.
        if (
            ollama_setup.supported()
            and ollama_setup.is_installed()
            and not llm.is_available()
        ):
            self._autostart_thread = OllamaSetupThread(full=False)
            self._autostart_thread.done.connect(
                lambda ok, err: self._refresh_ollama_models() if ok else None
            )
            self._autostart_thread.start()

    def _on_task_changed(self, name: str):
        self.custom_prompt.setEnabled(name == "Custom…")

    def on_process(self):
        text = self.transcript_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Nothing to process", "Transcribe some audio first.")
            return
        model = self.ollama_combo.currentText()
        if not self.ollama_combo.isEnabled():
            QMessageBox.warning(self, "Ollama", "No Ollama model is available.")
            return

        task = self.task_combo.currentText()
        if task == "Custom…":
            instruction = self.custom_prompt.text().strip()
            if not instruction:
                QMessageBox.warning(self, "Custom task", "Enter a custom instruction.")
                return
        else:
            instruction = llm.TASKS[task]

        self.llm_edit.clear()
        self._set_processing(True)
        self.statusBar().showMessage(f"Running {model}…")

        self.llm_thread = LLMThread(text, model, instruction)
        self.llm_thread.token.connect(self._append_llm_token)
        self.llm_thread.done.connect(self._llm_done)
        self.llm_thread.error.connect(self._llm_error)
        self.llm_thread.start()

    def on_cancel_llm(self):
        if self.llm_thread and self.llm_thread.isRunning():
            self.llm_thread.cancel()

    def _append_llm_token(self, token: str):
        self.llm_edit.moveCursor(QTextCursor.End)
        self.llm_edit.insertPlainText(token)

    def _llm_done(self, _out: str):
        self._set_processing(False)
        self.statusBar().showMessage("LLM finished.")

    def _llm_error(self, message: str):
        self._set_processing(False)
        self.statusBar().showMessage("LLM failed.")
        QMessageBox.critical(self, "LLM error", message)

    def _set_processing(self, running: bool):
        self.process_btn.setEnabled(not running)
        self.llm_cancel_btn.setEnabled(running)

    # ---- Helpers ---------------------------------------------------------- #
    def _copy(self, edit: QTextEdit):
        QGuiApplication.clipboard().setText(edit.toPlainText())
        self.statusBar().showMessage("Copied to clipboard.")

    def _save(self, edit: QTextEdit, default_name: str):
        text = edit.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "Nothing to save", "There is no text to save.")
            return
        suggested = default_name + ".txt"
        if self.audio_path:
            base = os.path.splitext(os.path.basename(self.audio_path))[0]
            suggested = f"{base}.{default_name}.txt"
        path, _ = QFileDialog.getSaveFileName(self, "Save as", suggested, "Text (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self.statusBar().showMessage(f"Saved: {path}")

    def _check_updates(self):
        self.statusBar().showMessage("Checking for updates…")
        self._upd_thread = UpdateCheckThread()
        self._upd_thread.result.connect(self._on_update_result)
        self._upd_thread.start()

    def _on_update_result(self, available: bool, latest: str, err: str):
        if err:
            self.statusBar().showMessage("Update check failed.")
            QMessageBox.information(
                self, "Check for updates", f"Couldn't check for updates:\n\n{err}"
            )
        elif available:
            self.statusBar().showMessage(f"Update available: v{latest}")
            QMessageBox.information(
                self,
                "Update available",
                f"A newer version (v{latest}) is available — you're on "
                f"v{__version__}.\n\nTo update: open a terminal in the app folder "
                f"and run:\n\n    ./update.sh",
            )
        else:
            self.statusBar().showMessage("You're up to date.")
            QMessageBox.information(
                self,
                "Check for updates",
                f"You're on the latest version (v{__version__}).",
            )

    def _about(self):
        QMessageBox.about(
            self,
            "About Local Audio Transcriber",
            f"<b>Local Audio Transcriber</b> v{__version__}<br>"
            "Offline transcription with faster-whisper + optional local Ollama "
            "post-processing.<br><br>Everything runs on your machine — no cloud.",
        )

    # ---- Persist the user's choices between sessions --------------------- #
    def _load_settings(self):
        s = QSettings()
        model = s.value("model", "")
        if model:
            i = self.model_combo.findData(model)
            if i >= 0:
                self.model_combo.setCurrentIndex(i)
        device = s.value("device", "")
        if device:
            i = self.device_combo.findData(device)
            if i >= 0:
                self.device_combo.setCurrentIndex(i)
        lang_i = s.value("language_index", -1, type=int)
        if 0 <= lang_i < self.lang_combo.count():
            self.lang_combo.setCurrentIndex(lang_i)
        self.ts_check.setChecked(s.value("timestamps", False, type=bool))
        self.vad_check.setChecked(s.value("vad", True, type=bool))
        task = s.value("task", "")
        if task:
            i = self.task_combo.findText(task)
            if i >= 0:
                self.task_combo.setCurrentIndex(i)

    def _save_settings(self):
        s = QSettings()
        s.setValue("model", self.model_combo.currentData())
        s.setValue("device", self.device_combo.currentData())
        s.setValue("language_index", self.lang_combo.currentIndex())
        s.setValue("timestamps", self.ts_check.isChecked())
        s.setValue("vad", self.vad_check.isChecked())
        s.setValue("task", self.task_combo.currentText())

    def closeEvent(self, event):
        self._save_settings()
        for th in (self.transcribe_thread, self.llm_thread):
            if th and th.isRunning():
                th.cancel()
                th.wait(3000)
        event.accept()


def _friendly_error(message: str) -> str:
    """Turn common import/runtime errors into actionable guidance."""
    low = message.lower()
    if "faster_whisper" in low or "faster-whisper" in low:
        return (
            "faster-whisper is not installed.\n\n"
            "Run the installer:  ./install.sh\n"
            "or:  pip install faster-whisper"
        )
    if "cuda" in low or "cublas" in low or "libcudnn" in low:
        return (
            "GPU (CUDA) is unavailable or its libraries are missing.\n\n"
            "Switch Device to 'CPU', or install the NVIDIA CUDA/cuDNN runtime.\n\n"
            f"Details: {message}"
        )
    return message


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Local Audio Transcriber")
    app.setOrganizationName("LocalAudioTranscriber")  # needed for QSettings
    # Associates the window with local-transcriber.desktop so the taskbar shows
    # the right icon/name (esp. on Wayland/GNOME) and matches StartupWMClass.
    app.setDesktopFileName("local-transcriber")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
