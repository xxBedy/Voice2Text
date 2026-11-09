from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QObject, QPointF, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QFileDialog, QMessageBox

from voice2text import osutils, paths, strings
from voice2text.gui import main_window
from voice2text.gui.main_window import MainWindow, State
from voice2text.job import Done, Failed, Progress
from voice2text.models import (
    COMPLETE_MARKER,
    ImportFailed,
    InvalidModelFolder,
    diarization_specs,
    model_path,
    required_specs,
    whisper_spec,
)
from voice2text.transcriber import UnreadableAudioError


class FakeRunner(QObject):
    progress = Signal(object)
    finished = Signal(object)

    def __init__(self):
        super().__init__()
        self.started = []
        self.running = False
        self.cancelled = False

    def start(self, req):
        self.started.append(req)
        self.running = True

    def cancel(self):
        self.cancelled = True
        self.running = False

    def is_running(self):
        return self.running


class FakeDownloader(QObject):
    progress = Signal("qint64", "qint64")  # model sizes exceed 32-bit int
    failed = Signal(str)
    succeeded = Signal()

    def __init__(self):
        super().__init__()
        self.started = False
        self.cancelled = False
        self.ended = False
        self.failed.connect(self._end)  # connected first: runs before the window's slot
        self.succeeded.connect(self._end)

    def _end(self, *args):
        self.ended = True

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def isRunning(self):
        return self.started and not self.cancelled and not self.ended

    def wait(self, *a):
        return True


def answer_quit_dialog(monkeypatch, button_text: str | None) -> list[dict]:
    """Stand-in for the modal quit dialog: record what it shows, click the button with this text."""
    shown: list[dict] = []

    def fake_exec(box):
        shown.append(
            {
                "title": box.windowTitle(),
                "text": box.text(),
                "buttons": sorted(b.text() for b in box.buttons()),
                "default": box.defaultButton().text() if box.defaultButton() else None,
                "escape": box.escapeButton().text() if box.escapeButton() else None,
            }
        )
        for button in box.buttons():
            if button.text() == button_text:
                button.click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    return shown


def install(models_dir: Path, specs) -> None:
    for spec in specs:
        folder = model_path(spec, models_dir)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / COMPLETE_MARKER).touch()


