"""Branding (Phase 4): the branding/ folder, its build-time checks and what
the CLI, VS Code packaging and the Windows Terminal profile make of it."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from cmcoder import brand
from cmcoder.cli import terminal_profile as wt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))

import brand as build_brand  # noqa: E402  (packaging/brand.py)


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    """A copy of the repository's branding/ to change."""
    f = tmp_path / "branding"
    shutil.copytree(ROOT / "branding", f)
    return f


def write_brand(folder: Path, **changes: Any) -> None:
    data = json.loads((folder / "brand.json").read_text("utf-8"))
    data.update(changes)
    (folder / "brand.json").write_text(json.dumps(data), "utf-8")


def use(monkeypatch: pytest.MonkeyPatch, folder: Path) -> None:
    monkeypatch.setattr(brand, "folders", lambda: [folder])
    brand.load.cache_clear()


@pytest.fixture(autouse=True)
def _fresh() -> Any:
    brand.load.cache_clear()
    yield
    brand.load.cache_clear()


def test_the_shipped_branding_is_valid() -> None:
    assert build_brand.load(ROOT / "branding")["productName"] == "cmcoder"
    b = brand.load()
    assert b.product_name == "cmcoder" and b.icon_png is not None and b.logo


def test_a_brand_is_used(monkeypatch: pytest.MonkeyPatch, folder: Path) -> None:
    write_brand(folder, productName="Acme Coder", accentColor="#112233")
    (folder / "logo.txt").write_text("ACME\n", "utf-8")
    use(monkeypatch, folder)
    b = brand.load()
    assert (b.product_name, b.accent_color, b.logo) == ("Acme Coder", "#112233", "ACME")


def test_a_bad_brand_never_stops_cmcoder(monkeypatch: pytest.MonkeyPatch, folder: Path) -> None:
    (folder / "brand.json").write_text("{not json", "utf-8")
    use(monkeypatch, folder)
    assert brand.load().product_name == "cmcoder"
    write_brand_text = json.dumps({"productName": "x" * 99})
    (folder / "brand.json").write_text(write_brand_text, "utf-8")
    use(monkeypatch, folder)
    assert brand.load().product_name == "cmcoder"


def test_text_rules() -> None:
    assert brand.problems({}, None) == []
    found = brand.problems(
        {
            "productName": "",
            "publisher": "acme corp",
            "accentColor": "blue",
            "company": "A\x07",
            "colour": 1,
        },
        "\x1b[31mred\x1b[0m\n" + "x" * 61,
    )
    text = "\n".join(found)
    for expected in (
        "unknown key 'colour'",
        "productName",
        "publisher",
        "accentColor",
        "company",
        "at most 8 lines of 60",
        "control characters",
    ):
        assert expected in text


def test_image_rules(folder: Path) -> None:
    Image.new("RGB", (300, 200)).save(folder / "icon.png")
    with pytest.raises(build_brand.BrandError) as e:
        build_brand.load(folder)
    assert "square" in str(e.value) and "transparent" in str(e.value)
    Image.new("RGBA", (64, 64)).save(folder / "icon.png")
    with pytest.raises(build_brand.BrandError, match="at least 256"):
        build_brand.load(folder)
    (folder / "icon.png").write_text("not an image")
    with pytest.raises(build_brand.BrandError, match="not an image"):
        build_brand.load(folder)


def test_the_side_bar_icon_is_one_colour(folder: Path) -> None:
    svg = folder / "icon-mono.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        '<path fill="#ff0000" d="M0 0h24v24H0z"/><path style="stroke: currentColor"/></svg>'
    )
    with pytest.raises(build_brand.BrandError, match="fill='#ff0000'"):
        build_brand.load(folder)
    svg.write_text("<svg><script>alert(1)</script></svg>")
    with pytest.raises(build_brand.BrandError, match="<script> isn't allowed"):
        build_brand.load(folder)


def test_build_outputs(folder: Path, tmp_path: Path) -> None:
    write_brand(folder, productName="Acme Coder", company="Acme Ltd")
    out = tmp_path / "out"
    b = build_brand.generate(folder, out)
    assert b["productName"] == "Acme Coder"
    with Image.open(out / "cmcoder.ico") as ico:
        assert {s[0] for s in ico.info["sizes"]} >= {16, 32, 48, 256}
    with Image.open(out / "icon-256.png") as png:
        assert png.size == (256, 256)
    info = (out / "version-info.txt").read_text("utf-8")
    assert "StringStruct('ProductName', 'Acme Coder')" in info
    assert "StringStruct('CompanyName', 'Acme Ltd')" in info


def test_vscode_package_json() -> None:
    data = json.loads((ROOT / "vscode" / "package.json").read_text("utf-8"))
    assert data["icon"] == "media/icon.png" and (ROOT / "vscode" / "media" / "icon.png").is_file()
    b = {"productName": "Acme Coder", "publisher": "acme"}
    out = build_brand.brand_package_json(data, {**b, "company": "", "copyright": ""})
    assert (out["displayName"], out["publisher"], out["name"]) == ("Acme Coder", "acme", "cmcoder")
    assert out["contributes"]["viewsContainers"]["activitybar"][0]["title"] == "Acme Coder"
    commands = {c["command"]: c for c in out["contributes"]["commands"]}
    assert commands["cmcoder.askAboutSelection"]["title"] == "Ask Acme Coder About Selection"
    assert all(c["category"] == "Acme Coder" for c in commands.values() if "category" in c)
    # IDs, settings and the program keep their names.
    assert "cmcoder.executable" in json.dumps(out) and data["displayName"] == "cmcoder"


def test_windows_terminal_profile(monkeypatch: pytest.MonkeyPatch, folder: Path) -> None:
    write_brand(folder, productName="Acme Coder")
    use(monkeypatch, folder)
    b = brand.load()
    env = {"LOCALAPPDATA": str(folder.parent / "local")}
    path = wt.install(b, env)
    assert path.parts[-3:] == ("Fragments", "Acme Coder", "cmcoder.json")
    p = json.loads(path.read_text("utf-8"))["profiles"][0]
    assert p["name"] == "Acme Coder" and p["icon"].endswith("icon.png")
    assert p["guid"] == wt.profile(b)["profiles"][0]["guid"]  # stable: reinstall updates
    assert wt.remove(b, env) == path and not path.parent.exists()
    assert wt.remove(b, env) is None
    with pytest.raises(RuntimeError, match="Windows"):
        wt.install(b, {})
