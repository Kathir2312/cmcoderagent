#!/bin/sh
# Installs cmcoder for this user (macOS, Linux): no root, no Python.
#   sh install.sh
# Copies the program to ~/.cmcoder/bin, the one cmcoder for the terminal and
# every IDE (the IDEs use it too, when it's at least as new as their own
# copy), links ~/.local/bin/cmcoder to it, and if ~/.local/bin isn't on your
# PATH yet, adds it in ~/.profile (and ~/.zprofile on macOS). It also makes
# ~/.cmcoder with your company's settings, if they came with this package and
# you have none yet. An install in the old place (~/.local/share/cmcoder) is
# moved. Run it again to update; uninstall.sh removes the program.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
home_dir=${CMCODER_CONFIG_DIR:-"$HOME/.cmcoder"}
dest=${CMCODER_INSTALL_DIR:-"$home_dir/bin"}
old=${CMCODER_OLD_INSTALL_DIR:-"$HOME/.local/share/cmcoder"}
bin=${CMCODER_BIN_DIR:-"$HOME/.local/bin"}
marker="# added by cmcoder's install.sh"

if [ ! -x "$here/cmcoder/cmcoder" ] && [ ! -f "$here/cmcoder/cmcoder" ]; then
    echo "cmcoder isn't next to this script ($here/cmcoder). Unzip the whole file first." >&2
    exit 1
fi

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
for folder in "$dest" "$old"; do
    if in_use "$folder"; then
        echo "cmcoder is running from $folder (in a terminal or an IDE). Close it, then run this again." >&2
        exit 1
    fi
done

moved=""
if [ "$old" != "$dest" ] && [ -f "$old/cmcoder" ]; then
    rm -rf "$old"
    moved="$old"
fi

# The program, and what cmcoder's own updates leave next to it.
rm -rf "$dest" "$dest".new-* "$dest".old-*
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

# Its first run makes ~/.cmcoder (and settings.json from your company's
# template, if this build has one and you have no settings yet).
version=$("$dest/cmcoder" --version)
echo "Installed cmcoder $version in $dest."
if [ -n "$moved" ]; then
    echo "Moved it from $moved (removed there)."
fi
echo "$path_note"
if [ -f "$home_dir/settings.json" ]; then
    echo "Your settings: $home_dir/settings.json"
else
    echo "No settings yet: run  cmcoder setup  (the gateway, your API key, the models)."
fi
other=$(command -v cmcoder 2>/dev/null || true)
if [ -n "$other" ] && [ "$other" != "$bin/cmcoder" ]; then
    echo "Warning: another cmcoder comes first on your PATH: $other. Remove it, or it runs instead." >&2
fi
echo
echo "Open a NEW terminal, then:  cmcoder doctor"
