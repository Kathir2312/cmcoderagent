#!/usr/bin/env bash
# Builds the NetBeans plugin for developers' PCs:
# dist/cmcoder-netbeans-<platform>.nbm (darwin-arm64, linux-x64), with cmcoder
# and the chat's browser (OpenJFX) inside. Developers need NetBeans 28+ only:
# no Python, uv, Node.js or JavaFX. (Windows: packaging/package-netbeans.ps1.)
#
# Run it on a build machine (not the developers'), in the repository:
#
#     bash packaging/package-netbeans.sh [--skip-tests] [--use-built]
#
# The build machine needs uv (https://docs.astral.sh/uv/), Node.js 22, a JDK
# 17 or newer (21 recommended), Maven 3.9+, and git with the repository as a
# git clone. docs/build-package/netbeans.md has the details.
#
# It does what the Release workflow does for this file: builds cmcoder with
# PyInstaller, runs it with no Python in reach, builds the chat page, puts
# cmcoder in netbeans/bin, builds the .nbm for this platform, and checks
# cmcoder is in the file.
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
case "$target" in
  darwin-arm64) platform=mac-aarch64 ;;
  linux-x64) platform=linux ;;
esac
need_node
need_java 17
need_maven
check_git
build_cmcoder
build_panel

step "cmcoder into the plugin (netbeans/bin/cmcoder)"
rm -rf netbeans/bin && mkdir -p netbeans/bin
cp -RL dist/cmcoder netbeans/bin/cmcoder # -L: links as files

step "The .nbm for $target"
mvn_args=(-B package "-Dplatform=$platform")
[ "$skip_tests" = 1 ] && mvn_args+=(-DskipTests)
(cd netbeans && mvn "${mvn_args[@]}")

out="$root/dist/cmcoder-netbeans-$target.nbm"
cp "netbeans/target/cmcoder-netbeans-$target.nbm" "$out"
zip_has "$out" "/bin/cmcoder/cmcoder" "/panel/chat.js"
done_message "$out" "Developers: NetBeans > Tools > Plugins > Downloaded > Add Plugins..., then restart."
