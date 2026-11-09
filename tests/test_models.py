import errno
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import os
import pytest

from voice2text.models import (
    DownloadError,
    ImportFailed,
    InvalidModelFolder,
    ModelSpec,
    RemoteFile,
    download,
    import_models_from,
    import_whisper_from,
    is_installed,
    model_path,
    required_specs,
    whisper_spec,
)


@pytest.fixture
def server():
    files = {"/a.bin": os.urandom(100_000), "/b.json": b'{"k": 1}'}
    range_headers: dict[str, str] = {}
    counts: dict[str, int] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _serve(self, head: bool):
            data = files.get(self.path)
            if data is None:
                self.send_error(404)
                return
            counts[self.path] = counts.get(self.path, 0) + 1
            rng = self.headers.get("Range")
            if rng:
                range_headers[self.path] = rng
                start = int(rng.split("=")[1].rstrip("-"))
                data = data[start:]
                self.send_response(206)
            else:
                self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if not head:
                self.wfile.write(data)

        def do_GET(self):
            self._serve(False)

        def do_HEAD(self):
            self._serve(True)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield SimpleNamespace(
        files=files, range_headers=range_headers, counts=counts,
        base=f"http://127.0.0.1:{httpd.server_address[1]}",
    )
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def spec(server):
    return ModelSpec(
        "test", "test-model",
        (RemoteFile(server.base + "/a.bin", "a.bin"), RemoteFile(server.base + "/b.json", "b.json")),
    )


@pytest.fixture
def spec_unreachable():
    return ModelSpec("t", "t-model", (RemoteFile("http://127.0.0.1:9/a.bin", "a.bin"),))


def test_download_installs_files_and_marker(server, spec, tmp_path):
    seen = []
    download(spec, tmp_path, lambda done, total: seen.append((done, total)))
    root = model_path(spec, tmp_path)
    assert (root / "a.bin").read_bytes() == server.files["/a.bin"]
    assert is_installed(spec, tmp_path)
    assert seen[-1][0] == seen[-1][1] == sum(len(b) for b in server.files.values())


def test_partial_download_is_not_installed_and_resumes_with_range(server, spec, tmp_path):
    root = model_path(spec, tmp_path)
    root.mkdir(parents=True)
    (root / "a.bin.part").write_bytes(server.files["/a.bin"][:40_000])
    assert not is_installed(spec, tmp_path)
    download(spec, tmp_path, lambda d, t: None)
    assert server.range_headers["/a.bin"] == "bytes=40000-"
    assert (root / "a.bin").read_bytes() == server.files["/a.bin"]


def test_cancel_leaves_no_marker(server, spec, tmp_path):
    with pytest.raises(DownloadError) as e:
        download(spec, tmp_path, lambda d, t: None, should_cancel=lambda: True)
    assert e.value.code == "cancelled" and not is_installed(spec, tmp_path)


def test_unreachable_server_is_network_error(spec_unreachable, tmp_path):
    with pytest.raises(DownloadError) as e:
        download(spec_unreachable, tmp_path, lambda d, t: None)
    assert e.value.code == "network"