@pytest.fixture
def win_factory(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(main_window, "probe_duration", lambda path: 60.0)
    # pytest-qt closes widgets before fixture teardown; never let a real modal dialog block.
    answer_quit_dialog(monkeypatch, strings.QUIT_CONFIRM_YES)
    windows = []
    counter = iter(range(1000))

    def make(installed=True, settings=None, reuse_settings=None, speakers_enabled=True):
        models_dir = tmp_path / "models"
        if installed:
            install(models_dir, required_specs(speakers_enabled))
        if reuse_settings is not None:
            s = reuse_settings
        else:
            s = QSettings(str(tmp_path / f"settings{next(counter)}.ini"), QSettings.Format.IniFormat)
            for key, value in (settings or {}).items():
                s.setValue(key, value)
        downloaders = []

        def factory():
            downloaders.append(FakeDownloader())
            return downloaders[-1]

        w = MainWindow(FakeRunner(), factory, s, models_dir, speakers_enabled)
        w.downloaders = downloaders
        qtbot.addWidget(w)
        w.show()
        windows.append(w)
        return w, (downloaders[-1] if downloaders else None)

    yield make
    for w in windows:
        w.runner.running = False
        for d in w.downloaders:
            d.cancelled = True
        w.close()


def drop(w, path: Path) -> None:
    w.handle_drop([QUrl.fromLocalFile(str(path))])


def drag_event(cls, mime: QMimeData):
    return cls(QPointF(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)


def audio_file(tmp_path: Path, name: str = "a.m4a") -> Path:
    p = tmp_path / name
    p.write_bytes(b"x")
    return p


# --- tests from the brief -------------------------------------------------


def test_starts_in_need_model_and_autostarts_download_when_model_missing(win_factory):
    w, dl = win_factory(installed=False)
    assert w.state in (State.NEED_MODEL, State.DOWNLOADING) and dl.started


def test_download_network_failure_shows_retry_and_import(win_factory, qtbot):
    w, dl = win_factory(installed=False)
    dl.failed.emit("network")
    assert w.state == State.NEED_MODEL and strings.ERR_DOWNLOAD_NETWORK in w.message_text()
    assert w.retry_button.isVisible() and w.import_button.isVisible()


@pytest.mark.parametrize(
    ("code", "text"), [("busy", strings.ERR_DOWNLOAD_BUSY), ("corrupt", strings.ERR_DOWNLOAD_CORRUPT)]
)
def test_download_busy_and_corrupt_show_message_with_retry_and_import(win_factory, qtbot, code, text):
    w, dl = win_factory(installed=False)
    dl.failed.emit(code)
    assert w.state == State.NEED_MODEL and text in w.message_text()
    assert w.retry_button.isVisible() and w.import_button.isVisible()


def test_drop_local_file_starts_job_with_saved_format(win_factory, tmp_path):
    w, _ = win_factory(installed=True, settings={"format": "txt"})
    audio = tmp_path / "a.m4a"
    audio.write_bytes(b"x")
    w.handle_drop([QUrl.fromLocalFile(str(audio))])
    assert w.state == State.RUNNING and w.runner.started[0].fmt == "txt"


def test_drop_non_file_url_shows_hint(win_factory):
    w, _ = win_factory(installed=True)
    w.handle_drop([QUrl("https://example.com/x.m4a")])
    assert w.state == State.IDLE and strings.ERR_NOT_FILE in w.message_text()


def test_drop_while_running_is_ignored(win_factory, tmp_path):
    w, _ = win_factory(installed=True)
    a = tmp_path / "a.m4a"
    a.write_bytes(b"x")
    b = tmp_path / "b.m4a"
    b.write_bytes(b"x")
    w.handle_drop([QUrl.fromLocalFile(str(a))])
    w.handle_drop([QUrl.fromLocalFile(str(b))])
    assert len(w.runner.started) == 1 and strings.ERR_BUSY in w.message_text()


def test_done_shows_buttons_and_saved_location(win_factory, tmp_path):
    w, _ = win_factory(installed=True)
    w.state = State.RUNNING
    w.runner.finished.emit(Done(tmp_path / "a.md", False, 660))
    assert w.state == State.DONE and "Hotovo za 11 min" in w.message_text()
    assert "vedle nahrávky" in w.message_text() and w.open_button.isVisible()


def test_format_and_speakers_choice_persist(win_factory):
    w, _ = win_factory(installed=True)
    w.txt_radio.click()
    w.speakers_check.setChecked(True)
    w2, _ = win_factory(installed=True, reuse_settings=w.settings)
    assert w2.txt_radio.isChecked() and w2.speakers_check.isChecked()


def test_speakers_checkbox_hidden_when_feature_disabled(win_factory):
    w, _ = win_factory(installed=True, speakers_enabled=False)
    assert not w.speakers_check.isVisibleTo(w)


def test_close_while_running_asks_and_cancel_keeps_running(win_factory, monkeypatch):
    w, _ = win_factory(installed=True)
    w.state = State.RUNNING
    w.runner.running = True
    shown = answer_quit_dialog(monkeypatch, strings.QUIT_CONFIRM_NO)
    w.close()
    assert w.isVisible() and not w.runner.cancelled
    assert shown == [
        {
            # Qt drops message box titles on macOS (Apple guidelines); the text says it all.
            "title": "" if sys.platform == "darwin" else strings.CONFIRM_QUIT_TITLE,
            "text": strings.CONFIRM_QUIT,
            "buttons": sorted(["Ukončit", "Pokračovat v přepisu"]),
            "default": "Pokračovat v přepisu",
            "escape": "Pokračovat v přepisu",
        }
    ]


def test_quit_dialog_closed_without_a_choice_keeps_running(win_factory, monkeypatch):
    w, _ = win_factory(installed=True)
    w.state = State.RUNNING
    w.runner.running = True
    answer_quit_dialog(monkeypatch, None)
    w.close()
    assert w.isVisible() and not w.runner.cancelled


def test_window_style_does_not_leak_into_dialogs(win_factory, qapp):
    """Dialogs parented to the window keep the system look (readable in dark mode)."""
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QLabel

    w, _ = win_factory(installed=True)
    box = QMessageBox(QMessageBox.Icon.Question, "t", "dialog text", QMessageBox.StandardButton.NoButton, w)
    button = box.addButton("x", QMessageBox.ButtonRole.RejectRole)
    label = next(l for l in box.findChildren(QLabel) if l.text() == "dialog text")
    for widget in (label, button):
        widget.ensurePolished()
        default = qapp.font(widget)
        assert (widget.font().pointSizeF(), widget.font().pixelSize()) == (default.pointSizeF(), default.pixelSize())
    role = QPalette.ColorRole.WindowText
    assert label.palette().color(role) == qapp.palette(label).color(role)
    w.model_label.ensurePolished()
    assert w.model_label.palette().color(role).name() == "#222222"  # the window itself stays styled


# --- additional coverage ---------------------------------------------------


def test_close_while_running_yes_cancels_job_and_closes(win_factory, monkeypatch, tmp_path):
    w, _ = win_factory(installed=True)
    drop(w, audio_file(tmp_path))
    answer_quit_dialog(monkeypatch, strings.QUIT_CONFIRM_YES)
    w.close()
    assert not w.isVisible() and w.runner.cancelled


def test_close_during_download_cancels_it(win_factory):
    w, dl = win_factory(installed=False)
    w.close()
    assert not w.isVisible() and dl.cancelled


def test_idle_when_models_installed(win_factory):
    w, dl = win_factory(installed=True)
    assert w.state == State.IDLE and dl is None
    assert w.md_radio.isChecked() and not w.speakers_check.isChecked()


def test_whisper_only_install_is_ready_and_fetches_speaker_models_in_background(win_factory, tmp_path):
    install(tmp_path / "models", [whisper_spec()])
    w, dl = win_factory(installed=False, speakers_enabled=True)
    assert w.state == State.IDLE and dl is not None and dl.started
    assert w.speakers_check.isVisible() and not w.speakers_check.isEnabled()
    assert w.speakers_note.text() == strings.SPEAKERS_UNAVAILABLE
    dl.progress.emit(10_000_000, 40_000_000)
    assert w.state == State.IDLE and w.message_text() == ""
    install(tmp_path / "models", diarization_specs())
    dl.succeeded.emit()
    assert w.state == State.IDLE and w.speakers_check.isEnabled()
    assert w.speakers_note.text() == strings.SPEAKERS_NOTE


def test_background_speaker_fetch_failure_is_quiet_and_keeps_checkbox_disabled(win_factory, tmp_path):
    install(tmp_path / "models", [whisper_spec()])
    w, dl = win_factory(installed=False, speakers_enabled=True, settings={"speakers": True})
    dl.failed.emit("network")
    assert w.state == State.IDLE and w.message_text() == ""
    assert not w.speakers_check.isEnabled()
    drop(w, audio_file(tmp_path))
    assert w.runner.started[0].speakers is False


def test_first_download_failing_after_whisper_still_opens_the_window(win_factory, tmp_path):
    w, dl = win_factory(installed=False, speakers_enabled=True)
    install(tmp_path / "models", [whisper_spec()])  # whisper arrived, diarization did not
    dl.failed.emit("network")
    assert w.state == State.IDLE and w.message_text() == ""
    assert not w.speakers_check.isEnabled()


def test_window_size_is_fixed(win_factory):
    w, _ = win_factory(installed=True)
    assert w.minimumSize() == w.maximumSize()
    assert w.width() == 360 and w.height() >= 300
    assert w.windowTitle() == strings.WINDOW_TITLE


def test_unreadable_drop_shows_message_and_starts_nothing(win_factory, tmp_path, monkeypatch):
    w, _ = win_factory(installed=True)

    def unreadable(path):
        raise UnreadableAudioError("nope")

    monkeypatch.setattr(main_window, "probe_duration", unreadable)
    drop(w, audio_file(tmp_path))
    assert w.state == State.IDLE and strings.ERR_UNREADABLE in w.message_text()
    assert w.runner.started == []


def test_drop_missing_file_shows_hint(win_factory, tmp_path):
    w, _ = win_factory(installed=True)
    drop(w, tmp_path / "gone.m4a")
    assert strings.ERR_NOT_FILE in w.message_text() and w.runner.started == []


def test_request_fields(win_factory, tmp_path):
    w, _ = win_factory(installed=True)
    w.speakers_check.setChecked(True)
    audio = audio_file(tmp_path, "Porada 3.11.m4a")
    drop(w, audio)
    req = w.runner.started[0]
    assert req.audio == audio and req.fmt == "md" and req.speakers is True
    assert req.whisper_dir == model_path(whisper_spec(), tmp_path / "models")
    assert req.models_dir == tmp_path / "models"
    assert req.fallback_dir.name == "Přepisy"
    assert "Porada 3.11.m4a" in w.file_label.text() and "1 min" in w.file_label.text()
    assert strings.PREPARING in w.message_text()


def test_speaker_count_defaults_and_range(win_factory):
    w, _ = win_factory(installed=True)
    assert w.speaker_count.value() == 2
    assert (w.speaker_count.minimum(), w.speaker_count.maximum()) == (2, 6)


def test_speaker_count_enabled_only_with_checkbox(win_factory):
    w, _ = win_factory(installed=True)
    assert not w.speaker_count.isEnabled()
    w.speakers_check.setChecked(True)
    assert w.speaker_count.isEnabled()
    w.speakers_check.setChecked(False)
    assert not w.speaker_count.isEnabled()


def test_speaker_count_disabled_when_models_missing(win_factory):
    w, _ = win_factory(installed=False, speakers_enabled=True, settings={"speakers": True})
    assert not w.speakers_check.isEnabled() and not w.speaker_count.isEnabled()


def test_speaker_count_hidden_when_feature_disabled(win_factory):
    w, _ = win_factory(installed=True, speakers_enabled=False)
    assert not w.speaker_count.isVisibleTo(w)
    assert not w.speaker_count_label.isVisibleTo(w)


def test_speaker_count_persists_and_reaches_request(win_factory, tmp_path):
    w, _ = win_factory(installed=True)
    w.speakers_check.setChecked(True)
    w.speaker_count.setValue(4)
    w2, _ = win_factory(installed=True, reuse_settings=w.settings)
    assert w2.speaker_count.value() == 4
    drop(w2, audio_file(tmp_path))
    assert w2.runner.started[0].num_speakers == 4


def test_speakers_never_requested_when_feature_disabled(win_factory, tmp_path):
    w, _ = win_factory(installed=True, settings={"speakers": True}, speakers_enabled=False)
    drop(w, audio_file(tmp_path))
    assert w.runner.started[0].speakers is False


def test_progress_updates_status_and_bar(win_factory, tmp_path):
    w, _ = win_factory(installed=True)
    drop(w, audio_file(tmp_path))
    w.runner.progress.emit(Progress("transcribing", 0.62, 240))
    assert "Přepisuji… 62 % · zbývá cca 4 min" in w.message_text()
    assert w.progress_bar.value() == 62
    w.runner.progress.emit(Progress("diarizing"))
    assert strings.DIARIZING in w.message_text()
    w.runner.progress.emit(Progress("saving"))
    assert strings.SAVING in w.message_text()


def test_done_in_fallback_folder(win_factory, tmp_path):
    w, _ = win_factory(installed=True)
    w.state = State.RUNNING
    w.runner.finished.emit(Done(tmp_path / "a.md", True, 40))
    assert "Hotovo za 40 s" in w.message_text()
    assert strings.SAVED_FALLBACK.format(name="a.md") in w.message_text()


@pytest.mark.parametrize(
    ("code", "expected"),
    [("unreadable", strings.ERR_UNREADABLE), ("no_speech", strings.ERR_NO_SPEECH), ("error", "Něco se nepovedlo")],
)
def test_failure_shows_czech_message_and_accepts_next_drop(win_factory, tmp_path, code, expected):
    w, _ = win_factory(installed=True)
    drop(w, audio_file(tmp_path, "a.m4a"))
    w.runner.running = False
    w.runner.finished.emit(Failed(code, "detail"))
    assert w.state == State.ERROR and expected in w.message_text()
    if code == "error":
        assert strings.ERR_GENERIC.format(log=paths.logs_dir()) in w.message_text().replace("\u200b", "")
    drop(w, audio_file(tmp_path, "b.m4a"))
    assert w.state == State.RUNNING and len(w.runner.started) == 2


def test_cancel_button_returns_to_idle(win_factory, tmp_path):
    w, _ = win_factory(installed=True)
    drop(w, audio_file(tmp_path))
    assert w.cancel_button.isVisible()
    w.cancel_button.click()
    assert w.runner.cancelled and w.state == State.IDLE


def test_open_and_show_use_output_path(win_factory, tmp_path, monkeypatch):
    opened, revealed = [], []
    monkeypatch.setattr(main_window, "open_file", lambda path: opened.append(path) or True)
    monkeypatch.setattr(main_window, "reveal_in_folder", revealed.append)
    w, _ = win_factory(installed=True)
    w.state = State.RUNNING
    w.runner.finished.emit(Done(tmp_path / "a.md", False, 5))
    w.open_button.click()
    w.show_button.click()
    assert opened == [tmp_path / "a.md"] and revealed == [tmp_path / "a.md"]


def test_click_on_drop_area_opens_file_dialog(win_factory, tmp_path, monkeypatch, qtbot):
    audio = audio_file(tmp_path)
    calls = []

    def fake_dialog(parent, title, directory, filt):
        calls.append((title, filt))
        return str(audio), filt

    monkeypatch.setattr(QFileDialog, "getOpenFileName", fake_dialog)
    w, _ = win_factory(installed=True)
    qtbot.mouseClick(w.drop_area, Qt.MouseButton.LeftButton)
    assert calls == [(strings.FILE_DIALOG_TITLE, f"{strings.FILE_FILTER};;{strings.FILE_FILTER_ALL}")]
    assert w.state == State.RUNNING and w.runner.started[0].audio == audio


def test_file_dialog_cancel_does_nothing(win_factory, monkeypatch, qtbot):
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
    w, _ = win_factory(installed=True)
    qtbot.mouseClick(w.drop_area, Qt.MouseButton.LeftButton)
    assert w.state == State.IDLE and w.runner.started == []


def test_drag_and_drop_events_route_to_handle_drop(win_factory, tmp_path):
    w, _ = win_factory(installed=True)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(audio_file(tmp_path)))])
    enter = drag_event(QDragEnterEvent, mime)
    w.dragEnterEvent(enter)
    assert enter.isAccepted()
    ev = drag_event(QDropEvent, mime)
    w.dropEvent(ev)
    assert w.state == State.RUNNING


