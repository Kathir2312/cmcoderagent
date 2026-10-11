# Branding

Replace these files with your company's before building, then build as usual
(`packaging/build.py`, `packaging/vsix.py`). See docs/phase4/branding.md.

| File | What it is | Rules |
|---|---|---|
| `icon.png` | The full-colour icon: VS Code's Extensions list, the chat panel, the terminal tab, `cmcoder.exe`, the Windows Terminal profile | PNG, square, at least 256×256 (512 or 1024 recommended), transparent background |
| `icon-mono.svg` | VS Code's side bar (activity bar) | SVG, one colour drawn with `currentColor` (VS Code colours it to the theme), 24×24 viewBox |
| `brand.json` | `productName` (the name shown everywhere), `publisher` (VS Code publisher ID), `company`, `copyright` (Windows file details), `accentColor` (`#RRGGBB`) | see docs/phase4/branding.md |
| `logo.txt` | Text logo shown when the terminal UI starts (optional; delete to show none) | at most 8 lines of 60 characters |
| `settings.template.json` | Optional. Your company's settings (gateway, certificate, models): becomes each developer's `~/.cmcoder/settings.json` the first time cmcoder starts, never overwriting one they have | a `settings.json` ([docs/settings.example.json](../docs/settings.example.json)); no API keys, no example addresses |
| `managed-settings.json` | Optional. Rules developers can't change: goes into the release bundle's `admin/` folder, for IT to install on each PC (docs/guides/admin.md, section 5) | start from [docs/managed-settings.example.json](../docs/managed-settings.example.json) |

The build checks both settings files (valid JSON, valid settings, no example
addresses) and stops if one is wrong: a broken file would stop cmcoder on
every PC.

The command stays `cmcoder`, and settings keep their `cmcoder.` names.