def test_not_enough_space(server, spec, tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.disk_usage", lambda p: shutil._ntuple_diskusage(10**9, 10**9, 1000))
    with pytest.raises(DownloadError) as e:
        download(spec, tmp_path, lambda d, t: None)
    assert e.value.code == "space"


def test_import_valid_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("VOICE2TEXT_WHISPER_MODEL", raising=False)
    src = tmp_path / "src"
    src.mkdir()
    for f in ["config.json", "model.bin", "tokenizer.json", "vocabulary.json", "preprocessor_config.json"]:
        (src / f).write_bytes(b"x")
    import_whisper_from(src, tmp_path / "models")
    assert is_installed(whisper_spec(), tmp_path / "models")


def test_import_rejects_folder_without_model_bin(tmp_path):
    (tmp_path / "config.json").write_text("{}")
    with pytest.raises(InvalidModelFolder):
        import_whisper_from(tmp_path, tmp_path / "models")


def test_whisper_spec_default_and_env(monkeypatch):
    monkeypatch.delenv("VOICE2TEXT_WHISPER_MODEL", raising=False)
    s = whisper_spec()
    assert s.subdir == "whisper-large-v3"
    assert {f.relpath for f in s.files} == {
        "config.json", "model.bin", "preprocessor_config.json", "tokenizer.json", "vocabulary.json"}
    assert all(f.url == f"https://huggingface.co/Systran/faster-whisper-large-v3/resolve/main/{f.relpath}"
               for f in s.files)
    monkeypatch.setenv("VOICE2TEXT_WHISPER_MODEL", "tiny")
    assert whisper_spec().subdir == "whisper-tiny"
    assert required_specs(False) == [whisper_spec()]


def _make_src(tmp_path, names=("config.json", "model.bin", "tokenizer.json", "vocabulary.json", "preprocessor_config.json")):
    src = tmp_path / "src"
    src.mkdir()
    for f in names:
        (src / f).write_bytes(b"x")
    return src


def test_import_requires_preprocessor_config_for_large_v3(tmp_path, monkeypatch):
    monkeypatch.delenv("VOICE2TEXT_WHISPER_MODEL", raising=False)
    src = _make_src(tmp_path, ("config.json", "model.bin", "tokenizer.json", "vocabulary.json"))
    with pytest.raises(InvalidModelFolder):
        import_whisper_from(src, tmp_path / "models")


def test_import_replaces_existing_install(tmp_path, monkeypatch):
    monkeypatch.delenv("VOICE2TEXT_WHISPER_MODEL", raising=False)
    models = tmp_path / "models"
    dest = model_path(whisper_spec(), models)
    dest.mkdir(parents=True)
    (dest / "stale.bin").write_bytes(b"old")
    src = _make_src(tmp_path)
    import_whisper_from(src, models)
    assert is_installed(whisper_spec(), models) and not (dest / "stale.bin").exists()


def test_import_copy_failure_maps_to_import_failed_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.delenv("VOICE2TEXT_WHISPER_MODEL", raising=False)
    src = _make_src(tmp_path)
    models = tmp_path / "models"

    def boom(*a, **k):
        raise OSError(errno.ENOSPC, "no space")

    monkeypatch.setattr("shutil.copy2", boom)
    with pytest.raises(ImportFailed) as e:
        import_whisper_from(src, models)
    assert e.value.code == "space"
    assert not is_installed(whisper_spec(), models)
    assert [p.name for p in models.iterdir()] == [whisper_spec().subdir + ".lock"]  # no .importing leftovers


def test_unknown_whisper_model_raises_value_error(monkeypatch):
    monkeypatch.setenv("VOICE2TEXT_WHISPER_MODEL", "huge")
    with pytest.raises(ValueError, match="large-v3, tiny"):
        whisper_spec()


def test_archive_is_extracted_and_removed(tmp_path):
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as tf:
        data = b"onnx"
        info = tarfile.TarInfo("inner/model.onnx")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    payload = buf.getvalue()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_HEAD(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()

        def do_GET(self):
            self.do_HEAD()
            self.wfile.write(payload)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        spec = ModelSpec("a", "arch", (RemoteFile(f"http://127.0.0.1:{httpd.server_address[1]}/x.tar.bz2", "x.tar.bz2", "tar.bz2"),))
        download(spec, tmp_path, lambda d, t: None)
    finally:
        httpd.shutdown()
        httpd.server_close()
    root = model_path(spec, tmp_path)
    assert (root / "inner" / "model.onnx").read_bytes() == b"onnx"
    assert not (root / "x.tar.bz2").exists()
    assert is_installed(spec, tmp_path)
    download(spec, tmp_path, lambda d, t: None)  # re-run after consumed archive is a no-op
    assert is_installed(spec, tmp_path)


# --- import_models_from: a Whisper folder or a whole `models` folder ----------------

WHISPER_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.json", "preprocessor_config.json")


def _models_tree(root, whisper=True, diar=True):
    """A copy of an installed `models` folder, as the author would bring it on a USB stick."""
    root.mkdir()
    if whisper:
        w = root / whisper_spec().subdir
        w.mkdir()
        for f in WHISPER_FILES:
            (w / f).write_bytes(b"x")
        (w / ".complete").touch()
    if diar:
        seg = root / "diar-segmentation"
        (seg / "sherpa-onnx-pyannote-segmentation-3-0").mkdir(parents=True)
        (seg / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx").write_bytes(b"seg")
        (seg / "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2.done").touch()
        (seg / ".complete").touch()
        emb = root / "diar-embedding"
        emb.mkdir()
        (emb / "wespeaker_en_voxceleb_resnet34.onnx").write_bytes(b"emb")
        (emb / ".complete").touch()
    return root


def test_import_models_from_whisper_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("VOICE2TEXT_WHISPER_MODEL", raising=False)
    src = _make_src(tmp_path)
    assert import_models_from(src, tmp_path / "models") == ["whisper"]
    assert is_installed(whisper_spec(), tmp_path / "models")


def test_import_models_from_whole_models_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("VOICE2TEXT_WHISPER_MODEL", raising=False)
    src = _models_tree(tmp_path / "usb-models")
    models = tmp_path / "models"
    assert import_models_from(src, models) == ["whisper", "diar-segmentation", "diar-embedding"]
    assert all(is_installed(s, models) for s in required_specs(True))
    nested = models / "diar-segmentation" / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
    assert nested.read_bytes() == b"seg"
    assert (models / "diar-embedding" / "wespeaker_en_voxceleb_resnet34.onnx").read_bytes() == b"emb"
    assert not list(models.glob("*.importing"))


def test_import_models_from_only_diarization_subfolders(tmp_path, monkeypatch):
    monkeypatch.delenv("VOICE2TEXT_WHISPER_MODEL", raising=False)
    src = _models_tree(tmp_path / "usb-models", whisper=False)
    models = tmp_path / "models"
    assert import_models_from(src, models) == ["diar-segmentation", "diar-embedding"]
    assert not is_installed(whisper_spec(), models)


def test_import_models_from_ignores_incomplete_subfolders(tmp_path, monkeypatch):
    monkeypatch.delenv("VOICE2TEXT_WHISPER_MODEL", raising=False)
    src = _models_tree(tmp_path / "usb-models", whisper=False)
    (src / "diar-embedding" / ".complete").unlink()  # interrupted download on the source machine
    assert import_models_from(src, tmp_path / "models") == ["diar-segmentation"]


def test_import_models_from_replaces_existing_diarization_install(tmp_path, monkeypatch):
    monkeypatch.delenv("VOICE2TEXT_WHISPER_MODEL", raising=False)
    models = tmp_path / "models"
    (models / "diar-embedding").mkdir(parents=True)
    (models / "diar-embedding" / "stale.onnx").write_bytes(b"old")
    import_models_from(_models_tree(tmp_path / "usb-models", whisper=False), models)
    assert not (models / "diar-embedding" / "stale.onnx").exists()


@pytest.mark.parametrize("make", [lambda p: p.mkdir(), lambda p: (p.mkdir(), (p / "diar-embedding").mkdir())])
def test_import_models_from_nothing_importable(tmp_path, make):
    src = tmp_path / "empty"
    make(src)
    with pytest.raises(InvalidModelFolder):
        import_models_from(src, tmp_path / "models")


def test_import_models_from_copy_failure_maps_to_import_failed(tmp_path, monkeypatch):
    monkeypatch.delenv("VOICE2TEXT_WHISPER_MODEL", raising=False)
    src = _models_tree(tmp_path / "usb-models", whisper=False)
    models = tmp_path / "models"

    def boom(*a, **k):
        raise OSError(errno.ENOSPC, "no space")

    monkeypatch.setattr("shutil.copy2", boom)
    with pytest.raises(ImportFailed) as e:
        import_models_from(src, models)
    assert e.value.code == "space"
    assert not any(is_installed(s, models) for s in required_specs(True))
    assert not list(models.glob("*.importing"))


_HOLDER = """
import sys
from pathlib import Path
from voice2text import models
spec = models.ModelSpec("t", sys.argv[2], ())
with models._model_lock(spec, Path(sys.argv[1])):
    print("locked", flush=True)
    sys.stdin.readline()
"""


@pytest.fixture
def lock_holder(tmp_path):
    procs = []

    def start(subdir, base=None):
        p = subprocess.Popen(
            [sys.executable, "-c", _HOLDER, str(base or tmp_path), subdir],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        procs.append(p)
        assert p.stdout.readline().strip() == "locked"
        return p

    yield start
    for p in procs:
        if p.poll() is None:
            p.stdin.close()
            p.wait(10)


def _release(p):
    p.stdin.write("\n")
    p.stdin.flush()
    p.wait(10)


def test_download_while_locked_by_other_process_is_busy_and_untouched(server, spec, tmp_path, lock_holder):
    root = model_path(spec, tmp_path)
    root.mkdir(parents=True)
    (root / "a.bin.part").write_bytes(b"abc")
    holder = lock_holder(spec.subdir)
    with pytest.raises(DownloadError) as e:
        download(spec, tmp_path, lambda d, t: None)
    assert e.value.code == "busy"
    assert sorted(p.name for p in root.iterdir()) == ["a.bin.part"]
    assert (root / "a.bin.part").read_bytes() == b"abc"
    assert not server.counts
    _release(holder)
    download(spec, tmp_path, lambda d, t: None)
    assert is_installed(spec, tmp_path)


def test_lock_is_per_model_directory(server, spec, tmp_path, lock_holder):
    lock_holder("some-other-model")
    download(spec, tmp_path, lambda d, t: None)
    assert is_installed(spec, tmp_path)


def test_completed_stream_with_wrong_size_is_corrupt_and_part_removed(server, spec, tmp_path, monkeypatch):
    from voice2text import models

    monkeypatch.setattr(models, "_remote_size", lambda url: len(server.files["/a.bin"]) - 5 if url.endswith("a.bin") else 8)
    with pytest.raises(DownloadError) as e:
        download(spec, tmp_path, lambda d, t: None)
    assert e.value.code == "corrupt"
    root = model_path(spec, tmp_path)
    assert not (root / "a.bin.part").exists() and not (root / "a.bin").exists()
    assert not is_installed(spec, tmp_path)


def test_import_models_from_while_locked_is_busy(tmp_path, monkeypatch, lock_holder):
    monkeypatch.delenv("VOICE2TEXT_WHISPER_MODEL", raising=False)
    src = _make_src(tmp_path)
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    holder = lock_holder(whisper_spec().subdir, models_dir)
    with pytest.raises(ImportFailed) as e:
        import_models_from(src, models_dir)
    assert e.value.code == "busy"
    assert not model_path(whisper_spec(), models_dir).exists()
    _release(holder)
    import_models_from(src, models_dir)
    assert is_installed(whisper_spec(), models_dir)


def test_import_whisper_from_while_locked_is_busy(tmp_path, monkeypatch, lock_holder):
    monkeypatch.delenv("VOICE2TEXT_WHISPER_MODEL", raising=False)
    src = _make_src(tmp_path)
    holder = lock_holder(whisper_spec().subdir)
    with pytest.raises(ImportFailed) as e:
        import_whisper_from(src, tmp_path)
    assert e.value.code == "busy"
    _release(holder)



# --- TLS: corporate inspection proxies install their root CA in the OS store ------------


def test_ssl_context_trusts_os_store_and_certifi(monkeypatch):
    import certifi

    from voice2text import models

    class SpyContext:
        def __init__(self):
            self.cafiles = []

        def load_verify_locations(self, cafile=None, capath=None, cadata=None):
            self.cafiles.append(cafile)

    created = []

    def fake_create_default_context(*args, **kwargs):
        created.append((args, kwargs))
        return SpyContext()

    monkeypatch.setattr(models, "_ssl_ctx", None)
    monkeypatch.setattr(models.ssl, "create_default_context", fake_create_default_context)
    ctx = models._ssl_context()
    assert created == [((), {})]  # default context: OS trust store, no cafile override
    assert ctx.cafiles == [certifi.where()]
    assert models._ssl_context() is ctx and len(created) == 1  # built once, then cached


def test_real_ssl_context_verifies_certificates(monkeypatch):
    import ssl

    from voice2text import models

    monkeypatch.setattr(models, "_ssl_ctx", None)
    ctx = models._ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert ctx.cert_store_stats()["x509_ca"] > 0


# --- Windows antivirus briefly locks freshly copied files: retry the swap --------------


class _Sleeps:
    """Stands in for the `time` module inside models.py only."""

    def __init__(self):
        self.calls = []

    def sleep(self, seconds):
        self.calls.append(seconds)


def _flaky_replace(monkeypatch, failures):
    from voice2text import models

    real = os.replace
    calls = []

    def replace(src, dst):
        calls.append((src, dst))
        if len(calls) <= failures:
            raise PermissionError(errno.EACCES, "used by another process")
        real(src, dst)

    monkeypatch.setattr(models.os, "replace", replace)
    sleeps = _Sleeps()
    monkeypatch.setattr(models, "time", sleeps)
    return calls, sleeps


def test_import_swap_retries_while_the_folder_is_locked(tmp_path, monkeypatch):
    monkeypatch.delenv("VOICE2TEXT_WHISPER_MODEL", raising=False)
    src = _make_src(tmp_path)
    calls, sleeps = _flaky_replace(monkeypatch, failures=2)
    import_whisper_from(src, tmp_path / "models")
    assert is_installed(whisper_spec(), tmp_path / "models")
    assert len(calls) == 3 and sleeps.calls == [0.5, 0.5]


def test_models_folder_swap_retries_too(tmp_path, monkeypatch):
    monkeypatch.delenv("VOICE2TEXT_WHISPER_MODEL", raising=False)
    src = _models_tree(tmp_path / "usb-models", whisper=False)
    calls, sleeps = _flaky_replace(monkeypatch, failures=2)
    assert import_models_from(src, tmp_path / "models") == ["diar-segmentation", "diar-embedding"]
    assert len(calls) == 4 and sleeps.calls == [0.5, 0.5]  # 2 failures + 2 swaps


def test_import_swap_gives_up_after_five_attempts(tmp_path, monkeypatch):
    monkeypatch.delenv("VOICE2TEXT_WHISPER_MODEL", raising=False)
    src = _make_src(tmp_path)
    models_dir = tmp_path / "models"
    calls, sleeps = _flaky_replace(monkeypatch, failures=99)
    with pytest.raises(ImportFailed) as e:
        import_whisper_from(src, models_dir)
    assert e.value.code == "io"
    assert len(calls) == 5 and sleeps.calls == [0.5] * 4
    assert not list(models_dir.glob("*.importing")) and not is_installed(whisper_spec(), models_dir)
