#!/usr/bin/env bash
# Builds the VS Code extension for developers' PCs, with the standalone cmcoder
# inside: vscode/cmcoder-<platform>.vsix (darwin-arm64, linux-x64). Developers
# need only VS Code: no Python, uv or Node.js, and no admin rights.
# (Windows: packaging/package-vscode.ps1.)
#
# Run it on a build machine (not the developers'), in the repository:
#
#     bash packaging/package-vscode.sh [--skip-tests]
#
# The build machine needs uv (https://docs.astral.sh/uv/; it downloads its own
# Python 3.12 if there's none) and Node.js 22 or newer.
#
# It does what the Release workflow does for this platform: builds cmcoder with
# PyInstaller, runs it with no Python in reach, packages the extension with it
# (and the brand in branding/), and checks cmcoder is in the file.
#
# The other "cmcoder.vsix" (npm run package, or CI's cmcoder-vsix artifact) has
# no cmcoder inside: it's for testing on machines that have cmcoder already.
set -euo pipefail

skip_tests=0
for arg in "$@"; do
  case "$arg" in
    --skip-tests) skip_tests=1 ;;
    -h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "$1 is needed: $2" >&2; exit 1; }
}
step() {
  printf '\n== %s\n' "$1"
}

need uv "install it from https://docs.astral.sh/uv/ (it brings its own Python)"
need node "install Node.js 22 LTS"
need npm "it comes with Node.js"
major="$(node -p "process.versions.node.split('.')[0]")"
if [ "$major" -lt 22 ]; then
  echo "Node.js 22 or newer is needed for packaging (this is $(node --version))" >&2
  exit 1
fi

step "Python environment for the build (uv)"
uv sync --python 3.12
step "Standalone cmcoder (PyInstaller)"
uv run --with pyinstaller python packaging/build.py
exe="$root/dist/cmcoder/cmcoder"
[ -x "$exe" ] || { echo "The build made no $exe" >&2; exit 1; }

if [ "$skip_tests" = 0 ]; then
  step "cmcoder, run as developers run it (no Python in reach)"
  CMCODER_TEST_BINARY="$exe" CMCODER_TEST_NO_PYTHON=1 uv run pytest -q tests/test_standalone.py
fi

step "The shared chat panel"
(cd clients/web-panel && npm ci && npm run build)
step "The extension's packages"
(cd vscode && npm ci)
step "The .vsix, with cmcoder inside"
uv run python packaging/vsix.py

# The file developers get must hold the program.
vsix="$(ls -t vscode/cmcoder-darwin-*.vsix vscode/cmcoder-linux-*.vsix 2>/dev/null | head -n 1 || true)"
[ -n "$vsix" ] || { echo "No vscode/cmcoder-<platform>.vsix was made" >&2; exit 1; }
uv run python - "$vsix" <<'EOF'
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as z:
    if "extension/bin/cmcoder/cmcoder" not in z.namelist():
        sys.exit(f"{sys.argv[1]} has no extension/bin/cmcoder/cmcoder")
EOF

if command -v sha256sum >/dev/null 2>&1; then
  hash="$(sha256sum "$vsix" | cut -d' ' -f1)"
else
  hash="$(shasum -a 256 "$vsix" | cut -d' ' -f1)"
fi
printf '\nDone: %s\n' "$root/$vsix"
printf '  %s bytes, SHA256 %s\n' "$(wc -c <"$vsix" | tr -d ' ')" "$hash"
printf '  cmcoder is inside. Developers: VS Code > Extensions > ... > Install from VSIX\n'
printf '  (or: code --install-extension %s). No admin rights needed.\n' "$(basename "$vsix")"
