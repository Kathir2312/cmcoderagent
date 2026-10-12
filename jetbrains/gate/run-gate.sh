#!/usr/bin/env bash
# The release gate for IntelliJ-based IDEs (Phase 7 item 5): unpacks the gate
# build of the plugin (the zip, with cmcoder and the self-test inside) into a
# fresh IDE profile as "Install Plugin from Disk" lays it out, starts the mock
# model server, starts the IDE on a test project, and waits for the plugin's
# self-test (src/gate/.../Gate.java) to write its result. Fails unless every
# step passed.
#
#   run-gate.sh --zip path/to/cmcoder-jetbrains-<v>-gate.zip --ide path/to/ide --python path/to/python
#
# --ide is an unpacked IntelliJ-based IDE, as gate/get-ide.sh leaves it: the
# folder with product-info.json (Linux, Windows) or the .app (macOS). --python
# is a Python with cmcoder, for the mock server only (test equipment); the IDE
# and cmcoder run without it (run this under packaging/no_python.py so nothing
# finds Python on PATH). On Linux, run it under xvfb-run; on Windows, in Git
# Bash. CMCODER_GATE_SHOTS=<folder> keeps screenshots of the chat, a diff and
# the navigator (and of the screen if the gate times out, on Linux).
# --ide-looks-for-python: for an IDE that looks for Python itself (PyCharm
# runs `uv python dir`): what the IDE's own process starts is a note, not a
# failure (no_python.py's IDE_VARIABLE; the plugin's code runs in that process
# too, so IntelliJ IDEA's gate, which has no such option, still holds it).
set -euo pipefail
trap 'echo "run-gate.sh: line $LINENO failed (exit $?): $BASH_COMMAND" >&2' ERR

timeout_s=900
ide_looks_for_python=0
while [ $# -gt 0 ]; do
  case "$1" in
    --zip) zip=$2; shift 2 ;;
    --ide) ide=$2; shift 2 ;;
    --python) python=$2; shift 2 ;;
    --timeout) timeout_s=$2; shift 2 ;;
    --ide-looks-for-python) ide_looks_for_python=1; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
: "${zip:?--zip is required}" "${ide:?--ide is required}" "${python:?--python is required}"
zip="$(cd "$(dirname "$zip")" && pwd)/$(basename "$zip")"
ide="$(cd "$ide" && pwd)"

case "$(uname -s)" in
  Linux) system=Linux; windows=0 ;;
  Darwin) system=macOS; windows=0 ;;
  MINGW*|MSYS*|CYGWIN*) system=Windows; windows=1 ;;
  *) echo "unknown system $(uname -s)" >&2; exit 1 ;;
