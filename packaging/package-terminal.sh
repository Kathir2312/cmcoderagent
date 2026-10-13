#!/usr/bin/env bash
# Builds the terminal package (the standalone cmcoder CLI) for developers'
# PCs: dist/cmcoder-terminal-<platform>.zip (darwin-arm64, linux-x64), with
# cmcoder and its install scripts. Developers need no Python, uv or Node.js,
# and no admin rights. (Windows: packaging/package-terminal.ps1.)
#
# Run it on a build machine (not the developers'), in the repository:
#
#     bash packaging/package-terminal.sh [--skip-tests] [--use-built]
#
# The build machine needs uv (https://docs.astral.sh/uv/; it downloads its own
# Python 3.12) and git with the repository as a git clone (the build is named
# after its commit). docs/building/cli.md has the details.
#
# It does what the Release workflow does for this file: builds cmcoder with
# PyInstaller, runs it with no Python in reach, packs it with the install
# scripts, tests installing, updating and uninstalling it, and checks cmcoder
# is in the file.
#
#   --skip-tests  leave out the test runs (try-outs only, never for release)
#   --use-built   use dist/cmcoder from an earlier run (built and checked then)
set -euo pipefail

skip_tests=0
use_built=0
for arg in "$@"; do
  case "$arg" in
    --skip-tests) skip_tests=1 ;;
    --use-built) use_built=1 ;;
    -h | --help) awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
. "$root/packaging/package-common.sh"

target="$(this_target)"
check_git
build_cmcoder

step "The terminal package"
uv run python packaging/terminal.py
if [ "$skip_tests" = 0 ]; then
  step "Install, update, run and uninstall it (in a temporary home folder)"
  CMCODER_TEST_BINARY="$cmcoder_exe" uv run pytest -q tests/test_terminal_package.py
fi

out="$root/dist/cmcoder-terminal-$target.zip"
cp "dist/terminal/cmcoder-$target.zip" "$out"
zip_has "$out" "cmcoder-$target/cmcoder/cmcoder" "cmcoder-$target/install.sh"
done_message "$out" "Developers: unzip it, run sh install.sh, open a new terminal, run cmcoder doctor."
