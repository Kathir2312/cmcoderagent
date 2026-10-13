#!/bin/sh
# Removes what install.sh added: the program (~/.cmcoder/bin), its link and
# the PATH lines. Your settings, sessions and keys (the rest of ~/.cmcoder and
# the system keychain) are kept. The IDE plugins keep working with their own
# copy of cmcoder.
set -eu

home_dir=${CMCODER_CONFIG_DIR:-"$HOME/.cmcoder"}
dest=${CMCODER_INSTALL_DIR:-"$home_dir/bin"}
bin=${CMCODER_BIN_DIR:-"$HOME/.local/bin"}
marker="# added by cmcoder's install.sh"

# Whether a cmcoder runs from folder $1 (a terminal, or an IDE using it):
# replacing its files under it would crash it.
in_use() {
    [ -f "$1/cmcoder" ] || return 1
    if command -v lsof >/dev/null 2>&1 && lsof -t "$1/cmcoder" >/dev/null 2>&1; then
        return 0
    fi
    real=$(cd "$1" && pwd -P)
    for exe in /proc/[0-9]*/exe; do
        case $(readlink "$exe" 2>/dev/null) in "$real"/*) return 0 ;; esac
    done
    return 1
}
if in_use "$dest"; then
    echo "cmcoder is running from $dest (in a terminal or an IDE). Close it, then run this again." >&2
    exit 1
fi
rm -rf "$dest" "$dest".new-* "$dest".old-* "$dest".running "$dest".updating
if [ -L "$bin/cmcoder" ]; then
    rm -f "$bin/cmcoder"
fi
for rc in "$HOME/.profile" "$HOME/.zprofile"; do
    if grep -qs "$marker" "$rc"; then
        # The marker line and the export line after it.
        awk -v m="$marker" '$0 == m { skip = 2 } skip > 0 { skip--; next } { print }' "$rc" > "$rc.cmcoder-tmp"
        mv "$rc.cmcoder-tmp" "$rc"
    fi
done
echo "Removed cmcoder from $dest and $bin."
