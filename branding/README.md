# Branding

Replace these files with your company's before building, then build as usual
(`packaging/build.py`, `packaging/vsix.py`). See docs/phase4/branding.md.

| File | What it is | Rules |
|---|---|---|
| `icon.png` | The full-colour icon: VS Code's Extensions list, the chat panel, the terminal tab, `cmcoder.exe`, the Windows Terminal profile | PNG, square, at least 256×256 (512 or 1024 recommended), transparent background |
| `icon-mono.svg` | VS Code's side bar (activity bar) | SVG, one colour drawn with `currentColor` (VS Code colours it to the theme), 24×24 viewBox |
| `brand.json` | `productName` (the name shown everywhere), `publisher` (VS Code publisher ID), `company`, `copyright` (Windows file details), `accentColor` (`#RRGGBB`) | see docs/phase4/branding.md |
| `logo.txt` | Text logo shown when the terminal UI starts (optional; delete to show none) | at most 8 lines of 60 characters |

The command stays `cmcoder`, and settings keep their `cmcoder.` names.
