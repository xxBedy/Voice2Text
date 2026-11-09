"""The single Czech window: drop a recording, watch progress, open the transcript."""

from __future__ import annotations

import enum
import logging
import math
import os
from collections.abc import Callable
from html import escape
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, Qt, QUrl, Signal
from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from voice2text import languages, paths, strings
from voice2text.gui.runner import DownloadRunner, ImportRunner, JobRunner
from voice2text.job import Done, Failed, JobRequest, Progress
from voice2text.models import (
    diarization_specs,
    import_models_from,
    is_installed,
    model_path,
    whisper_spec,
)
from voice2text.osutils import open_file, reveal_in_folder
from voice2text.transcriber import UnreadableAudioError, probe_duration

log = logging.getLogger(__name__)

WIDTH = 360
MIN_HEIGHT = 300
MIC = "\U0001f399️"

# Set on the central widget, not on the window: dialogs parented to the window (the quit
# confirmation) must keep the system look instead of inheriting these colours.
STYLE = """
#central { background: #ffffff; }
#central QLabel { color: #222222; font-size: 13px; }
#central QComboBox { font-size: 12px; }
#central QCheckBox { color: #222222; font-size: 12px; }
#central QCheckBox:disabled { color: #999999; }
#dropArea { border: 2px dashed #99aabb; border-radius: 10px; background: #f5f9fc; }
#dropArea QLabel { color: #556677; background: transparent; }
#messageBox { border-radius: 8px; }
#messageBox[kind="done"] { background: #f0fff4; border: 1px solid #9ae6b4; }
#messageBox[kind="error"] { background: #fff5f5; border: 1px solid #feb2b2; }
#messageBox QLabel { background: transparent; font-size: 12px; }
QLabel#muted { color: #777777; font-size: 11px; }
QLabel#dropIcon { font-size: 28px; }
QProgressBar { background: #e3e8ee; border: none; border-radius: 4px; min-height: 8px; max-height: 8px; }
QProgressBar::chunk { background: #38a169; border-radius: 4px; }
QPushButton { border-radius: 6px; padding: 6px 12px; font-size: 12px; }
QPushButton#primary { background: #2b6cb0; color: #ffffff; border: 1px solid #2b6cb0; }
QPushButton#ghost { background: #ffffff; color: #2b6cb0; border: 1px solid #2b6cb0; }
QPushButton#grey { background: #ffffff; color: #666666; border: 1px solid #bbbbbb; }
QRadioButton { border: 1px solid #bbbbbb; border-radius: 6px; padding: 3px 8px; background: #fafafa;
               color: #222222; font-size: 12px; spacing: 0px; }
QRadioButton:checked { background: #2b6cb0; color: #ffffff; border-color: #2b6cb0; }
QRadioButton::indicator { width: 0px; height: 0px; }
"""


class State(enum.Enum):
    NEED_MODEL = enum.auto()
    DOWNLOADING = enum.auto()
    IDLE = enum.auto()
    RUNNING = enum.auto()
    DONE = enum.auto()
    ERROR = enum.auto()


_V01_KEYS = ("format", "speakers", "speaker_count")  # settings v0.1 wrote (no audio_language)

_ACCEPTS_FILES = (State.IDLE, State.RUNNING, State.DONE, State.ERROR)


