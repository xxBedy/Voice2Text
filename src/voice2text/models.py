"""Model registry, resumable download and manual import.

The model download is the only network access the app ever makes.
"""

from __future__ import annotations

import errno
import http.client
import os
import shutil
import ssl
import sys
import tarfile
import time
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import certifi

CHUNK = 1024 * 1024
SPACE_MARGIN = 200 * 1024 * 1024
COMPLETE_MARKER = ".complete"
TIMEOUT = 30
REPLACE_ATTEMPTS = 5
REPLACE_DELAY_S = 0.5

_HF = "https://huggingface.co/Systran/faster-whisper-{name}/resolve/main/{file}"
_WHISPER_FILES: dict[str, tuple[str, ...]] = {
    "large-v3": ("config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json"),
    "tiny": ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt"),
}


@dataclass(frozen=True)
class RemoteFile:
    url: str
    relpath: str
    archive: Literal["tar.bz2"] | None = None


@dataclass(frozen=True)
class ModelSpec:
    key: str
    subdir: str
    files: tuple[RemoteFile, ...]


class DownloadError(Exception):
    def __init__(self, code: Literal["network", "space", "cancelled", "busy", "corrupt"]):
        super().__init__(code)
        self.code = code


class InvalidModelFolder(Exception):
    pass


class ImportFailed(Exception):
    def __init__(self, code: Literal["space", "io", "busy"]):
        super().__init__(code)
        self.code = code


def whisper_spec() -> ModelSpec:
    name = os.environ.get("VOICE2TEXT_WHISPER_MODEL") or "large-v3"
    files = _WHISPER_FILES.get(name)
    if files is None:
        allowed = ", ".join(_WHISPER_FILES)
        raise ValueError(f"Unknown VOICE2TEXT_WHISPER_MODEL {name!r}; allowed values: {allowed}")
    return ModelSpec(
        key="whisper",
        subdir=f"whisper-{name}",
        files=tuple(RemoteFile(_HF.format(name=name, file=f), f) for f in files),
    )


_SHERPA = "https://github.com/k2-fsa/sherpa-onnx/releases/download"


def diarization_specs() -> list[ModelSpec]:
    return [
        ModelSpec(
            key="diar-segmentation",
            subdir="diar-segmentation",
            files=(
                RemoteFile(
                    f"{_SHERPA}/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
                    "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
                    "tar.bz2",
                ),
            ),
        ),
        ModelSpec(
            key="diar-embedding",
            subdir="diar-embedding",
            files=(
                RemoteFile(
                    f"{_SHERPA}/speaker-recongition-models/wespeaker_en_voxceleb_resnet34.onnx",
                    "wespeaker_en_voxceleb_resnet34.onnx",
                ),
            ),
        ),
    ]


def required_specs(speakers_enabled: bool) -> list[ModelSpec]:
    specs = [whisper_spec()]
    if speakers_enabled:
        specs += diarization_specs()
    return specs


def model_path(spec: ModelSpec, models_dir: Path) -> Path:
    return Path(models_dir) / spec.subdir


def is_installed(spec: ModelSpec, models_dir: Path) -> bool:
    return (model_path(spec, models_dir) / COMPLETE_MARKER).exists()


class _LockBusy(Exception):
    pass


