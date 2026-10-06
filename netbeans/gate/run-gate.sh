#!/usr/bin/env bash
# The release gate for NetBeans (Phase 6 item 11): puts the gate build of the
# module (.nbm with cmcoder and the self-test inside) into a fresh NetBeans
# user folder, as NetBeans' installer lays it out, starts the mock model
# server, starts NetBeans, and waits for the module's self-test (Gate.java) to
# write its result. Fails unless every step passed.
#
#   run-gate.sh --nbm path/to/gate.nbm --netbeans path/to/netbeans --python path/to/python [--jdk path]
#
# --python is a Python with cmcoder, for the mock server only (test
# equipment); NetBeans and cmcoder run without it (run this under
# packaging/no_python.py so nothing finds Python on PATH). On Linux, run it
# under xvfb-run. Works in Git Bash on Windows.
set -euo pipefail

timeout_s=900
while [ $# -gt 0 ]; do
  case "$1" in
    --nbm) nbm=$2; shift 2 ;;
    --netbeans) netbeans=$2; shift 2 ;;
    --python) python=$2; shift 2 ;;
    --jdk) jdk=$2; shift 2 ;;
    --timeout) timeout_s=$2; shift 2 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
: "${nbm:?--nbm is required}" "${netbeans:?--netbeans is required}" "${python:?--python is required}"

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    windows=1
    netbeans=$(cygpath -u "$netbeans"); nbm=$(cygpath -u "$nbm")
    [ -n "${jdk:-}" ] && jdk=$(cygpath -u "$jdk")
    launcher="$netbeans/bin/netbeans64.exe" ;;
  *) windows=0; launcher="$netbeans/bin/netbeans" ;;
esac
native() { if [ "$windows" = 1 ]; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
echo "NetBeans: $netbeans ($(grep -o 'netbeans-[0-9][0-9]*' "$netbeans/nb/.lastModified" 2>/dev/null || ls "$netbeans"/nb/core/*.jar 2>/dev/null | head -1))"

work=$(mktemp -d "${TMPDIR:-/tmp}/cmcoder-nb-gate-XXXXXX")
project="$work/project"
mkdir -p "$project/.git" "$work/config" "$work/userdir" "$work/cache" "$work/nbm"
# Plain text: a fresh NetBeans enables Java support only when asked (a dialog).
printf 'first line\nsecond line\n' > "$project/app.txt"
result="$work/result.json"

# The module's files where NetBeans' installer puts a plugin: the user folder.
if command -v unzip >/dev/null; then unzip -q "$nbm" 'netbeans/*' -d "$work/nbm"
else (cd "$work/nbm" && "${jdk:-$JAVA_HOME}/bin/jar" xf "$(native "$(cd "$(dirname "$nbm")" && pwd)/$(basename "$nbm")")"); fi
cp -R "$work/nbm/netbeans/." "$work/userdir/"
test -f "$work/userdir/config/Modules/cmcoder-netbeans.xml"

# The model's replies, in order: a getDiagnostics call, a reply, a Write
# accepted, a reply, a Write rejected (the turn stops there).
script="$work/script.json"
printf '%s' '[{"tool_calls":[{"name":"getDiagnostics","arguments":{}}]},{"content":"Checked the problems."},{"tool_calls":[{"name":"Write","arguments":{"file_path":"new.txt","content":"x = 2\n"}}]},{"content":"Wrote new.txt."},{"tool_calls":[{"name":"Write","arguments":{"file_path":"other.txt","content":"y = 3\n"}}]}]' > "$script"
api_key="sk-gate-$(date +%s%N)$RANDOM"

"$python" -m cmcoder.testing.mock_server --script "$(native "$script")" --port 0 --api-key "$api_key" > "$work/mock.out" 2>&1 &
mock=$!
nb_pid=
cleanup() {
  kill "$mock" 2>/dev/null || true
  if [ -n "$nb_pid" ]; then kill "$nb_pid" 2>/dev/null || true; fi
}
trap cleanup EXIT
# A first start can be slow (compiling, a virus scan or Gatekeeper on macOS): up to 2 minutes.
for _ in $(seq 600); do
  grep -q '^mock server on ' "$work/mock.out" 2>/dev/null && break
  kill -0 "$mock" 2>/dev/null || break
  sleep 0.2
done
url=$(sed -n 's/^mock server on //p' "$work/mock.out" | head -1 | tr -d '\r')
if [ -z "$url" ]; then
  echo "--- mock server output:"; cat "$work/mock.out"
  echo "mock server didn't start ($(kill -0 "$mock" 2>/dev/null && echo still running || echo it exited))" >&2
  exit 1
fi
echo "Mock model server: $url"

export CMCODER_BASE_URL="$url" CMCODER_API_KEY="$api_key" CMCODER_MODEL=qwen3-27b
export CMCODER_CONFIG_DIR="$(native "$work/config")" PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring
unset HTTPS_PROXY HTTP_PROXY ALL_PROXY https_proxy http_proxy all_proxy JAVA_TOOL_OPTIONS || true

args=(--userdir "$(native "$work/userdir")" --cachedir "$(native "$work/cache")" --nosplash
      "-J-Dcmcoder.gate=$(native "$result")" "-J-Dcmcoder.gate.project=$(native "$project")"
      -J-Dplugin.manager.check.updates=false -J-Dnetbeans.close.no.question=true)
[ -n "${jdk:-}" ] && args+=(--jdkhome "$(native "$jdk")")
"$launcher" "${args[@]}" > "$work/netbeans.out" 2>&1 &
nb_pid=$!

end=$(( $(date +%s) + timeout_s ))
# (On Windows the launcher may hand over to Java and end: there, only the time limit counts.)
alive() { [ "$windows" = 1 ] || kill -0 "$nb_pid" 2>/dev/null; }
while [ ! -f "$result" ] && [ "$(date +%s)" -lt "$end" ] && alive; do sleep 2; done
show() { [ -f "$1" ] && { echo "--- $(basename "$1"):"; sed 's/^/  /' "$1"; } || true; }
if [ ! -f "$result" ]; then
  show "$result.steps"
  tail -40 "$work/netbeans.out" | sed 's/^/  netbeans: /'
  tail -60 "$work/userdir/var/log/messages.log" 2>/dev/null | sed 's/^/  log: /'
  echo "No gate result after $timeout_s s (NetBeans still running: $(kill -0 "$nb_pid" 2>/dev/null && echo yes || echo no))" >&2
  exit 1
fi
for _ in $(seq 60); do kill -0 "$nb_pid" 2>/dev/null || break; sleep 1; done
sleep 3 # NetBeans ends cmcoder as it closes: nothing may be left running
nb_pid=

# The report is Json.write's output: {"ok":true,...}, the steps one per line below.
echo "Result: $(head -c 2000 "$result")" | sed 's/\\n/\n  /g'
if ! grep -q '"ok":true' "$result"; then
  grep -E 'cmcoder|SEVERE|Exception' "$work/userdir/var/log/messages.log" 2>/dev/null | tail -40 | sed 's/^/  log: /'
  echo "The NetBeans gate failed." >&2
  exit 1
fi
echo "The NetBeans gate passed."
