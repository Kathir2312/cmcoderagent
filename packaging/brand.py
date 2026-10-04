"""Check the `branding/` folder and make what the builds need from it.

    uv run python packaging/brand.py     # check only

Called by packaging/build.py and packaging/vsix.py. Writes to build/brand/:

    icon-256.png      VS Code (Extensions list, chat panel, terminal tab)
    cmcoder.ico       cmcoder.exe and the Windows Terminal profile (16-256 px)
    version-info.txt  cmcoder.exe's file details (PyInstaller version file)

Pillow (a dev dependency) is needed only here, at build time.
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET  # nosec B405: our own branding file, at build time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cmcoder import __version__  # noqa: E402
from cmcoder.brand import problems, read_folder  # noqa: E402

BRANDING = ROOT / "branding"
OUT = ROOT / "build" / "brand"
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]
MIN_ICON = 256
# Colours a one-colour SVG may use: VS Code paints currentColor to the theme.
MONO_OK = {"currentcolor", "none", "inherit", ""}


class BrandError(Exception):
    pass


def check_png(path: Path) -> list[str]:
    from PIL import Image

    if not path.is_file():
        return [f"{path.name} is missing"]
    try:
        with Image.open(path) as img:
            fmt, size, mode = img.format, img.size, img.mode
            transparent = mode in ("RGBA", "LA", "PA") or "transparency" in img.info
    except OSError as e:
        return [f"{path.name}: not an image Pillow can read ({e})"]
    out = []
    if fmt != "PNG":
        out.append(f"{path.name} must be a PNG (it's {fmt})")
    if size[0] != size[1]:
        out.append(f"{path.name} must be square (it's {size[0]}×{size[1]})")
    elif size[0] < MIN_ICON:
        out.append(f"{path.name} must be at least {MIN_ICON}×{MIN_ICON} (it's {size[0]})")
    if not transparent:
        out.append(f"{path.name} should have a transparent background (no alpha channel)")
    return out


def check_mono_svg(path: Path) -> list[str]:
    if not path.is_file():
        return [f"{path.name} is missing"]
    try:
        root = ET.parse(path).getroot()  # nosec B314: our own branding file
    except ET.ParseError as e:
        return [f"{path.name}: not valid SVG ({e})"]
    out = []
    if not root.tag.endswith("svg"):
        out.append(f"{path.name}: the root element must be <svg>")
    for el in root.iter():
        if el.tag.endswith(("image", "script", "foreignObject")):
            out.append(f"{path.name}: <{el.tag.split('}')[-1]}> isn't allowed")
        styles = dict(el.attrib)
        for decl in el.attrib.get("style", "").split(";"):
            key, _, value = decl.partition(":")
            styles[key.strip()] = value
        for key in ("fill", "stroke", "color", "stop-color"):
            value = str(styles.get(key, "")).strip().lower()
            if value not in MONO_OK:
                out.append(
                    f"{path.name}: {key}={value!r}; draw with currentColor so VS Code can "
                    "colour it to the theme"
                )
    return sorted(set(out))


def load(folder: Path = BRANDING) -> dict[str, str]:
    """brand.json with defaults filled in; raises BrandError listing every problem."""
    try:
        data, logo = read_folder(folder)
    except FileNotFoundError:
        raise BrandError(f"{folder / 'brand.json'} is missing") from None
    except ValueError as e:
        raise BrandError(f"brand.json: invalid JSON ({e})") from None
    found = problems(data, logo)
    found += check_png(folder / "icon.png")
    found += check_mono_svg(folder / "icon-mono.svg")
    if found:
        raise BrandError("branding/ has problems:\n  - " + "\n  - ".join(found))
    assert isinstance(data, dict)
    return {
        "productName": str(data.get("productName", "cmcoder")).strip(),
        "publisher": str(data.get("publisher", "cmcoder")),
        "company": str(data.get("company", "")),
        "copyright": str(data.get("copyright", "")),
        "accentColor": str(data.get("accentColor", "#4F8EF7")),
    }


def version_info(brand: dict[str, str]) -> str:
    """PyInstaller's --version-file format (a VSVersionInfo expression)."""
    nums = [int(x) for x in __version__.split(".")[:3] if x.isdigit()]
    v = tuple((nums + [0, 0, 0, 0])[:4])
    strings = {
        "CompanyName": brand["company"],
        "FileDescription": brand["productName"],
        "FileVersion": __version__,
        "InternalName": "cmcoder",
        "LegalCopyright": brand["copyright"],
        "OriginalFilename": "cmcoder.exe",
        "ProductName": brand["productName"],
        "ProductVersion": __version__,
    }
    entries = ",\n          ".join(f"StringStruct({k!r}, {val!r})" for k, val in strings.items())
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={v}, prodvers={v}, mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
          {entries}])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def _rename(text: str, name: str) -> str:
    # The word only: never command IDs or settings (cmcoder.executable).
    return re.sub(r"\bcmcoder\b(?![.\w-])", name, text)


def brand_package_json(data: dict[str, Any], brand: dict[str, str]) -> dict[str, Any]:
    """The extension's package.json with the brand's name and publisher.
    The publisher is part of the extension's ID: changing it makes VS Code
    treat it as a different extension."""
    out = json.loads(json.dumps(data))
    name = brand["productName"]
    out["displayName"] = name
    out["publisher"] = brand["publisher"]
    contributes = out.get("contributes", {})
    for container in contributes.get("viewsContainers", {}).get("activitybar", []):
        container["title"] = _rename(container.get("title", ""), name)
    for command in contributes.get("commands", []):
        for key in ("title", "category"):
            if isinstance(command.get(key), str):
                command[key] = _rename(command[key], name)
    configuration = contributes.get("configuration")
    if isinstance(configuration, dict) and isinstance(configuration.get("title"), str):
        configuration["title"] = _rename(configuration["title"], name)
    return out


def generate(folder: Path = BRANDING, out: Path = OUT) -> dict[str, str]:
    from PIL import Image

    brand = load(folder)
    out.mkdir(parents=True, exist_ok=True)
    with Image.open(folder / "icon.png") as src:
        img = src.convert("RGBA")
    img.resize((256, 256), Image.Resampling.LANCZOS).save(out / "icon-256.png", optimize=True)
    img.save(out / "cmcoder.ico", sizes=[(s, s) for s in ICO_SIZES])
    (out / "version-info.txt").write_text(version_info(brand), encoding="utf-8")
    (out / "brand.json").write_text(json.dumps(brand, indent=2), encoding="utf-8")
    return brand


def main() -> None:
    try:
        brand = generate()
    except BrandError as e:
        sys.exit(str(e))
    print(f"branding OK: {brand['productName']} (publisher {brand['publisher']}) -> {OUT}")


if __name__ == "__main__":
    main()