def test_drag_rejected_while_model_missing(win_factory, tmp_path):
    w, _ = win_factory(installed=False)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(audio_file(tmp_path)))])
    enter = drag_event(QDragEnterEvent, mime)
    w.dragEnterEvent(enter)
    assert not enter.isAccepted()


def test_download_progress_and_success(win_factory, tmp_path):
    w, dl = win_factory(installed=False)
    dl.progress.emit(75_000_000, 3_100_000_000)
    assert w.state == State.DOWNLOADING
    assert "Stahuji model… 2 % (75 MB z 3,1 GB)" in w.message_text()
    install(tmp_path / "models", required_specs(True))
    dl.succeeded.emit()
    assert w.state == State.IDLE


def test_download_space_failure_and_retry(win_factory):
    w, dl = win_factory(installed=False)
    dl.failed.emit("space")
    assert strings.ERR_DOWNLOAD_SPACE in w.message_text()
    w.retry_button.click()
    assert len(w.downloaders) == 2 and w.downloaders[-1].started and w.state == State.DOWNLOADING


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (InvalidModelFolder("x"), strings.ERR_IMPORT_INVALID),
        (ImportFailed("space"), strings.ERR_DOWNLOAD_SPACE),
        (ImportFailed("busy"), strings.ERR_DOWNLOAD_BUSY),
        (ImportFailed("io"), "Něco se nepovedlo"),
    ],
)
def test_import_errors_map_to_messages(win_factory, tmp_path, monkeypatch, qtbot, exc, expected):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))

    def failing(folder, models_dir):
        raise exc

    monkeypatch.setattr(main_window, "import_models_from", failing)
    w, dl = win_factory(installed=False)
    dl.failed.emit("network")
    w.import_button.click()
    qtbot.waitUntil(lambda: w.state == State.NEED_MODEL, timeout=5_000)
    assert expected in w.message_text()


