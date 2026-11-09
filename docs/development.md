# Developer guide

English: development.md · [Česky: development.cs.md](development.cs.md)

## Setup

Python 3.12 and [uv](https://docs.astral.sh/uv/):

    uv sync

## Running from source

    uv run voice2text                         # the window
    uv run voice2text --transcribe FILE       # headless: writes the transcript next to FILE

`uv run voice2text --help` lists the other options (`--format`, `--language`, `--speakers`).

Development happens on the `main` branch of this repository; send changes as pull
requests against it.

## Tests

    uv run pytest -q                                   # fast tests, no model needed
    VOICE2TEXT_WHISPER_MODEL=tiny uv run pytest -m model   # tests that run a real (tiny) Whisper model

`VOICE2TEXT_WHISPER_MODEL=tiny` downloads the small model instead of large-v3. Other
useful variables: `VOICE2TEXT_HOME` (app data folder), `VOICE2TEXT_UI_LANG` (`cs`/`en`),
`VOICE2TEXT_CPU_THREADS`, `VOICE2TEXT_CLI_SPAWN=1` (the headless run uses a spawned
worker process like the window does).

Audio fixtures in `tests/fixtures/` are produced on macOS by `scripts/make_fixtures.sh`
(uses `say` and `afconvert`); they are committed, so you rarely need to rerun it.

Screenshots for the READMEs are rendered offscreen by

    uv run python scripts/render_screenshots.py

into `docs/images/window-en.png` and `window-cs.png`.

## Local build

    uv run pyinstaller packaging/voice2text.spec --noconfirm
    packaging/macos/build_dmg.sh <version>              # macOS: dist/Voice2Text-<version>.dmg

On Windows, build the installer with Inno Setup 6:

    ISCC.exe /DMyAppVersion=<version> packaging\windows\installer.iss

## CI and releases

`.github/workflows/build.yml` runs the tests on every push to `main` and on pull
requests. On tags `v*` (and manually) it runs the full pipeline: tests, PyInstaller
build, smoke tests, then the `.dmg` and the Windows installer, attached to a GitHub
Release on tags.

To release:

1. Change the version in **both** places: `version` in `pyproject.toml` and
   `__version__` in `src/voice2text/__init__.py` (the `uv_build` backend cannot read
   it dynamically; a test checks that they match).
2. Commit to `main`, then tag and push `vX.Y.Z`. CI refuses a tag that does not match
   `__version__`.