esac
case "$(uname -m)" in arm64|aarch64) arch=aarch64 ;; *) arch=amd64 ;; esac
# Paths for Windows programs (Java, Python): C:\... ; in IntelliJ's settings file, C:/...
native() { if [ "$windows" = 1 ]; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
setting() { if [ "$windows" = 1 ]; then cygpath -m "$1"; else printf '%s' "$1"; fi; }

# The launcher and its settings variable (IDEA_PROPERTIES, PYCHARM_PROPERTIES, ...)
# from the IDE's own description of itself.
info="$ide/product-info.json"
[ -f "$info" ] || info="$ide/Contents/Resources/product-info.json"
[ -f "$info" ] || { echo "No product-info.json in $ide: not an unpacked IDE" >&2; exit 1; }
launcher=$("$python" -c '
import json, os, sys
info, system, arch = sys.argv[1:4]
launch = [l for l in json.load(open(info, encoding="utf-8"))["launch"] if l["os"].lower() == system.lower()]
launch = [l for l in launch if l.get("arch", arch) == arch] or launch
print(os.path.normpath(os.path.join(os.path.dirname(info), launch[0]["launcherPath"])))
' "$(native "$info")" "$system" "$arch" | tr -d '\r')
[ "$windows" = 1 ] && launcher=$(cygpath -u "$launcher")
product=$("$python" -c 'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8")); print(d["name"], d["version"], d["buildNumber"])' "$(native "$info")" | tr -d '\r')
name=$(basename "$launcher"); name=${name%.exe}; name=${name%.sh}; name=${name%64}
properties_variable="$(printf '%s' "$name" | tr '[:lower:]' '[:upper:]')_PROPERTIES"
echo "IDE: $product on $system ($launcher, settings through \$$properties_variable)"

work=$(mktemp -d "${TMPDIR:-/tmp}/cmcoder-ij-gate-XXXXXX")
project="$work/project"
mkdir -p "$project" "$work/config" "$work/system" "$work/plugins" "$work/log" "$work/cmcoder"
printf 'first line\nsecond line\n' > "$project/app.txt"
git -C "$project" init -q 2>/dev/null || mkdir -p "$project/.git"
result="$work/result.json"

# The plugin's files where "Install Plugin from Disk" puts them: <plugins>/<the zip's folder>.
if command -v unzip >/dev/null; then
  unzip -q "$zip" -d "$work/plugins"
else
  "$python" -m zipfile -e "$(native "$zip")" "$(native "$work/plugins")"
fi
if ! ls "$work"/plugins/*/lib/*.jar >/dev/null 2>&1; then
  echo "The zip's files didn't land as a plugin (<plugins>/<name>/lib/*.jar). Unpacked:" >&2
  find "$work/plugins" -maxdepth 3 | head -30 >&2
  exit 1
fi
ls "$work"/plugins/*/panel/test-driver.js >/dev/null 2>&1 || { echo "No panel/test-driver.js: not the gate build (-Pgate)" >&2; exit 1; }

# The model's replies, in order: a getDiagnostics call, a reply, a Write
# accepted, a reply, a Write rejected (the turn stops there).
script="$work/script.json"
printf '%s' '[{"tool_calls":[{"name":"getDiagnostics","arguments":{}}]},{"content":"Checked the problems."},{"tool_calls":[{"name":"Write","arguments":{"file_path":"new.txt","content":"x = 2\n"}}]},{"content":"Wrote new.txt."},{"tool_calls":[{"name":"Write","arguments":{"file_path":"other.txt","content":"y = 3\n"}}]}]' > "$script"
api_key="sk-gate-$(date +%s%N)$RANDOM"

"$python" -m cmcoder.testing.mock_server --script "$(native "$script")" --port 0 --api-key "$api_key" > "$work/mock.out" 2>&1 &
mock=$!
ide_pid=
cleanup() {
  kill "$mock" 2>/dev/null || true
  if [ -n "$ide_pid" ]; then kill "$ide_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT
for _ in $(seq 600); do
  grep -q '^mock server on ' "$work/mock.out" 2>/dev/null && break
  kill -0 "$mock" 2>/dev/null || break
  sleep 0.2
done
url=$(sed -n 's/^mock server on //p' "$work/mock.out" | head -1 | tr -d '\r')
if [ -z "$url" ]; then
  echo "--- mock server output:"; cat "$work/mock.out"
  echo "mock server didn't start" >&2
  exit 1
fi
echo "Mock model server: $url"

# A fresh profile, no first-start dialogs (privacy policy, data sharing,
# settings import, tips), the project trusted (a developer opening their own
# project); IntelliJ makes each line here a system property.
{
  echo "idea.config.path=$(setting "$work/config")"
  echo "idea.system.path=$(setting "$work/system")"
  echo "idea.plugins.path=$(setting "$work/plugins")"
  echo "idea.log.path=$(setting "$work/log")"
  echo "jb.privacy.policy.text=<!--999.999-->"
  echo "jb.consents.confirmation.enabled=false"
  echo "idea.initially.ask.config=never"
  echo "ide.show.tips.on.startup.default.value=false"
  echo "ide.newUsersOnboarding=false"
  echo "idea.trust.all.projects=true"
  echo "cmcoder.gate=$(setting "$result")"
  echo "cmcoder.gate.project=$(setting "$project")"
  [ -n "${CMCODER_GATE_SHOTS:-}" ] && echo "cmcoder.gate.shots=$(setting "$CMCODER_GATE_SHOTS")"
  true
} > "$work/idea.properties"

export CMCODER_BASE_URL="$url" CMCODER_API_KEY="$api_key" CMCODER_MODEL=qwen3-27b
export CMCODER_CONFIG_DIR="$(native "$work/cmcoder")" PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring
export "$properties_variable=$(native "$work/idea.properties")"
unset HTTPS_PROXY HTTP_PROXY ALL_PROXY https_proxy http_proxy all_proxy JAVA_TOOL_OPTIONS JAVA_HOME || true
if [ "$ide_looks_for_python" = 1 ]; then export CMCODER_PYTHON_TRAP_IDE="$(cd "$ide" && pwd -P)"; fi

"$launcher" "$(native "$project")" > "$work/ide.out" 2>&1 &
ide_pid=$!

end=$(( $(date +%s) + timeout_s ))
# (On Windows a launcher may hand over to another process and end: there, only the time limit counts.)
alive() { [ "$windows" = 1 ] || kill -0 "$ide_pid" 2>/dev/null; }
while [ ! -f "$result" ] && [ "$(date +%s)" -lt "$end" ] && alive; do sleep 2; done
show() { [ -f "$1" ] && { echo "--- $(basename "$1"):"; sed 's/^/  /' "$1"; } || true; }
if [ ! -f "$result" ]; then
  show "$result.steps"
  if [ -n "${CMCODER_GATE_SHOTS:-}" ] && [ -n "${DISPLAY:-}" ] && command -v import >/dev/null; then
    mkdir -p "$CMCODER_GATE_SHOTS" && import -window root "$CMCODER_GATE_SHOTS/intellij-timeout.png" || true
  fi
  tail -40 "$work/ide.out" 2>/dev/null | sed 's/^/  ide: /' || true
  if [ "$windows" = 0 ] && kill -0 "$ide_pid" 2>/dev/null; then
    # The JVM prints its threads on SIGQUIT: the UI thread's and the gate's say what they wait for.
    kill -QUIT "$ide_pid" 2>/dev/null || true
    sleep 3
    echo "--- the IDE's UI thread and the gate's (thread dump):"
    awk '/^"/ { show = ($0 ~ /AWT-EventQueue|cmcoder gate/) } show' "$work/ide.out" 2>/dev/null | head -150 | sed 's/^/  /' || true
  fi
  echo "--- the plugin in the IDE's log:"
  grep -E 'custom plugins|PluginManager.*(cmcoder|disabled|incompatible)|cmcoder|SEVERE' "$work/log/idea.log" 2>/dev/null | head -40 | cut -c1-400 | sed 's/^/  log: /' || true
  echo "--- the IDE's log, last lines:"
  tail -60 "$work/log/idea.log" 2>/dev/null | cut -c1-400 | sed 's/^/  log: /' || true
  echo "No gate result after $timeout_s s (the IDE still running: $(kill -0 "$ide_pid" 2>/dev/null && echo yes || echo no))" >&2
  exit 1
fi
for _ in $(seq 90); do kill -0 "$ide_pid" 2>/dev/null || break; sleep 1; done
if kill -0 "$ide_pid" 2>/dev/null; then echo "(the IDE didn't close within 90 s of the result)"; fi
sleep 3 # the IDE ends cmcoder as it closes the project: nothing may be left running
ide_pid=

# The report is Json.write's output: {"ok":true,...}, the steps one per line below.
echo "Result: $(head -c 4000 "$result")" | sed 's/\\n/\n  /g'
if ! grep -q '"ok":true' "$result"; then
  grep -E 'cmcoder|SEVERE|ERROR' "$work/log/idea.log" 2>/dev/null | tail -60 | sed 's/^/  log: /' || true
  # The IDE saves thread dumps when its UI or write actions stall: the UI thread's stack says why.
  for dump in "$work"/log/*/thread-dump-*.txt "$work"/log/threadDumps-*/*.txt; do
    [ -f "$dump" ] || continue
    echo "--- the UI thread in $(basename "$(dirname "$dump")")/$(basename "$dump"):"
    awk '/^"/ { show = ($0 ~ /AWT-EventQueue|cmcoder gate/) } show' "$dump" | head -60 | sed 's/^/  /' || true
    break
  done
  echo "The IntelliJ gate failed." >&2
  exit 1
fi
echo "The IntelliJ gate passed ($product on $system)."
