# Branding: your icon and name

cmcoder's icon and name come from one folder, `branding/`. Replace its files
with your company's, then build as usual. Nothing else needs editing.

## 1. Replace the files

| File | Used for | Rules |
|---|---|---|
| `icon.png` | VS Code's Extensions list, the chat panel (empty screen and the small icon beside each reply), the terminal tab, `cmcoder.exe` (Explorer, taskbar), the Windows Terminal profile | PNG, **square**, at least **256×256** (512 or 1024 recommended), **transparent** background. Keep it readable at 16 px: a simple mark, little or no text. |
| `icon-mono.svg` | VS Code's side bar (activity bar) | SVG drawn in **one colour with `currentColor`** (VS Code colours it to match the theme), 24×24 `viewBox`. No embedded images or scripts. |
| `brand.json` | Names and colour (below) | |
| `logo.txt` | Text logo when the terminal UI starts | Optional (delete it to show none). At most 8 lines of 60 characters, plain text (no escape codes). |

`brand.json`:

```json
{
  "productName": "Acme Coder",
  "publisher": "acme",
  "company": "Acme Ltd",
  "copyright": "© 2026 Acme Ltd",
  "accentColor": "#E4002B"
}
```

| Key | Where it shows |
|---|---|
| `productName` | VS Code (the extension's name, the side bar title, command categories, the chat panel, messages), the terminal UI's header, the terminal window title, the Windows Terminal profile, `cmcoder.exe`'s file details. At most 40 characters. |
| `publisher` | VS Code's publisher ID: letters, digits and hyphens. **It is part of the extension's ID** (`publisher.cmcoder`): change it once, before you hand the extension out. Changing it later makes VS Code treat it as a different extension (people uninstall the old one; settings carry over, as they're named `cmcoder.*`). |
| `company`, `copyright` | `cmcoder.exe` → Properties → Details. |
| `accentColor` | The terminal UI's logo, header and border (`#RRGGBB`). |

What keeps its name: the `cmcoder` command, its settings (`cmcoder.executable`,
`~/.cmcoder/`), the VS Code command IDs and environment variables
(`CMCODER_*`). Renaming those would break existing setups and scripts.

## 2. Check

```
uv run python packaging/brand.py
```

This prints `branding OK: …` or lists every problem (not square, too small,
no transparency, colours in the side-bar SVG, an invalid publisher, …).
`build.py` and `vsix.py` run the same check and stop on a problem.

## 3. Build

As in [the standalone build](PLAN.md#5-standalone-binary-and-per-platform-vs-code-extension),
on each platform you release for:

```
uv run --with pyinstaller python packaging/build.py    # dist/cmcoder/
uv run python packaging/vsix.py                         # vscode/cmcoder-<platform>.vsix
```

Run **both** after changing `branding/`: the VSIX bundles the cmcoder that
`build.py` made, and that one carries the name, logo and icon too.

- `build.py` puts the branding inside the build and, on Windows, gives
  `cmcoder.exe` the icon (16–256 px) and the file details.
- `vsix.py` puts the icons, `productName` and `publisher` into the packaged
  extension. Your checked-out `vscode/package.json` and `vscode/media/` are
  put back afterwards, so the repository stays as it was.
- The **Release build** workflow (GitHub Actions) uses the `branding/` of the
  commit it builds: commit your files to your fork and run it.

A pip/uv install (`uv tool install`) carries the branding too: it is part of
the package.

## 4. Windows Terminal profile

Each user who wants **cmcoder in Windows Terminal's drop-down** (with your
name and icon) runs once:

```
cmcoder terminal-profile             # add (or update) the profile
cmcoder terminal-profile --remove    # remove it
cmcoder terminal-profile --print     # show it, change nothing
```

It's a Windows Terminal "fragment"
(`%LOCALAPPDATA%\Microsoft\Windows Terminal\Fragments\<productName>\cmcoder.json`):
Windows Terminal's own settings file isn't touched. Open a new Windows
Terminal window to see the profile. It starts the cmcoder you ran the command
with, in your home folder. Run it again after moving or reinstalling cmcoder.

## What can't be branded

- **macOS and Linux:** a command-line program has no icon there; only the
  name, logo and colour show (in the terminal).
- **A terminal window's icon** (other than Windows Terminal's profile): set by
  the terminal program, not by programs running in it.
- **Extensions list in VS Code before packaging:** a development checkout (F5)
  shows the default icon and name, since the branding goes into the packaged
  copy.