def importing(monkeypatch, tmp_path, specs_installed):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path / "src"))
    imported = []

    def fake_import(folder, models_dir):
        imported.append(Path(folder))
        install(models_dir, specs_installed)
        return [spec.key for spec in specs_installed]

    monkeypatch.setattr(main_window, "import_models_from", fake_import)
    return imported


def test_import_runs_off_the_ui_thread_with_busy_bar(win_factory, tmp_path, monkeypatch, qtbot):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path / "src"))
    release = threading.Event()

    def slow_import(folder, models_dir):
        assert release.wait(10)
        install(models_dir, [whisper_spec()])
        return ["whisper"]

    monkeypatch.setattr(main_window, "import_models_from", slow_import)
    w, dl = win_factory(installed=False, speakers_enabled=False)
    dl.failed.emit("network")
    w.import_button.click()  # returns while the copy is still running
    assert w.state == State.DOWNLOADING and strings.MODEL_IMPORTING in w.message_text()
    assert w.progress_bar.maximum() == 0 and not w.model_row.isVisible()
    release.set()
    qtbot.waitUntil(lambda: w.state == State.IDLE, timeout=5_000)


def test_import_success_without_speakers_goes_idle(win_factory, tmp_path, monkeypatch, qtbot):
    imported = importing(monkeypatch, tmp_path, [whisper_spec()])
    w, dl = win_factory(installed=False, speakers_enabled=False)
    dl.failed.emit("network")
    w.import_button.click()
    qtbot.waitUntil(lambda: w.state == State.IDLE, timeout=5_000)
    assert imported == [tmp_path / "src"]


