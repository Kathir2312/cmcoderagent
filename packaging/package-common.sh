# Shared by the packaging/package-*.sh scripts (sourced, not run): checking
# the build machine's tools, naming the build after its git commit, building
# the standalone cmcoder and running it with no Python in reach, building the
# shared chat page, and checking what's inside a finished file.
#
# The calling script sets, before using these:
#   root        the repository (absolute)
#   skip_tests  1 to leave out the test runs (try-outs only)
#   use_built   1 to use dist/cmcoder from an earlier run (built and checked then)

die() { printf '%s\n' "$*" >&2; exit 1; }

need() {
  command -v "$1" >/dev/null 2>&1 || die "$1 is needed: $2"
}

step() {
  printf '\n== %s\n' "$1"
}

# This machine's platform, as the files are named: darwin-arm64 or linux-x64.
this_target() {
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) echo darwin-arm64 ;;
    Linux-x86_64 | Linux-amd64) echo linux-x64 ;;
    MINGW* | MSYS* | CYGWIN*) die "On Windows, run the .ps1 script of the same name (PowerShell)." ;;
    *) die "This platform ($(uname -s) $(uname -m)) isn't one the files are made for: Windows x64, macOS on Apple silicon, Linux x64." ;;
  esac
}

need_node() {
  need node "install Node.js 22 LTS"
  need npm "it comes with Node.js"
  local major
  major="$(node -p "process.versions.node.split('.')[0]")"
  [ "$major" -ge 22 ] || die "Node.js 22 or newer is needed (this is $(node --version))"
}

# need_java MIN: a JDK of at least that version, as `java` on PATH.
need_java() {
  need java "install a JDK $1 or newer (for example Eclipse Temurin)"
  local v
  v="$(java -XshowSettings:properties -version 2>&1 | sed -n 's/^ *java\.specification\.version = //p' | head -n 1 || true)"
  v="${v#1.}"
  [ -n "$v" ] && [ "${v%%.*}" -ge "$1" ] || die "JDK $1 or newer is needed (java -version says ${v:-nothing}); set JAVA_HOME and PATH to it"
}

need_maven() {
  need mvn "install Apache Maven 3.9 or newer"
  local v
  v="$(mvn -v 2>/dev/null | sed -n 's/^Apache Maven \([0-9]*\)\.\([0-9]*\).*/\1 \2/p' | head -n 1 || true)"
  set -- $v
  [ -n "${1:-}" ] && { [ "$1" -gt 3 ] || { [ "$1" -eq 3 ] && [ "$2" -ge 9 ]; }; } ||
    die "Maven 3.9 or newer is needed ($(mvn -v 2>/dev/null | head -n 1))"
}

need_gradle() {
  need gradle "install Gradle 9.8.1 (https://gradle.org/releases/)"
  local major
  major="$(gradle --version 2>/dev/null | sed -n 's/^Gradle \([0-9]*\)\..*/\1/p' | head -n 1 || true)"
  [ -n "$major" ] && [ "$major" -ge 9 ] || die "Gradle 9 or newer is needed (9.8.1 is what CI uses)"
}

# The build is named after its git commit ("0.1.0 (aa3dc83 2026-10-12)", in
# --version and Copy Diagnostics). Checked first, not after a long build.
check_git() {
  need git "install git (the build is named after its commit)"
  local commit why
  if ! commit="$(git -C "$root" rev-parse --short=7 HEAD 2>&1)"; then
    why="git can't read this folder's commit, which names the build:
  $commit
- 'dubious ownership': trust the folder, then run this again:
    git config --global --add safe.directory '$root'
- 'not a git repository': use a git clone of the repository, not a copy or a downloaded zip."
    [ "$skip_tests" = 1 ] || die "$why"
    printf 'WARNING: %s\nGoing on (--skip-tests): the build will say "unknown" instead of its commit.\n' "$why" >&2
  fi
}

# The standalone cmcoder in dist/cmcoder, built and run with no Python in
# reach (or, with use_built, the one an earlier run built and checked).
build_cmcoder() {
  need uv "install it from https://docs.astral.sh/uv/ (it brings its own Python)"
  cmcoder_exe="$root/dist/cmcoder/cmcoder"
  if [ "$use_built" = 1 ]; then
    [ -x "$cmcoder_exe" ] || die "--use-built: there's no $cmcoder_exe (run without --use-built)"
    step "Standalone cmcoder: the one already in dist/cmcoder"
    return
  fi
  step "Python environment for the build (uv)"
  uv sync --python 3.12
  step "Standalone cmcoder (PyInstaller)"
  uv run --with pyinstaller python packaging/build.py
  [ -x "$cmcoder_exe" ] || die "The build made no $cmcoder_exe"
  if [ "$skip_tests" = 0 ]; then
    step "cmcoder, run as developers run it (no Python in reach)"
    CMCODER_TEST_BINARY="$cmcoder_exe" CMCODER_TEST_NO_PYTHON=1 uv run pytest -q tests/test_standalone.py
  fi
}

build_panel() {
  step "The shared chat panel"
  (cd "$root/clients/web-panel" && npm ci && npm run build)
}

# zip_has FILE SUFFIX...: fails unless FILE (a zip: .vsix, .nbm, .zip) has an
# entry ending in each SUFFIX. A SUFFIX "a.zip!b" looks inside the zips in FILE
# whose names match the glob "a.zip" for an entry ending in "b".
zip_has() {
  uv run --no-sync python - "$@" <<'EOF'
import fnmatch
import io
import sys
import zipfile

path, wanted = sys.argv[1], sys.argv[2:]
with zipfile.ZipFile(path) as z:
    names = z.namelist()
    for want in wanted:
        if "!" in want:
            pattern, inner = want.split("!", 1)
            inners = [n for n in names if fnmatch.fnmatch(n, pattern)]
            ok = False
            for n in inners:
                with zipfile.ZipFile(io.BytesIO(z.read(n))) as nested:
                    ok = ok or any(e.endswith(inner) for e in nested.namelist())
        else:
            ok = any(n.endswith(want) for n in names)
        if not ok:
            sys.exit(f"{path} has no {want}: the program isn't in the file")
EOF
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

# done_message FILE "how developers install it"
done_message() {
  printf '\nDone: %s\n' "$1"
  printf '  %s bytes, SHA256 %s\n' "$(wc -c <"$1" | tr -d ' ')" "$(sha256_of "$1")"
  printf '  cmcoder is inside. %s\n' "$2"
}
