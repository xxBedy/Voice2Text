# Příručka pro vývojáře

[English: development.md](development.md) · Česky: development.cs.md

## Příprava

Python 3.12 a [uv](https://docs.astral.sh/uv/):

    uv sync

## Spuštění ze zdrojových kódů

    uv run voice2text                         # okno aplikace
    uv run voice2text --transcribe SOUBOR     # bez okna: přepis uloží vedle SOUBORU

`uv run voice2text --help` vypíše další volby (`--format`, `--language`, `--speakers`).

Vývoj probíhá ve větvi `main` tohoto repozitáře; změny posílej jako pull requesty do ní.

## Testy

    uv run pytest -q                                   # rychlé testy bez modelu
    VOICE2TEXT_WHISPER_MODEL=tiny uv run pytest -m model   # testy se skutečným (malým) modelem Whisper

`VOICE2TEXT_WHISPER_MODEL=tiny` stáhne malý model místo large-v3. Další užitečné
proměnné: `VOICE2TEXT_HOME` (složka dat aplikace), `VOICE2TEXT_UI_LANG` (`cs`/`en`),
`VOICE2TEXT_CPU_THREADS`, `VOICE2TEXT_CLI_SPAWN=1` (přepis z příkazové řádky běží
v odděleném procesu stejně jako z okna).

Zvukové fixtures v `tests/fixtures/` vzniknou na macOS skriptem `scripts/make_fixtures.sh`
(používá `say` a `afconvert`); jsou v repozitáři, takže ho obvykle není třeba spouštět.

Screenshoty do README se renderují offscreen příkazem

    uv run python scripts/render_screenshots.py

do `docs/images/window-en.png` a `window-cs.png`.

## Lokální build

    uv run pyinstaller packaging/voice2text.spec --noconfirm
    packaging/macos/build_dmg.sh <verze>                # macOS: dist/Voice2Text-<verze>.dmg

Na Windows se instalátor staví v Inno Setup 6:

    ISCC.exe /DMyAppVersion=<verze> packaging\windows\installer.iss

## CI a vydání

`.github/workflows/build.yml` spouští testy při každém pushi do `main` a u pull requestů.
Na tagy `v*` (a ručně) běží celý postup: testy, build PyInstalleru, smoke testy, pak
`.dmg` a instalátor pro Windows, které se u tagů přiloží k GitHub Release.

Postup vydání:

1. Změň verzi na **obou** místech: `version` v `pyproject.toml` a `__version__`
   v `src/voice2text/__init__.py` (build backend `uv_build` neumí verzi načíst
   dynamicky; test hlídá, že se shodují).
2. Commitni do `main`, pak vytvoř a pushni tag `vX.Y.Z`. CI odmítne tag, který nesedí
   s `__version__`.