def test_import_of_whisper_only_goes_idle_and_fetches_speaker_models(win_factory, tmp_path, monkeypatch, qtbot):
    importing(monkeypatch, tmp_path, [whisper_spec()])
    w, dl = win_factory(installed=False, speakers_enabled=True)
    dl.failed.emit("network")
    w.import_button.click()
    qtbot.waitUntil(lambda: w.state == State.IDLE, timeout=5_000)
    assert len(w.downloaders) == 2 and w.downloaders[-1].started
    assert not w.speakers_check.isEnabled()


def test_import_of_whole_models_folder_enables_speakers(win_factory, tmp_path, monkeypatch, qtbot):
    importing(monkeypatch, tmp_path, required_specs(True))
    w, dl = win_factory(installed=False, speakers_enabled=True)
    dl.failed.emit("network")
    w.import_button.click()
    qtbot.waitUntil(lambda: w.state == State.IDLE, timeout=5_000)
    assert len(w.downloaders) == 1 and w.speakers_check.isEnabled()


def test_import_without_whisper_model_keeps_asking(win_factory, tmp_path, monkeypatch, qtbot):
    importing(monkeypatch, tmp_path, diarization_specs())
    w, dl = win_factory(installed=False, speakers_enabled=True)
    dl.failed.emit("network")
    w.import_button.click()
    qtbot.waitUntil(lambda: w.state == State.NEED_MODEL, timeout=5_000)
    assert strings.ERR_IMPORT_INVALID in w.message_text()