@contextmanager
def _model_lock(spec: ModelSpec, models_dir: Path) -> Iterator[None]:
    """Exclusive, non-blocking, cross-process lock for one model directory.

    Released by the OS when the process dies, so a crash never leaves a stale lock.
    Raises _LockBusy when another process holds it.
    """
    models_dir = Path(models_dir)
    models_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(models_dir / f"{spec.subdir}.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            if sys.platform == "win32":
                import msvcrt

                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            if e.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise _LockBusy from e
            raise
        try:
            yield
        finally:
            try:
                if sys.platform == "win32":
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        os.close(fd)


_ssl_ctx: ssl.SSLContext | None = None


def _ssl_context() -> ssl.SSLContext:
    """Trust the OS store (corporate TLS inspection roots live there) and certifi
    (frozen builds may lack OpenSSL's default CA paths). Built once."""
    global _ssl_ctx
    if _ssl_ctx is None:
        ctx = ssl.create_default_context()
        ctx.load_verify_locations(cafile=certifi.where())
        _ssl_ctx = ctx
    return _ssl_ctx


def _open(url: str, method: str = "GET", headers: dict[str, str] | None = None):
    req = urllib.request.Request(url, method=method, headers=headers or {})
    return urllib.request.urlopen(req, timeout=TIMEOUT, context=_ssl_context())


def _remote_size(url: str) -> int:
    with _open(url, "HEAD") as r:
        length = r.headers.get("Content-Length")
    if length is None:
        raise DownloadError("network")
    return int(length)


def _extract(archive: Path, dest: Path) -> None:
    with tarfile.open(archive, "r:bz2") as tf:
        tf.extractall(dest, filter="data")


def _fetch_file(
    f: RemoteFile,
    root: Path,
    total_size: int,
    advance: Callable[[int], None],
    should_cancel: Callable[[], bool],
) -> None:
    final = root / f.relpath
    part = root / (f.relpath + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    have = part.stat().st_size if part.exists() else 0
    if have > total_size:
        part.unlink()
        have = 0
    if have < total_size:
        headers = {"Range": f"bytes={have}-"} if have else {}
        with _open(f.url, headers=headers) as r:
            if have and getattr(r, "status", 200) != 206:
                have = 0  # server ignored Range: restart this file
            advance(have)
            with open(part, "ab" if have else "wb") as out:
                while True:
                    if should_cancel():
                        raise DownloadError("cancelled")
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    advance(len(chunk))
    else:
        advance(have)
    if part.stat().st_size != total_size:
        part.unlink(missing_ok=True)  # corrupt (e.g. written by two processes): never resume from it
        raise DownloadError("corrupt")
    os.replace(part, final)


def download(
    spec: ModelSpec,
    models_dir: Path,
    on_progress: Callable[[int, int], None],
    should_cancel: Callable[[], bool] = lambda: False,
) -> None:
    try:
        with _model_lock(spec, models_dir):
            _download_locked(spec, models_dir, on_progress, should_cancel)
    except _LockBusy as e:
        raise DownloadError("busy") from e
    except OSError as e:
        if e.errno == errno.ENOSPC:
            raise DownloadError("space") from e
        raise DownloadError("network") from e


def _download_locked(
    spec: ModelSpec,
    models_dir: Path,
    on_progress: Callable[[int, int], None],
    should_cancel: Callable[[], bool],
) -> None:
    root = model_path(spec, models_dir)
    try:
        root.mkdir(parents=True, exist_ok=True)
        if should_cancel():
            raise DownloadError("cancelled")

        pending: list[tuple[RemoteFile, int]] = []
        total = 0
        done = 0
        for f in spec.files:
            if (root / (f.relpath + ".done")).exists() or (not f.archive and (root / f.relpath).exists()):
                size = (root / f.relpath).stat().st_size if (root / f.relpath).exists() else 0
                if f.archive:
                    size = 0  # archive was consumed; nothing left to do
                total += size
                done += size
                continue
            size = _remote_size(f.url)
            total += size
            part = root / (f.relpath + ".part")
            have = min(part.stat().st_size, size) if part.exists() else 0
            done += have
            pending.append((f, size))

        remaining = total - done
        if shutil.disk_usage(root).free < remaining + SPACE_MARGIN:
            raise DownloadError("space")

        base = total - sum(s for _, s in pending)
        on_progress(done, total)
        for f, size in pending:
            if should_cancel():
                raise DownloadError("cancelled")
            counted = 0

            def advance(n: int, _base=base) -> None:
                nonlocal counted
                counted += n
                on_progress(_base + counted, total)

            _fetch_file(f, root, size, advance, should_cancel)
            base += size
            if f.archive == "tar.bz2":
                _extract(root / f.relpath, root)
                (root / f.relpath).unlink()
                (root / (f.relpath + ".done")).write_bytes(b"")
        on_progress(total, total)
        (root / COMPLETE_MARKER).write_bytes(b"")
    except DownloadError:
        raise
    except (OSError, ValueError, tarfile.TarError, http.client.HTTPException) as e:  # URLError is an OSError
        if isinstance(e, OSError) and e.errno == errno.ENOSPC:
            raise DownloadError("space") from e
        raise DownloadError("network") from e


def _locked_import(spec: ModelSpec, models_dir: Path, fn: Callable[[], None]) -> None:
    try:
        with _model_lock(spec, models_dir):
            fn()
    except _LockBusy as e:
        raise ImportFailed("busy") from e
    except OSError as e:
        raise ImportFailed("space" if e.errno == errno.ENOSPC else "io") from e


def import_whisper_from(folder: Path, models_dir: Path) -> None:
    folder = Path(folder)
    spec = whisper_spec()
    vocab = {"vocabulary.json", "vocabulary.txt"}
    required = [f.relpath for f in spec.files if f.relpath not in vocab]
    has_vocab = any((folder / v).is_file() for v in vocab)
    if not folder.is_dir() or not all((folder / n).is_file() for n in required) or not has_vocab:
        raise InvalidModelFolder(str(folder))
    _locked_import(spec, models_dir, lambda: _import_whisper_locked(folder, spec, models_dir))


def _import_whisper_locked(folder: Path, spec: ModelSpec, models_dir: Path) -> None:
    dest = model_path(spec, models_dir)
    tmp = dest.with_name(dest.name + ".importing")
    try:
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        for item in folder.iterdir():
            if item.is_file():
                shutil.copy2(item, tmp / item.name)
        (tmp / COMPLETE_MARKER).write_bytes(b"")
        if dest.exists():
            shutil.rmtree(dest)
        _replace_with_retry(tmp, dest)
    except OSError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ImportFailed("space" if e.errno == errno.ENOSPC else "io") from e


def _replace_with_retry(src: Path, dest: Path) -> None:
    """os.replace, retried while Windows antivirus still holds the freshly copied files."""
    for attempt in range(1, REPLACE_ATTEMPTS + 1):
        try:
            os.replace(src, dest)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS:
                raise
            time.sleep(REPLACE_DELAY_S)


def _raise(error: OSError) -> None:
    raise error


def _copy_tree_atomic(src: Path, dest: Path) -> None:
    """Copy an installed model folder (nested files included) and swap it in at once."""
    tmp = dest.with_name(dest.name + ".importing")
    try:
        if tmp.exists():
            shutil.rmtree(tmp)
        for root, _dirs, files in os.walk(src, onerror=_raise):
            target = tmp / Path(root).relative_to(src)
            target.mkdir(parents=True, exist_ok=True)
            for name in files:
                if not name.endswith(".part"):
                    shutil.copy2(Path(root) / name, target / name)
        (tmp / COMPLETE_MARKER).write_bytes(b"")
        if dest.exists():
            shutil.rmtree(dest)
        _replace_with_retry(tmp, dest)
    except OSError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ImportFailed("space" if e.errno == errno.ENOSPC else "io") from e


def import_models_from(folder: Path, models_dir: Path) -> list[str]:
    """Import a Whisper model folder, or a copied `models` folder holding installed models.

    A `models` folder is recognised by subfolders named like the known specs that carry a
    `.complete` marker; each of them is imported. Returns the imported spec keys and raises
    InvalidModelFolder when nothing usable is there.
    """
    folder = Path(folder)
    whisper = whisper_spec()
    found = [s for s in [whisper, *diarization_specs()] if (folder / s.subdir / COMPLETE_MARKER).is_file()]
    if not found:
        import_whisper_from(folder, models_dir)
        return [whisper.key]
    for spec in found:
        if spec.key == whisper.key:
            import_whisper_from(folder / spec.subdir, models_dir)
        else:
            _locked_import(
                spec, models_dir,
                lambda spec=spec: _copy_tree_atomic(folder / spec.subdir, model_path(spec, models_dir)),
            )
    return [spec.key for spec in found]
