#!/usr/bin/env bash
# Usage: packaging/macos/build_dmg.sh <version>
# Packs dist/Voice2Text.app into dist/Voice2Text-<version>.dmg.
set -euo pipefail

version="${1:?usage: build_dmg.sh <version>}"
root="$(cd "$(dirname "$0")/../.." && pwd)"
app="$root/dist/Voice2Text.app"
dmg="$root/dist/Voice2Text-$version.dmg"

[ -d "$app" ] || { echo "missing $app (run pyinstaller first)" >&2; exit 1; }

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
cp -R "$app" "$stage/"
ln -s /Applications "$stage/Applications"

hdiutil create -volname Voice2Text -srcfolder "$stage" -ov -format UDZO "$dmg"
echo "$dmg"