def test_import_dialog_cancel_keeps_state(win_factory, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "")
    w, dl = win_factory(installed=False)
    dl.failed.emit("network")
    w.import_button.click()
    assert w.state == State.NEED_MODEL and strings.ERR_DOWNLOAD_NETWORK in w.message_text()


def test_layout_is_applied_synchronously_on_state_change(win_factory, tmp_path):
    w, _ = win_factory(installed=True)
    drop(w, audio_file(tmp_path))
    w.runner.running = False
    w.runner.finished.emit(Done(tmp_path / "a.md", False, 5))
    w.state = State.RUNNING
    w.runner.finished.emit(Failed("error"))
    # no event processing: the new state must not paint with the previous layout
    label = w.message_label
    assert label.height() >= label.heightForWidth(label.width())
    assert w.drop_area.height() <= w.drop_area.sizeHint().height()


@pytest.mark.model
def test_real_runner_transcribes_dropped_file(qtbot, tmp_path, whisper_dir, monkeypatch):
    """End to end without Finder: real JobRunner, real worker process, tiny model."""
    import shutil

    from voice2text.gui.runner import JobRunner

    monkeypatch.setenv("VOICE2TEXT_WHISPER_MODEL", whisper_dir.name.removeprefix("whisper-"))
    audio = tmp_path / "Porada 3.11.m4a"
    shutil.copy(Path(__file__).parent / "fixtures" / "cs_sample.m4a", audio)
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    from tests import fakes

    runner = JobRunner(target=fakes.worker_outside_temp_rule)  # the real worker_main inside
    w = MainWindow(runner, FakeDownloader, settings, whisper_dir.parent, False)
    qtbot.addWidget(w)
    w.show()
    assert w.state == State.IDLE
    drop(w, audio)
    assert w.state == State.RUNNING and "Porada 3.11.m4a" in w.file_label.text()
    qtbot.waitUntil(lambda: w.state != State.RUNNING, timeout=120_000)
    assert w.state == State.DONE, w.message_text()
    assert strings.SAVED_NEXT_TO.format(name="Porada 3.11.md") in w.message_text()
    assert (tmp_path / "Porada 3.11.md").read_text(encoding="utf-8").startswith("# Porada 3.11")


def test_runner_start_failure_shows_error_and_allows_next_drop(win_factory, tmp_path):
    w, _ = win_factory(installed=True)

    def broken_start(req):
        raise OSError("spawn failed")

    w.runner.start = broken_start
    drop(w, audio_file(tmp_path))
    assert w.state == State.ERROR and "Něco se nepovedlo" in w.message_text()
    del w.runner.start
    drop(w, audio_file(tmp_path, "b.m4a"))
    assert w.state == State.RUNNING and len(w.runner.started) == 1


def test_drag_without_urls_is_accepted_and_drop_shows_not_file_hint(win_factory):
    w, _ = win_factory(installed=True)
    mime = QMimeData()
    mime.setData("application/octet-stream", b"x")  # e.g. an Outlook attachment or a file promise
    enter = drag_event(QDragEnterEvent, mime)
    w.dragEnterEvent(enter)
    assert enter.isAccepted()
    w.dropEvent(drag_event(QDropEvent, mime))
    assert w.state == State.IDLE and strings.ERR_NOT_FILE in w.message_text()
    assert w.runner.started == []


def test_cancel_after_worker_finished_shows_done(win_factory, tmp_path):
    w, _ = win_factory(installed=True)
    drop(w, audio_file(tmp_path))

    def cancel_finds_done():
        w.runner.running = False
        w.runner.finished.emit(Done(tmp_path / "a.md", False, 5))

    w.runner.cancel = cancel_finds_done
    w.cancel_button.click()
    assert w.state == State.DONE and "Hotovo za 5 s" in w.message_text()


def test_message_labels_render_plain_text(win_factory):
    w, _ = win_factory(installed=True)
    assert w.message_label.textFormat() == Qt.TextFormat.PlainText
    assert w.detail_label.textFormat() == Qt.TextFormat.PlainText


def test_real_download_runner_drives_window_from_its_thread(qtbot, tmp_path):
    """Signals queued from the download thread reach the window (sender check included)."""
    from voice2text.gui.runner import DownloadRunner

    models_dir = tmp_path / "models"

    def fake_download(spec, target, on_progress, should_cancel):
        on_progress(50, 100)
        on_progress(100, 100)
        install(target, [spec])

    factory = lambda: DownloadRunner(required_specs(True), models_dir, download_fn=fake_download)  # noqa: E731
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    w = MainWindow(FakeRunner(), factory, settings, models_dir, True)
    qtbot.addWidget(w)
    w.show()
    qtbot.waitUntil(lambda: w.state == State.IDLE, timeout=5_000)
    assert w.speakers_check.isEnabled()


