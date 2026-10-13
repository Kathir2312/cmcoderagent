#!/bin/sh
# Installs your company's managed settings on this Mac or Linux PC: the rules
# developers can't change (docs/admin/README.md, section 5). Run it as root:
#   sudo sh install-managed.sh [file]
# Copies managed-settings.json (next to this script, or the file you name) to
#   macOS: /Library/Application Support/cmcoder/managed-settings.json
#   Linux: /etc/cmcoder/managed-settings.json
# which every user's cmcoder reads and only root can change. For many PCs,
# deploy that same file with your device management instead.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
src=${1:-"$here/managed-settings.json"}

if [ ! -f "$src" ]; then
    echo "No managed settings to install: $src is missing. Fill in managed-settings.example.json and save it as managed-settings.json next to this script." >&2
    exit 1
fi

# A broken file stops cmcoder for everyone on this PC: check it first, with
# what this PC has (jq, macOS's JavaScript, or python3).
is_object() {
    if command -v jq >/dev/null 2>&1; then
        jq -e 'type == "object"' "$1" >/dev/null 2>&1
    elif [ "$(uname)" = Darwin ] && command -v osascript >/dev/null 2>&1; then
        osascript -l JavaScript -e 'function run(argv) {
            const text = $.NSString.stringWithContentsOfFileEncodingError(argv[0], $.NSUTF8StringEncoding, $()).js;
            const d = JSON.parse(text.replace(/^\uFEFF/, ""));
            if (typeof d !== "object" || d === null || Array.isArray(d)) throw new Error("not an object");
        }' "$1" >/dev/null 2>&1
    elif command -v python3 >/dev/null 2>&1; then
        python3 -I -c 'import json, sys; sys.exit(not isinstance(json.load(open(sys.argv[1], encoding="utf-8-sig")), dict))' "$1" 2>/dev/null
    else
        echo "Note: nothing here to check the JSON fully (jq, python3); check it with: cmcoder doctor" >&2
        squeezed=$(tr -d ' \t\r\n' < "$1")
        case $squeezed in "{"*"}") ;; *) return 1 ;; esac
    fi
}
bad=0
is_object "$src" || bad=1
if [ "$bad" = 1 ]; then
    echo "$src isn't valid JSON, or doesn't hold one JSON object: { ... }." >&2
    exit 1
fi
if grep -q 'YOUR-' "$src"; then
    echo "$src still has the example's placeholders (YOUR-...). Put in your company's values." >&2
    exit 1
fi

if [ -n "${CMCODER_MANAGED_TARGET:-}" ]; then
    target=$CMCODER_MANAGED_TARGET  # tests only: no root check
else
    if [ "$(id -u)" -ne 0 ]; then
        echo "Run this as root:  sudo sh $0" >&2
        exit 1
    fi
    if [ "$(uname)" = Darwin ]; then
        target="/Library/Application Support/cmcoder/managed-settings.json"
    else
        target=/etc/cmcoder/managed-settings.json
    fi
fi

verb=Installed
[ -f "$target" ] && verb=Replaced
mkdir -p "$(dirname "$target")"
chmod 755 "$(dirname "$target")"
cp "$src" "$target.new"
chmod 644 "$target.new"
mv -f "$target.new" "$target"
echo "$verb the managed settings: $target"
echo "Every user's cmcoder uses them from its next start. Check on a developer's PC:  cmcoder doctor"
