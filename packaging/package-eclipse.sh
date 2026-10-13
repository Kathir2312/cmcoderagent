#!/usr/bin/env bash
# Builds the Eclipse plugin for developers' PCs: dist/cmcoder-eclipse.zip, an
# update-site archive for Windows, macOS and Linux in one file, with each
# platform's cmcoder inside. Developers need Eclipse 2024-06+ only: no Python,
# uv or Node.js. (Windows: packaging/package-eclipse.ps1.)
#
# Run it on a build machine (not the developers'), in the repository:
#
#     bash packaging/package-eclipse.sh --win32-x64 DIR|ZIP --darwin-arm64 DIR|ZIP \
#         --linux-x64 DIR|ZIP [--skip-tests] [--use-built] [--one-platform]
#
# Each platform's cmcoder is built on that platform (packaging/build.py there:
# its dist/cmcoder folder), or taken from a Release build run's artifacts
# (cmcoder-win32-x64, cmcoder-darwin-arm64, cmcoder-linux-x64): give the
# folder, or the zip as downloaded. This machine's own platform may be left
# out: then it's built here (and run with no Python in reach).
#
#   --one-platform  only this machine's cmcoder, for a try-out: the file is
#                   named cmcoder-eclipse-TRYOUT-<platform>-only.zip (on the
#                   other platforms the plugin would look for cmcoder on PATH)
#   --skip-tests    leave out the test runs (try-outs only, never for release)
#   --use-built     use dist/cmcoder from an earlier run (built and checked then)
#
# The build machine needs uv (https://docs.astral.sh/uv/), Node.js 22, JDK 21,
# Maven 3.9+, access to download.eclipse.org, and git with the repository as
# a git clone. docs/building/eclipse.md has the details.
set -euo pipefail

skip_tests=0
use_built=0
one_platform=0
given_win="" given_mac="" given_linux=""
while [ $# -gt 0 ]; do
  case "$1" in
    --win32-x64 | --darwin-arm64 | --linux-x64)
      [ $# -ge 2 ] || { echo "$1 needs a folder or zip (see --help)" >&2; exit 2; }
      case "$1" in
        --win32-x64) given_win="$2" ;;
        --darwin-arm64) given_mac="$2" ;;
        --linux-x64) given_linux="$2" ;;
      esac
      shift 2 ;;
    --skip-tests) skip_tests=1; shift ;;
    --use-built) use_built=1; shift ;;
    --one-platform) one_platform=1; shift ;;
    -h | --help) awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
. "$root/packaging/package-common.sh"

target="$(this_target)"
# Per platform: the folder or zip given, the fragment its cmcoder goes in, the
# program's name. (Functions, not associative arrays: macOS has bash 3.2.)
given_for() {
  case "$1" in win32-x64) echo "$given_win" ;; darwin-arm64) echo "$given_mac" ;; linux-x64) echo "$given_linux" ;; esac
}
fragment_for() {
  case "$1" in win32-x64) echo win32.x86_64 ;; darwin-arm64) echo macosx.aarch64 ;; linux-x64) echo linux.x86_64 ;; esac
}
program_for() {
  case "$1" in win32-x64) echo cmcoder.exe ;; *) echo cmcoder ;; esac
}

if [ "$one_platform" = 1 ]; then
  platforms="$target"
else
  platforms="win32-x64 darwin-arm64 linux-x64"
  missing=""
  for p in $platforms; do
    [ -n "$(given_for "$p")" ] || [ "$p" = "$target" ] || missing="$missing --$p"
  done
  [ -z "$missing" ] || die "The archive holds every platform's cmcoder: give$missing (a folder or zip; see --help).
For a try-out with this machine's only: --one-platform."
fi

need_node
need_java 21
need_maven
check_git
[ -n "$(given_for "$target")" ] || build_cmcoder
build_panel

work="$(mktemp -d)"
cleanup() {
  rm -rf "$work"
  rm -rf "$root"/eclipse/fragments/*/bin/cmcoder # git ignores them; the next build must not take them
}
trap cleanup EXIT

# A cmcoder folder (the one holding the program and _internal), from a folder
# or a zip (a downloaded artifact, a copied dist/cmcoder, ...).
cmcoder_folder() { # PLATFORM SOURCE
  local name src="$2" dir found
  name="$(program_for "$1")"
  if [ -f "$src" ]; then
    dir="$work/$1"
    mkdir -p "$dir"
    uv run --no-sync python -m zipfile -e "$src" "$dir"
    src="$dir"
  fi
  [ -d "$src" ] || die "--$1: no folder or zip at $src"
  found="$(find "$src" -maxdepth 4 -type f -name "$name" -print | while read -r f; do
    if [ -d "$(dirname "$f")/_internal" ]; then dirname "$f"; break; fi
  done || true)"
  [ -n "$found" ] || die "--$1: $2 holds no $name next to an _internal folder (is it that platform's dist/cmcoder?)"
  printf '%s\n' "$found"
}

step "Each platform's cmcoder into its fragment"
rm -rf eclipse/fragments/*/bin/cmcoder
for p in $platforms; do
  if [ -n "$(given_for "$p")" ]; then src="$(cmcoder_folder "$p" "$(given_for "$p")")"; else src="$root/dist/cmcoder"; fi
  dest="eclipse/fragments/cmcoder.eclipse.$(fragment_for "$p")/bin/cmcoder"
  mkdir -p "$(dirname "$dest")"
  cp -RL "$src" "$dest" # -L: links as files (Eclipse's installer keeps no links)
  [ "$p" = win32-x64 ] || chmod +x "$dest/cmcoder"
  echo "  $p: $src"
done

step "The update-site archive (the gate test runs in the Release build: it needs a real Eclipse)"
(cd eclipse && mvn -B -DskipTests verify)
site="$(ls eclipse/site/target/cmcoder-eclipse-*.zip 2>/dev/null | head -n 1 || true)"
[ -n "$site" ] || die "The build made no eclipse/site/target/cmcoder-eclipse-*.zip"

if [ "$one_platform" = 1 ]; then
  out="$root/dist/cmcoder-eclipse-TRYOUT-$target-only.zip"
else
  out="$root/dist/cmcoder-eclipse.zip"
fi
mkdir -p dist
cp "$site" "$out"
checks=()
for p in $platforms; do
  checks+=("plugins/cmcoder.eclipse.$(fragment_for "$p")_*.jar!bin/cmcoder/$(program_for "$p")")
done
zip_has "$out" "${checks[@]}"
done_message "$out" "Developers: Eclipse > Help > Install New Software... > Add... > Archive..., then restart."
[ "$one_platform" = 0 ] || printf '  A try-out with %s only: not for handing out.\n' "$target"