# --- speaker failure note and diarization progress ---------------------------------


def test_done_after_speaker_failure_adds_note(win_factory, tmp_path):
    w, _ = win_factory(installed=True)
    w.state = State.RUNNING
    w.runner.finished.emit(Done(tmp_path / "a.md", False, 60, speakers_failed=True))
    assert w.state == State.DONE
    assert "Hotovo za 1 min\n" + strings.SPEAKERS_FAILED in w.message_text()
    assert strings.SAVED_NEXT_TO.format(name="a.md") in w.message_text()


def test_done_without_speaker_failure_has_no_note(win_factory, tmp_path):
    w, _ = win_factory(installed=True)
    w.state = State.RUNNING
    w.runner.finished.emit(Done(tmp_path / "a.md", False, 60))
    assert strings.SPEAKERS_FAILED not in w.message_text()


def test_diarizing_progress_shows_percent_and_eta(win_factory, tmp_path):
    w, _ = win_factory(installed=True)
    drop(w, audio_file(tmp_path))
    w.runner.progress.emit(Progress("diarizing"))
    assert strings.DIARIZING in w.message_text() and w.progress_bar.maximum() == 0
    w.runner.progress.emit(Progress("diarizing", 0.4, 90))
    assert "Rozlišuji mluvčí… 40 % · zbývá cca 2 min" in w.message_text()
    assert w.progress_bar.maximum() == 100 and w.progress_bar.value() == 40
    w.runner.progress.emit(Progress("diarizing", 0.45, None))
    assert w.status_label.text() == "Rozlišuji mluvčí… 45 %"


# --- manual import offered during the first-run download ------------------------------


def test_import_button_offered_during_first_run_download(win_factory):
    w, dl = win_factory(installed=False)
    assert w.state == State.DOWNLOADING and dl.started
    assert w.import_button.isVisible() and not w.retry_button.isVisible()


def test_import_during_download_cancels_it_first_and_ignores_its_late_signal(win_factory, tmp_path, monkeypatch, qtbot):
    release = threading.Event()

    def slow_import(folder, models_dir):
        assert release.wait(10)
        install(models_dir, [whisper_spec()])
        return ["whisper"]

    monkeypatch.setattr(main_window, "import_models_from", slow_import)
    w, dl = win_factory(installed=False, speakers_enabled=False)
    cancelled_when_picking = []

    def picker(*a, **k):
        cancelled_when_picking.append(dl.cancelled)
        return str(tmp_path / "src")

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", picker)
    w.import_button.click()
    assert cancelled_when_picking == [True]
    assert w.state == State.DOWNLOADING and strings.MODEL_IMPORTING in w.message_text()
    assert not w.import_button.isVisible()
    dl.failed.emit("cancelled")  # the stopped download reports late: must not reset the window
    assert w.state == State.DOWNLOADING and strings.MODEL_IMPORTING in w.message_text()
    release.set()
    qtbot.waitUntil(lambda: w.state == State.IDLE, timeout=5_000)


def test_import_picker_cancelled_during_download_offers_retry(win_factory, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "")
    w, dl = win_factory(installed=False)
    w.import_button.click()
    assert dl.cancelled and w.state == State.NEED_MODEL and w.message_label.text() == ""
    assert w.retry_button.isVisible() and w.import_button.isVisible()
    w.retry_button.click()
    assert len(w.downloaders) == 2 and w.downloaders[-1].started and w.state == State.DOWNLOADING


# --- partial models-folder import ------------------------------------------------------


def test_partial_import_with_whisper_installed_leaves_the_window_usable(win_factory, tmp_path, monkeypatch, qtbot):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path / "src"))

    def partial(folder, models_dir):
        install(models_dir, [whisper_spec()])  # whisper copied, then the speaker models failed
        raise ImportFailed("io")

    monkeypatch.setattr(main_window, "import_models_from", partial)
    w, dl = win_factory(installed=False, speakers_enabled=True)
    dl.failed.emit("network")
    w.import_button.click()
    qtbot.waitUntil(lambda: w.state == State.IDLE, timeout=5_000)
    assert "Něco se nepovedlo" in w.message_text() and w.drop_area.isVisible()
    assert not w.model_row.isVisible()
    assert w.downloaders[-1] is not dl and w.downloaders[-1].started  # speaker models fetched quietly


# --- "Otevřít přepis" when no app opens the file ---------------------------------------


