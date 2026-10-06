#!/bin/sh
# Removes what install.sh added. Your settings, sessions and keys
# (~/.cmcoder and the system keychain) are kept.
set -eu

dest=${CMCODER_INSTALL_DIR:-"$HOME/.local/share/cmcoder"}
bin=${CMCODER_BIN_DIR:-"$HOME/.local/bin"}
marker="# added by cmcoder's install.sh"

rm -rf "$dest"
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
