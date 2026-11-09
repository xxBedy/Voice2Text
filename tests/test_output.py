import os
import sys
import unicodedata

import pytest

from voice2text.segments import Paragraph
from voice2text.output import render, target_path, write_atomic, save

P = Paragraph

def test_render_md_has_title_and_blank_line_separated_paragraphs():
    assert render([P("A.", None), P("B.", None)], "Porada 3.11", "md") == "# Porada 3.11\n\nA.\n\nB.\n"

def test_render_txt_has_no_title():
    assert render([P("A.", None), P("B.", None)], "x", "txt") == "A.\n\nB.\n"

def test_render_speakers_md_bold_txt_plain():
    ps = [P("Ahoj.", 1), P("Čau.", 2)]
    assert render(ps, "t", "md") == "# t\n\n**Mluvčí 1:** Ahoj.\n\n**Mluvčí 2:** Čau.\n"
    assert render(ps, "t", "txt") == "Mluvčí 1: Ahoj.\n\nMluvčí 2: Čau.\n"

def test_render_title_is_nfc():
    nfd = unicodedata.normalize("NFD", "Porada č. 3 – úterý")
    out = render([P("x", None)], nfd, "md")
    assert out.splitlines()[0] == "# " + unicodedata.normalize("NFC", nfd)

def test_target_path_same_stem_and_collision_numbering(tmp_path):
    audio = tmp_path / "Porada č. 3 – úterý.m4a"
    audio.write_bytes(b"x")
    assert target_path(audio, "md") == tmp_path / "Porada č. 3 – úterý.md"
    (tmp_path / "Porada č. 3 – úterý.md").write_text("old")
    (tmp_path / "Porada č. 3 – úterý (2).md").write_text("old")
    assert target_path(audio, "md") == tmp_path / "Porada č. 3 – úterý (3).md"

def test_write_atomic_uses_given_newline_without_bom(tmp_path):
    p = tmp_path / "a.txt"
    write_atomic("a\n\nb\n", p, newline="\r\n")
    assert p.read_bytes() == b"a\r\n\r\nb\r\n"
    assert not (tmp_path / "a.txt.part").exists()

def test_save_never_overwrites(tmp_path):
    audio = tmp_path / "x.m4a"; audio.write_bytes(b"x")
    (tmp_path / "x.md").write_text("keep")
    path, fell_back = save("new\n", audio, "md", tmp_path / "fb")
    assert path.name == "x (2).md" and not fell_back
    assert (tmp_path / "x.md").read_text() == "keep"

@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="chmod does not block writes here",
)
def test_save_falls_back_when_folder_not_writable(tmp_path):
    audio = tmp_path / "ro" / "x.m4a"; audio.parent.mkdir(); audio.write_bytes(b"x")
    audio.parent.chmod(0o500)
    try:
        path, fell_back = save("t\n", audio, "txt", tmp_path / "Přepisy")
        assert fell_back and path == tmp_path / "Přepisy" / "x.txt"
    finally:
        audio.parent.chmod(0o700)


# --- recordings in a temp folder (opened attachments, unzipped archives) -----------------

from pathlib import Path  # noqa: E402

from voice2text import output  # noqa: E402

real_temp_roots = output.temp_roots  # conftest swaps it for a hermetic folder in every test


def test_recording_in_system_temp_folder_goes_to_fallback_even_if_writable(tmp_path, monkeypatch):
    system_temp = tmp_path / "systmp"
    audio = system_temp / "Outlook" / "porada.m4a"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"x")
    monkeypatch.setattr(output, "temp_roots", real_temp_roots)
    monkeypatch.setattr(output.tempfile, "gettempdir", lambda: str(system_temp))
    path, fell_back = save("t\n", audio, "md", tmp_path / "Přepisy")
    assert fell_back and path == tmp_path / "Přepisy" / "porada.md"
    assert not (audio.parent / "porada.md").exists()


def test_temp_roots_are_gettempdir_plus_var_folders_on_macos(monkeypatch):
    monkeypatch.setattr(output.tempfile, "gettempdir", lambda: "/some/tmp")
    monkeypatch.setattr(output.sys, "platform", "darwin")
    assert real_temp_roots() == [Path("/some/tmp"), Path("/private/var/folders")]
    monkeypatch.setattr(output.sys, "platform", "win32")
    assert real_temp_roots() == [Path("/some/tmp")]


def test_in_temp_folder_compares_resolved_paths(tmp_path):
    root = tmp_path / "systmp"
    (root / "a" / "b").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    assert output.in_temp_folder(root, [root])
    assert output.in_temp_folder(root / "a" / "b", [root])
    assert output.in_temp_folder(root / "a" / ".." / "a", [root])
    assert not output.in_temp_folder(tmp_path / "elsewhere", [root])
    assert not output.in_temp_folder(tmp_path / "systmp-other", [root])  # prefix of the name is not inside


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_in_temp_folder_follows_symlinks(tmp_path):
    root = tmp_path / "systmp"
    (root / "sub").mkdir(parents=True)
    (tmp_path / "link").symlink_to(root / "sub")
    (tmp_path / "rootlink").symlink_to(root)
    assert output.in_temp_folder(tmp_path / "link", [root])  # recording reached through a link
    assert output.in_temp_folder(root / "sub", [tmp_path / "rootlink"])  # like /var -> /private/var


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS temp layout")
def test_macos_var_folders_counts_as_temp():
    assert output.in_temp_folder(Path("/var/folders/ab/cdef/T/Outlook"), [Path("/private/var/folders")])