def test_open_falls_back_to_reveal_when_nothing_opens_the_file(win_factory, tmp_path, monkeypatch):
    revealed = []
    monkeypatch.setattr(main_window, "open_file", lambda path: False)
    monkeypatch.setattr(main_window, "reveal_in_folder", revealed.append)
    w, _ = win_factory(installed=True)
    w.state = State.RUNNING
    w.runner.finished.emit(Done(tmp_path / "a.md", False, 5))
    w.open_button.click()
    assert revealed == [tmp_path / "a.md"]


def test_open_file_reports_whether_it_opened(monkeypatch, tmp_path):
    from PySide6.QtGui import QDesktopServices

    urls = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: urls.append(url) or False)
    assert osutils.open_file(tmp_path / "a.md") is False
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: True)
    assert osutils.open_file(tmp_path / "a.md") is True
    assert urls[0].toLocalFile() == str(tmp_path / "a.md").replace("\\", "/")


# --- recording language -----------------------------------------------------------


def test_language_combo_lists_auto_then_top_languages(win_factory):
    from voice2text import languages

    w, _ = win_factory(installed=True)
    items = [(w.language_combo.itemData(i), w.language_combo.itemText(i)) for i in range(w.language_combo.count())]
    assert items == languages.choices()
    assert items[0][0] == "auto" and items[1][0] == "cs"
    assert w.language_label.text() == strings.AUDIO_LANGUAGE
    assert w.language_combo.isVisibleTo(w)


def test_language_first_run_uses_default_choice(win_factory, monkeypatch):
    from voice2text import languages

    monkeypatch.setattr(languages, "default_choice", lambda: "de")
    w, _ = win_factory(installed=True)  # true first run: no settings at all
    assert w.language_combo.currentData() == "de"
    drop(w, audio_file(w.models_dir.parent))
    assert w.runner.started[0].language == "de"


def test_language_upgrade_from_v01_defaults_to_czech(win_factory, monkeypatch):
    from voice2text import languages

    monkeypatch.setattr(languages, "default_choice", lambda: "de")
    for v01 in ({"format": "txt"}, {"speakers": True}, {"speaker_count": 3}):
        w, _ = win_factory(installed=True, settings=v01)  # v0.1 was Czech-only
        assert w.language_combo.currentData() == "cs"
    w, _ = win_factory(installed=True, settings={"format": "txt", "audio_language": "pl"})
    assert w.language_combo.currentData() == "pl"


def test_language_unsupported_system_defaults_to_auto(win_factory, monkeypatch):
    from voice2text import languages

    monkeypatch.setattr(languages, "default_choice", lambda: "auto")
    w, _ = win_factory(installed=True)
    assert w.language_combo.currentData() == "auto"


def test_saved_language_beats_default_and_unknown_saved_falls_back(win_factory, monkeypatch):
    from voice2text import languages

    monkeypatch.setattr(languages, "default_choice", lambda: "de")
    w, _ = win_factory(installed=True, settings={"audio_language": "sk"})
    assert w.language_combo.currentData() == "sk"
    w, _ = win_factory(installed=True, settings={"audio_language": "klingon"})
    assert w.language_combo.currentData() == "de"


def test_language_persists_and_reaches_request(win_factory, tmp_path):
    w, _ = win_factory(installed=True)
    w.language_combo.setCurrentIndex(w.language_combo.findData("pl"))
    assert w.settings.value("audio_language") == "pl"
    w2, _ = win_factory(installed=True, reuse_settings=w.settings)
    assert w2.language_combo.currentData() == "pl"
    drop(w2, audio_file(tmp_path))
    assert w2.runner.started[0].language == "pl"


def test_detected_language_shown_only_when_reported(win_factory, tmp_path):
    w, _ = win_factory(installed=True)
    drop(w, audio_file(tmp_path))
    w.runner.finished.emit(Done(tmp_path / "a.md", False, 5, detected_language="de"))
    assert strings.DETECTED_LANGUAGE.format(name="Němčina") in w.message_text()
    w, _ = win_factory(installed=True)
    drop(w, audio_file(tmp_path))
    w.runner.finished.emit(Done(tmp_path / "a.md", False, 5))
    assert "Rozpoznaný jazyk" not in w.message_text()


def test_language_row_follows_the_choosing_states(win_factory, tmp_path):
    w, _ = win_factory(installed=True)
    drop(w, audio_file(tmp_path))
    assert w.state == State.RUNNING and not w.language_row.isVisibleTo(w)
    w.runner.finished.emit(Done(tmp_path / "a.md", False, 5))
    assert w.language_row.isVisibleTo(w)


def test_language_row_hidden_while_model_is_missing(win_factory):
    w, _ = win_factory(installed=False)
    assert not w.language_row.isVisibleTo(w)


def test_window_stays_compact_in_both_languages(win_factory):
    w, _ = win_factory(installed=True)
    assert w.width() == main_window.WIDTH and w.height() <= 480
