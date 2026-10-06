#!/bin/sh
# Installs cmcoder for this user (macOS, Linux): no root, no Python.
#   sh install.sh
# Copies the program to ~/.local/share/cmcoder, links ~/.local/bin/cmcoder to
# it, and if ~/.local/bin isn't on your PATH yet, adds it in ~/.profile (and
# ~/.zprofile on macOS). Run it again to update; uninstall.sh removes it all.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
dest=${CMCODER_INSTALL_DIR:-"$HOME/.local/share/cmcoder"}
bin=${CMCODER_BIN_DIR:-"$HOME/.local/bin"}
marker="# added by cmcoder's install.sh"

if [ ! -x "$here/cmcoder/cmcoder" ] && [ ! -f "$here/cmcoder/cmcoder" ]; then
    echo "cmcoder isn't next to this script ($here/cmcoder). Unzip the whole file first." >&2
    exit 1
fi

rm -rf "$dest"
mkdir -p "$dest" "$bin"
cp -R "$here/cmcoder/." "$dest/"
chmod +x "$dest/cmcoder"
# macOS: a downloaded file is quarantined and Gatekeeper refuses to start an
# unsigned program; this is the program you chose to install.
if [ "$(uname)" = Darwin ]; then
    xattr -dr com.apple.quarantine "$dest" 2>/dev/null || true
fi
ln -sf "$dest/cmcoder" "$bin/cmcoder"

# The files a login shell reads: ~/.profile, and on macOS (zsh) ~/.zprofile.
profiles="$HOME/.profile"
profile_names="~/.profile"
if [ "$(uname)" = Darwin ]; then
    profiles="$profiles $HOME/.zprofile"
    profile_names="~/.profile and ~/.zprofile"
fi
case ":$PATH:" in
*":$bin:"*) path_note="$bin is already on your PATH." ;;
*)
    for rc in $profiles; do
        if ! grep -qs "$marker" "$rc"; then
            # Start on a new line; uninstall.sh removes exactly these two lines.
            if [ -s "$rc" ] && [ -n "$(tail -c 1 "$rc")" ]; then
                echo >> "$rc"
            fi
            printf '%s\nexport PATH="%s:$PATH"\n' "$marker" "$bin" >> "$rc"
        fi
    done
    path_note="Added $bin to your PATH (in $profile_names)."
    ;;
esac

version=$("$dest/cmcoder" --version)
echo "Installed cmcoder $version in $dest."
echo "$path_note"
other=$(command -v cmcoder 2>/dev/null || true)
if [ -n "$other" ] && [ "$other" != "$bin/cmcoder" ]; then
    echo "Warning: another cmcoder comes first on your PATH: $other. Remove it, or it runs instead." >&2
fi
echo
echo "Open a NEW terminal, then:  cmcoder doctor"
