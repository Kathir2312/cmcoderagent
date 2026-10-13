#!/usr/bin/env bash
# Builds every package for this platform (macOS on Apple silicon, Linux x64) in
# one go: cmcoder is built and run with no Python in reach once, then each
# package is made around it, in dist/:
#   terminal   dist/cmcoder-terminal-<platform>.zip   (package-terminal.sh)
#   vscode     vscode/cmcoder-<platform>.vsix         (package-vscode.sh)
#   netbeans   dist/cmcoder-netbeans-<platform>.nbm   (package-netbeans.sh)
#   jetbrains  dist/cmcoder-jetbrains-<platform>.zip  (package-jetbrains.sh)
# (Windows: packaging/package-all.ps1, which also makes the Visual Studio
# extension.) The Eclipse plugin is one file for all platforms: make it with
# package-eclipse.sh and the three platforms' cmcoder.
#
#     bash packaging/package-all.sh [--skip-tests] [--only terminal,vscode,...]
#
# The build machine needs what each package's page in docs/building/ lists;
# --only leaves out the packages whose tools it doesn't have.
#
#   --skip-tests  leave out the test runs (try-outs only, never for release)
#   --only LIST   only these packages (comma-separated)
set -euo pipefail

skip_tests=0
use_built=0
packages="terminal vscode netbeans jetbrains"
while [ $# -gt 0 ]; do
  case "$1" in
    --skip-tests) skip_tests=1; shift ;;
    --only)
      [ $# -ge 2 ] || { echo "--only needs a list, e.g. --only terminal,vscode" >&2; exit 2; }
      packages="$(printf '%s' "$2" | tr ',' ' ')"; shift 2 ;;
    -h | --help) awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"; exit 0 ;;
    *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
  esac
done
for p in $packages; do
  case "$p" in
    terminal | vscode | netbeans | jetbrains) ;;
    *) echo "unknown package: $p (terminal, vscode, netbeans, jetbrains)" >&2; exit 2 ;;
  esac
done

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
. "$root/packaging/package-common.sh"

this_target >/dev/null
check_git
build_cmcoder # once, for every package

pass=(--use-built)
[ "$skip_tests" = 1 ] && pass+=(--skip-tests)
for p in $packages; do
  printf '\n######## %s ########\n' "$p"
  bash "$root/packaging/package-$p.sh" "${pass[@]}"
done

printf '\nAll done (%s). The files:\n' "$packages"
for f in dist/cmcoder-terminal-*.zip vscode/cmcoder-*-*.vsix dist/cmcoder-netbeans-*.nbm dist/cmcoder-jetbrains-*.zip; do
  [ -f "$f" ] && printf '  %s  %s\n' "$(sha256_of "$f")" "$f"
done
true