def _muted(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("muted")
    return label


def _breakable(path: Path) -> str:
    """Let long paths (no spaces on Windows) wrap at separators inside a narrow label."""
    return str(path).replace(os.sep, os.sep + "​")


class DropArea(QFrame):
    """Dashed drop target; a left click asks the window to open the file dialog."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("dropArea")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.icon_label = QLabel(MIC)
        self.icon_label.setObjectName("dropIcon")
        self.hint_label = QLabel(strings.DROP_HINT)
        self.sub_label = _muted(strings.DROP_SUB)
        self._layout = QVBoxLayout(self)
        self._layout.setSpacing(2)
        for label in (self.icon_label, self.hint_label, self.sub_label):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._layout.addWidget(label)
        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self.set_mode("full")

    def set_mode(self, mode: str) -> None:
        """`full` (waiting), `dim` (job running) or `compact` (after a result)."""
        compact = mode == "compact"
        self.icon_label.setVisible(not compact)
        self.sub_label.setVisible(mode == "full")
        self.hint_label.setText(strings.DROP_NEXT if compact else strings.DROP_HINT)
        self._layout.setContentsMargins(10, 10 if compact else 18, 10, 10 if compact else 18)
        self._opacity.setOpacity(0.45 if mode == "dim" else 1.0)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            self.clicked.emit()
        else:
            super().mousePressEvent(event)


class MainWindow(QWidget):
    def __init__(
        self,
        runner: JobRunner,
        downloader_factory: Callable[[], DownloadRunner],
        settings: QSettings,
        models_dir: Path,
        speakers_enabled: bool,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.runner = runner
        self.settings = settings
        self.models_dir = Path(models_dir)
        self.speakers_enabled = speakers_enabled
        self._downloader_factory = downloader_factory
        self._downloader: DownloadRunner | None = None
        self._download_in_background = False
        self._importer: ImportRunner | None = None
        self._importing = False
        self._output: Path | None = None
        self._state = State.IDLE

        self.setObjectName("mainWindow")
        self.setWindowTitle(strings.WINDOW_TITLE)
        self.setAcceptDrops(True)
        self._build_ui()
        self._load_settings()

        runner.progress.connect(self._on_progress)
        runner.finished.connect(self._on_finished)

        self._refresh_speakers()
        if self._whisper_ready():
            self.state = State.IDLE
            self._fetch_speaker_models_if_missing()
        else:
            self._start_download()

    # --- layout ---------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.central = QWidget()
        self.central.setObjectName("central")
        self.central.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.central.setStyleSheet(STYLE)
        outer.addWidget(self.central)

        root = QVBoxLayout(self.central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(8)

        self.model_label = QLabel(strings.MODEL_NEEDED)
        self.model_label.setWordWrap(True)
        root.addWidget(self.model_label)

        self.message_box = QFrame()
        self.message_box.setObjectName("messageBox")
        box = QVBoxLayout(self.message_box)
        box.setContentsMargins(10, 8, 10, 8)
        box.setSpacing(2)
        self.message_label = QLabel()
        self.message_label.setTextFormat(Qt.TextFormat.PlainText)
        self.message_label.setWordWrap(True)
        self.detail_label = _muted()
        self.detail_label.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_label.setWordWrap(True)
        box.addWidget(self.message_label)
        box.addWidget(self.detail_label)
        root.addWidget(self.message_box)

        self.open_button = QPushButton(strings.OPEN)
        self.open_button.setObjectName("primary")
        self.open_button.clicked.connect(self._open_output)
        self.show_button = QPushButton(strings.SHOW)
        self.show_button.setObjectName("ghost")
        self.show_button.clicked.connect(self._reveal_output)
        self.result_row = self._row(self.open_button, self.show_button)
        root.addWidget(self.result_row)

        self.drop_area = DropArea()
        self.drop_area.clicked.connect(self._choose_file)
        root.addSpacing(4)
        root.addWidget(self.drop_area)

        self.file_label = QLabel()
        self.file_label.setTextFormat(Qt.TextFormat.RichText)
        self.file_label.setWordWrap(True)
        self.status_label = _muted()
        self.status_label.setWordWrap(True)
        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        root.addWidget(self.file_label)
        root.addWidget(self.status_label)
        root.addWidget(self.progress_bar)

        self.cancel_button = QPushButton(strings.CANCEL)
        self.cancel_button.setObjectName("grey")
        self.cancel_button.clicked.connect(self._cancel_job)
        self.cancel_row = self._row(self.cancel_button)
        root.addWidget(self.cancel_row)

        self.language_label = _muted(strings.AUDIO_LANGUAGE)
        self.language_combo = QComboBox()
        self.language_combo.setMaxVisibleItems(15)
        self.language_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.language_combo.setMinimumContentsLength(16)
        for code, name in languages.choices():
            self.language_combo.addItem(name, code)
        self.language_row = self._row(self.language_label, self.language_combo)
        root.addWidget(self.language_row)

        self.md_radio = QRadioButton(strings.FMT_MD)
        self.txt_radio = QRadioButton(strings.FMT_TXT)
        self._format_group = QButtonGroup(self)
        self._format_group.addButton(self.md_radio)
        self._format_group.addButton(self.txt_radio)
        self.format_row = self._row(_muted(strings.SAVE_AS), self.md_radio, self.txt_radio)
        root.addWidget(self.format_row)

        self.speakers_check = QCheckBox(strings.SPEAKERS)
        self.speakers_note = _muted(strings.SPEAKERS_NOTE)
        self.speakers_row = self._row(self.speakers_check, self.speakers_note)
        root.addWidget(self.speakers_row)

        self.speaker_count_label = _muted(strings.SPEAKER_COUNT)
        self.speaker_count = QSpinBox()
        self.speaker_count.setRange(2, 6)
        self.speaker_count.setValue(2)
        self.speaker_count_row = self._row(self.speaker_count_label, self.speaker_count)
        root.addWidget(self.speaker_count_row)

        self.retry_button = QPushButton(strings.MODEL_RETRY)
        self.retry_button.setObjectName("primary")
        self.retry_button.clicked.connect(self._start_download)
        self.import_button = QPushButton(strings.MODEL_IMPORT)
        self.import_button.setObjectName("ghost")
        self.import_button.clicked.connect(self._import_model)
        self.model_row = self._row(self.retry_button, self.import_button)
        root.addWidget(self.model_row)

        root.addStretch(1)

    @staticmethod
    def _row(*widgets: QWidget) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for w in widgets:
            layout.addWidget(w)
        layout.addStretch(1)
        return row

    def _fit(self) -> None:
        """Fixed 360 px wide window, at least 300 px tall, never clipping the current state."""
        # Inner layouts (message box text, drop area mode) changed: drop the outer
        # layout's cached size hints, otherwise it lays out the previous state.
        for child in self.findChildren(QWidget):
            child.updateGeometry()
        layout = self.layout()
        layout.invalidate()
        layout.activate()
        if layout.hasHeightForWidth():
            height = layout.totalHeightForWidth(WIDTH)
        else:
            height = layout.totalSizeHint().height()
        self.setFixedSize(WIDTH, max(MIN_HEIGHT, height))

    # --- state ----------------------------------------------------------------

    @property
    def state(self) -> State:
        return self._state

    @state.setter
    def state(self, value: State) -> None:
        self._state = value
        self._render()

    def _render(self) -> None:
        s = self._state
        needs_model = s in (State.NEED_MODEL, State.DOWNLOADING)
        choosing = s in (State.IDLE, State.DONE, State.ERROR)
        self.model_label.setVisible(needs_model)
        self.message_box.setVisible(bool(self.message_label.text()))
        self.result_row.setVisible(s == State.DONE)
        self.drop_area.setVisible(not needs_model)
        self.drop_area.set_mode(
            {State.RUNNING: "dim", State.DONE: "compact", State.ERROR: "compact"}.get(s, "full")
        )
        self.file_label.setVisible(s == State.RUNNING)
        self.status_label.setVisible(s in (State.RUNNING, State.DOWNLOADING))
        self.progress_bar.setVisible(s in (State.RUNNING, State.DOWNLOADING))
        self.cancel_row.setVisible(s == State.RUNNING)
        self.language_row.setVisible(choosing)
        self.format_row.setVisible(choosing)
        self.speakers_row.setVisible(choosing and self.speakers_enabled)
        self.speaker_count_row.setVisible(choosing and self.speakers_enabled)
        # Manual import is offered while the model is missing, during the first-run download
        # too (a blocked or crawling corporate network), but not while an import runs.
        offer_import = s == State.NEED_MODEL or (s == State.DOWNLOADING and not self._importing)
        self.retry_button.setVisible(s == State.NEED_MODEL)
        self.import_button.setVisible(offer_import)
        self.model_row.setVisible(offer_import)
        self._fit()

    def message_text(self) -> str:
        """Text of the message box and the status line, as currently shown."""
        labels = (self.model_label, self.message_label, self.detail_label, self.status_label)
        return "\n".join(l.text() for l in labels if l.text() and l.isVisibleTo(self))

    def _set_message(self, text: str, detail: str = "", kind: str = "error") -> None:
        self.message_label.setText(text)
        self.detail_label.setText(detail)
        self.detail_label.setVisible(bool(detail))
        self.message_box.setProperty("kind", kind)
        self.message_box.style().unpolish(self.message_box)
        self.message_box.style().polish(self.message_box)
        self.message_box.setVisible(bool(text))
        self._fit()

    def _clear_message(self) -> None:
        self._set_message("")

    def _set_status(self, text: str, fraction: float | None) -> None:
        self.status_label.setText(text)
        if fraction is None:
            self.progress_bar.setRange(0, 0)  # busy indicator
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(_percent(fraction))

    @staticmethod
    def _generic_error() -> str:
        return strings.ERR_GENERIC.format(log=_breakable(paths.logs_dir()))

    # --- settings -------------------------------------------------------------

    def _load_settings(self) -> None:
        fmt = self.settings.value("format", "md")
        (self.txt_radio if fmt == "txt" else self.md_radio).setChecked(True)
        self.speakers_check.setChecked(bool(self.settings.value("speakers", False, type=bool)))
        saved = self.settings.value("audio_language", "")
        index = self.language_combo.findData(saved) if saved else -1
        if index < 0:
            if not saved and any(self.settings.contains(k) for k in _V01_KEYS):
                default = "cs"  # an upgrade from v0.1, which only transcribed Czech
            else:
                default = languages.default_choice()  # first run
            index = max(0, self.language_combo.findData(default))
        self.language_combo.setCurrentIndex(index)
        self.language_combo.currentIndexChanged.connect(self._save_settings)
        self.md_radio.toggled.connect(self._save_settings)
        self.speaker_count.setValue(int(self.settings.value("speaker_count", 2, type=int)))
        self.speakers_check.toggled.connect(self._save_settings)
        self.speakers_check.toggled.connect(lambda _on: self._refresh_speaker_count())
        self.speaker_count.valueChanged.connect(self._save_settings)

    def _save_settings(self) -> None:
        self.settings.setValue("format", self._fmt())
        self.settings.setValue("audio_language", self._language())
        self.settings.setValue("speakers", self.speakers_check.isChecked())
        self.settings.setValue("speaker_count", self.speaker_count.value())
        self.settings.sync()

    def _language(self) -> str:
        return str(self.language_combo.currentData() or "auto")

    def _fmt(self) -> str:
        return "txt" if self.txt_radio.isChecked() else "md"

    # --- recordings -----------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        # Accept drags without file URLs too (Outlook attachments, file promises from apps),
        # so the drop can explain what to do instead of showing a "not allowed" cursor.
        if self._state in _ACCEPTS_FILES:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        event.acceptProposedAction()
        self.handle_drop(list(event.mimeData().urls()))

    def _choose_file(self) -> None:
        if self._busy():
            self._set_message(strings.ERR_BUSY)
            return
        start = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
        path, _ = QFileDialog.getOpenFileName(self, strings.FILE_DIALOG_TITLE, start, f"{strings.FILE_FILTER};;{strings.FILE_FILTER_ALL}")
        if path:
            self.handle_drop([QUrl.fromLocalFile(path)])

    def handle_drop(self, urls: list[QUrl]) -> None:
        if self._state not in _ACCEPTS_FILES:
            return
        if self._busy():
            self._set_message(strings.ERR_BUSY)
            return
        url = urls[0] if urls else None
        path = Path(url.toLocalFile()) if url is not None and url.isLocalFile() else None
        if path is None or not path.is_file():
            self._reject_drop(strings.ERR_NOT_FILE)
            return
        try:
            duration = probe_duration(path)
        except UnreadableAudioError as e:
            log.info("unreadable recording %s: %s", path.name, e)
            self._reject_drop(strings.ERR_UNREADABLE)
            return
        self._start_job(path, duration)

    def _busy(self) -> bool:
        return self._state == State.RUNNING or self.runner.is_running()

    def _reject_drop(self, text: str) -> None:
        self._output = None
        self._set_message(text)
        self.state = State.IDLE

    def _start_job(self, audio: Path, duration: float) -> None:
        req = JobRequest(
            audio=audio,
            fmt=self._fmt(),
            speakers=self.speakers_enabled and self.speakers_check.isEnabled() and self.speakers_check.isChecked(),
            whisper_dir=model_path(whisper_spec(), self.models_dir),
            models_dir=self.models_dir,
            fallback_dir=paths.fallback_output_dir(),
            num_speakers=self.speaker_count.value(),
            language=self._language(),
        )
        self._output = None
        self._clear_message()
        self.file_label.setText(f"<b>{escape(audio.name)}</b>{strings.SEPARATOR}{strings.format_minutes(duration)}")
        self._set_status(strings.PREPARING, None)
        self.state = State.RUNNING
        try:
            self.runner.start(req)
        except Exception:
            log.exception("could not start the job")
            self._set_message(self._generic_error())
            self.state = State.ERROR

    def _on_progress(self, event: object) -> None:
        if self._state != State.RUNNING or not isinstance(event, Progress):
            return
        if event.stage == "transcribing":
            fraction = event.fraction or 0.0
            text = strings.TRANSCRIBING.format(pct=_percent(fraction))
            eta = strings.format_eta(event.eta_s)
            if eta:
                text += strings.SEPARATOR + eta
            self._set_status(text, fraction)
        elif event.stage == "diarizing" and event.fraction is not None:
            text = strings.DIARIZING_PCT.format(pct=_percent(event.fraction))
            eta = strings.format_eta(event.eta_s)
            if eta:
                text += strings.SEPARATOR + eta
            self._set_status(text, event.fraction)
        elif event.stage == "diarizing":
            self._set_status(strings.DIARIZING, None)
        elif event.stage == "saving":
            self._set_status(strings.SAVING, None)
        else:
            self._set_status(strings.PREPARING, None)

    def _on_finished(self, event: object) -> None:
        if self._state != State.RUNNING:
            return
        if isinstance(event, Done):
            self._output = event.output
            saved = strings.SAVED_FALLBACK if event.fell_back else strings.SAVED_NEXT_TO
            text = strings.DONE.format(elapsed=strings.format_minutes(event.elapsed_s))
            if event.speakers_failed:
                text += "\n" + strings.SPEAKERS_FAILED
            detail = saved.format(name=event.output.name)
            if event.detected_language:
                detail += "\n" + strings.DETECTED_LANGUAGE.format(name=languages.display_name(event.detected_language))
            self._set_message(text, detail, kind="done")
            self.state = State.DONE
            return
        code = event.code if isinstance(event, Failed) else "error"
        text = {"unreadable": strings.ERR_UNREADABLE, "no_speech": strings.ERR_NO_SPEECH}.get(code)
        self._set_message(text or self._generic_error())
        self.state = State.ERROR

    def _cancel_job(self) -> None:
        self.runner.cancel()
        if self._state == State.RUNNING:  # else cancel() delivered a Done that had just arrived
            self._clear_message()
            self.state = State.IDLE

    def _open_output(self) -> None:
        if self._output is not None and not open_file(self._output):
            log.warning("no app opened %s; showing it in its folder", self._output.name)
            reveal_in_folder(self._output)

    def _reveal_output(self) -> None:
        if self._output is not None:
            reveal_in_folder(self._output)

    # --- models ---------------------------------------------------------------

    def _whisper_ready(self) -> bool:
        return is_installed(whisper_spec(), self.models_dir)

    def _speaker_models_ready(self) -> bool:
        return all(is_installed(spec, self.models_dir) for spec in diarization_specs())

    def _refresh_speakers(self) -> None:
        available = self._speaker_models_ready()
        self.speakers_check.setEnabled(available)
        self.speakers_note.setText(strings.SPEAKERS_NOTE if available else strings.SPEAKERS_UNAVAILABLE)
        self._refresh_speaker_count()

    def _refresh_speaker_count(self) -> None:
        enabled = self.speakers_check.isEnabled() and self.speakers_check.isChecked()
        self.speaker_count.setEnabled(enabled)
        self.speaker_count_label.setEnabled(enabled)

    def _new_downloader(self, background: bool) -> DownloadRunner:
        previous = self._downloader
        if previous is not None and previous.isRunning():
            previous.wait(2000)
        downloader = self._downloader_factory()
        self._downloader = downloader
        self._download_in_background = background
        downloader.progress.connect(self._on_download_progress)
        downloader.failed.connect(self._on_download_failed)
        downloader.succeeded.connect(self._on_download_succeeded)
        return downloader

    def _start_download(self) -> None:
        """First launch or retry: fetch what is missing and show the progress."""
        downloader = self._new_downloader(background=False)
        self._clear_message()
        self._set_status(strings.PREPARING, None)
        self.state = State.DOWNLOADING
        downloader.start()

    def _fetch_speaker_models_if_missing(self) -> None:
        """Whisper is enough to work; the small speaker models arrive quietly in the background."""
        if not self.speakers_enabled or self._speaker_models_ready():
            return
        if self._downloader is not None and self._downloader.isRunning():
            return
        log.info("speaker models missing, fetching them in the background")
        self._new_downloader(background=True).start()

    def _from_stale_downloader(self) -> bool:
        sender = self.sender()
        return sender is not None and sender is not self._downloader

    def _on_download_progress(self, done: int, total: int) -> None:
        if self._from_stale_downloader() or self._download_in_background or self._state != State.DOWNLOADING:
            return
        fraction = done / total if total else 0.0
        text = strings.MODEL_DOWNLOADING.format(
            pct=_percent(fraction), done=strings.format_bytes(done), total=strings.format_bytes(total)
        )
        self._set_status(text, fraction)

    def _on_download_failed(self, code: str) -> None:
        if self._from_stale_downloader():
            return
        if self._whisper_ready():
            # Only the speaker models are missing: work without them, no error popup.
            log.warning("speaker models unavailable (%s), transcribing without speakers", code)
            self._refresh_speakers()
            if self._state in (State.NEED_MODEL, State.DOWNLOADING):
                self._clear_message()
                self.state = State.IDLE
            return
        if code == "cancelled":
            self._clear_message()
        elif code == "network":
            self._set_message(strings.ERR_DOWNLOAD_NETWORK)
        elif code == "space":
            self._set_message(strings.ERR_DOWNLOAD_SPACE)
        elif code == "busy":
            self._set_message(strings.ERR_DOWNLOAD_BUSY)
        elif code == "corrupt":
            self._set_message(strings.ERR_DOWNLOAD_CORRUPT)
        else:
            self._set_message(self._generic_error())
        self.state = State.NEED_MODEL

    def _on_download_succeeded(self) -> None:
        if self._from_stale_downloader():
            return
        self._refresh_speakers()
        if self._download_in_background:
            return
        if self._whisper_ready():
            self._clear_message()
            self.state = State.IDLE
        else:
            log.error("download finished but the speech model is still missing")
            self._set_message(self._generic_error())
            self.state = State.NEED_MODEL

    def _stop_foreground_download(self) -> None:
        """A manual import replaces the first-run download: stop it and ignore its late signals."""
        downloader = self._downloader
        if downloader is None or self._download_in_background or not downloader.isRunning():
            return
        log.info("manual import chosen; cancelling the model download")
        downloader.cancel()
        downloader.wait(3000)  # let it release the model lock before the import takes it
        self._downloader = None  # its late "cancelled" now counts as stale

    def _import_model(self) -> None:
        self._stop_foreground_download()
        folder = QFileDialog.getExistingDirectory(self, strings.MODEL_IMPORT_TITLE)
        if not folder:
            if self._state == State.DOWNLOADING:  # the download was stopped above
                self._clear_message()
                self.state = State.NEED_MODEL
            return
        importer = ImportRunner(Path(folder), self.models_dir, import_fn=import_models_from)
        self._importer = importer
        importer.succeeded.connect(self._on_import_succeeded)
        importer.failed.connect(self._on_import_failed)
        self._importing = True
        self._clear_message()
        self._set_status(strings.MODEL_IMPORTING, None)
        self.state = State.DOWNLOADING
        importer.start()

    def _on_import_succeeded(self, keys: list[str]) -> None:
        self._importing = False
        self._refresh_speakers()
        if self._whisper_ready():
            self._clear_message()
            self.state = State.IDLE
            self._fetch_speaker_models_if_missing()
        else:  # only speaker models were in the folder
            self._set_message(strings.ERR_IMPORT_INVALID)
            self.state = State.NEED_MODEL

    def _on_import_failed(self, code: str) -> None:
        self._importing = False
        text = {"invalid": strings.ERR_IMPORT_INVALID, "space": strings.ERR_DOWNLOAD_SPACE, "busy": strings.ERR_DOWNLOAD_BUSY}.get(code)
        self._set_message(text or self._generic_error())
        self._refresh_speakers()
        if self._whisper_ready():
            # A models folder failed part-way (e.g. on the speaker models) after Whisper
            # was copied: say so, but the app can transcribe now.
            self.state = State.IDLE
            self._fetch_speaker_models_if_missing()
        else:
            self.state = State.NEED_MODEL

    # --- quitting ---------------------------------------------------------------

    def _confirm_quit(self) -> bool:
        """Ask before killing a running transcription; Czech buttons, staying is the default."""
        box = QMessageBox(
            QMessageBox.Icon.Question,
            strings.CONFIRM_QUIT_TITLE,
            strings.CONFIRM_QUIT,
            QMessageBox.StandardButton.NoButton,
            self,
        )
        quit_button = box.addButton(strings.QUIT_CONFIRM_YES, QMessageBox.ButtonRole.DestructiveRole)
        stay_button = box.addButton(strings.QUIT_CONFIRM_NO, QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(stay_button)
        box.setEscapeButton(stay_button)
        box.exec()
        confirmed = box.clickedButton() is quit_button
        box.deleteLater()
        return confirmed

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.runner.is_running():
            if not self._confirm_quit():
                event.ignore()
                return
            self.runner.cancel()
        downloader = self._downloader
        if downloader is not None and downloader.isRunning():
            downloader.cancel()  # resumes from the .part files on next launch
            downloader.wait(3000)  # a stuck socket read cannot be interrupted; the daemon thread dies with us
        event.accept()


def _percent(fraction: float) -> int:
    return max(0, min(100, math.floor(fraction * 100 + 1e-6)))
