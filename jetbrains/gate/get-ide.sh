#!/usr/bin/env bash
# Gets an IntelliJ-based IDE for this machine from JetBrains, for the release
# gate: looks it up in JetBrains' release list, downloads this platform's file,
# checks it against JetBrains' published checksum and unpacks it. Prints the
# folder to give run-gate.sh as --ide (on macOS, the .app).
#
#   get-ide.sh <product code> <version|newest> <folder to unpack into>
#
# Product codes as JetBrains' list has them: IIC (IntelliJ IDEA Community, up
# to 2025.2), IIU (IntelliJ IDEA), PCC / PCP (PyCharm), RD (Rider), WS
# (WebStorm), GO (GoLand). Needs curl and jq; on Windows 7-Zip (or unzip), on
# macOS hdiutil. Works in Git Bash on Windows.
set -euo pipefail

[ $# -eq 3 ] || { echo "usage: get-ide.sh <product code> <version|newest> <folder>" >&2; exit 2; }
code=$1 version=$2 dest=$3

case "$(uname -s)" in
  Linux) key=linux ;;
  Darwin) if [ "$(uname -m)" = arm64 ]; then key=macM1; else key=mac; fi ;;
  MINGW*|MSYS*|CYGWIN*) key=windowsZip ;;
  *) echo "get-ide.sh: unknown system $(uname -s)" >&2; exit 1 ;;
esac

query="https://data.services.jetbrains.com/products/releases?code=$code&type=release"
[ "$version" = newest ] && query="$query&latest=true"
release=$(curl -fsSL --retry 3 "$query" \
  | jq -c --arg c "$code" --arg v "$version" '[.[$c][] | select($v == "newest" or .version == $v)][0]')
[ "$release" != null ] || { echo "get-ide.sh: no $code $version in JetBrains' release list" >&2; exit 1; }
url=$(jq -r --arg k "$key" '.downloads[$k].link // empty' <<<"$release")
sums=$(jq -r --arg k "$key" '.downloads[$k].checksumLink // empty' <<<"$release")
[ -n "$url" ] && [ -n "$sums" ] || { echo "get-ide.sh: $code $version has no $key download" >&2; exit 1; }
echo "get-ide.sh: $code $(jq -r '.version + " (" + .build + ")"' <<<"$release"), $url" >&2

mkdir -p "$dest"
cd "$dest"
dest=$(pwd)
archive=$(basename "$url")
curl -fsSL --retry 3 -o "$archive" "$url"
expected=$(curl -fsSL --retry 3 "$sums" | cut -d' ' -f1)
actual=$( (sha256sum "$archive" 2>/dev/null || shasum -a 256 "$archive") | cut -d' ' -f1)
if [ -z "$expected" ] || [ "$expected" != "$actual" ]; then
  echo "get-ide.sh: $archive doesn't match JetBrains' checksum ($expected, got $actual)" >&2
  exit 1
fi

case "$key" in
  linux)
    mkdir -p ide && tar -xzf "$archive" -C ide --strip-components=1
    home="$dest/ide" ;;
  windowsZip)
    if command -v 7z >/dev/null; then 7z x -bd -y -oide "$archive" >/dev/null; else unzip -q "$archive" -d ide; fi
    info=$(find ide -maxdepth 2 -name product-info.json | head -1)
    [ -n "$info" ] || { echo "get-ide.sh: no product-info.json in $archive" >&2; exit 1; }
    home="$dest/$(dirname "$info")" ;;
  mac*)
    hdiutil attach -nobrowse -readonly -mountpoint "$dest/dmg" "$archive" >/dev/null
    app=$(ls -d "$dest"/dmg/*.app | head -1)
    cp -R "$app" "$dest/ide.app"
    hdiutil detach "$dest/dmg" >/dev/null
    home="$dest/ide.app" ;;
esac
rm -f "$archive"
echo "$home"
