#!/usr/bin/env bash
# Builds the JetBrains plugin (IntelliJ IDEA, PyCharm, Rider, WebStorm, GoLand
# and the other JetBrains IDEs, 2024.2+) for developers' PCs:
# dist/cmcoder-jetbrains-<platform>.zip (darwin-arm64, linux-x64), with cmcoder
# inside. Developers need the IDE only: no Python, uv or Node.js.
# (Windows: packaging/package-jetbrains.ps1.)
#
# Run it on a build machine (not the developers'), in the repository:
#
#     bash packaging/package-jetbrains.sh [--skip-tests] [--use-built]
#
# The build machine needs uv (https://docs.astral.sh/uv/), Node.js 22, JDK 21,
# Gradle 9.8.1, access to JetBrains' servers, and git with the repository as a
# git clone. docs/building/intellij.md has the details.
#
# It does what the Release workflow does for this file: builds cmcoder with
# PyInstaller, runs it with no Python in reach, builds the chat page, builds
# the plugin with cmcoder inside, and checks cmcoder is in the file.
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
need_node
need_java 21
need_gradle
check_git
build_cmcoder
build_panel

step "The plugin, with cmcoder inside (the first build downloads the IntelliJ Platform)"
rm -f jetbrains/build/distributions/cmcoder-jetbrains-*.zip
(cd jetbrains && gradle --no-daemon "-PcmcoderDir=$root/dist/cmcoder" buildPlugin)

built="$(ls jetbrains/build/distributions/cmcoder-jetbrains-*.zip 2>/dev/null | grep -v -- '-gate\.zip$' | head -n 1 || true)"
[ -n "$built" ] || die "The build made no jetbrains/build/distributions/cmcoder-jetbrains-*.zip"
out="$root/dist/cmcoder-jetbrains-$target.zip"
cp "$built" "$out"
zip_has "$out" "/bin/cmcoder/cmcoder" "/panel/chat.js"
done_message "$out" "Developers: Settings > Plugins > gear > Install Plugin from Disk..., the zip as it is."
