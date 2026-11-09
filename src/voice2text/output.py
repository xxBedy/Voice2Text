import os
import sys
import tempfile
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from voice2text import strings
from voice2text.segments import Paragraph

Format = Literal["md", "txt"]


def render(paragraphs: list[Paragraph], title: str, fmt: Format) -> str:
    blocks: list[str] = []
    if fmt == "md":
        blocks.append("# " + unicodedata.normalize("NFC", title))
    for p in paragraphs:
        if p.speaker is None:
            blocks.append(p.text)
        else:
            label = strings.SPEAKER_LABEL.format(n=p.speaker)
            if fmt == "md":
                label = f"**{label}**"
            blocks.append(f"{label} {p.text}")
    return "\n\n".join(blocks) + "\n"


def target_path(audio: Path, fmt: Format, directory: Path | None = None) -> Path:
    folder = directory or audio.parent
    stem = audio.stem
    candidate = folder / f"{stem}.{fmt}"
    n = 2
    while candidate.exists():
        candidate = folder / f"{stem} ({n}).{fmt}"
        n += 1
    return candidate


def write_atomic(text: str, path: Path, newline: str = os.linesep) -> None:
    part = path.with_name(path.name + ".part")
    try:
        with open(part, "w", encoding="utf-8", newline=newline) as f:
            f.write(text)
        os.replace(part, path)
    except BaseException:
        part.unlink(missing_ok=True)
        raise


def temp_roots() -> list[Path]:
    """Folders the system may clean up: a transcript saved there could silently vanish."""
    roots = [Path(tempfile.gettempdir())]
    if sys.platform == "darwin":
        roots.append(Path("/private/var/folders"))  # per-user temp and caches, also under /var
    return roots


def in_temp_folder(folder: Path, roots: Iterable[Path] | None = None) -> bool:
    try:
        resolved = Path(folder).resolve()
    except OSError:
        return False
    for root in temp_roots() if roots is None else roots:
        try:
            if resolved.is_relative_to(Path(root).resolve()):
                return True
        except OSError:
            continue
    return False


def save(text: str, audio: Path, fmt: Format, fallback_dir: Path) -> tuple[Path, bool]:
    """Save next to the recording; use the fallback folder when that folder is not writable
    or is a temp folder (an opened attachment, an unpacked archive)."""
    if not in_temp_folder(audio.parent):
        path = target_path(audio, fmt)
        try:
            write_atomic(text, path)
            return path, False
        except OSError:
            pass
    fallback_dir.mkdir(parents=True, exist_ok=True)
    path = target_path(audio, fmt, fallback_dir)
    write_atomic(text, path)
    return path, True
