"""`cmcoder setup`: the first-time setup, when the company's settings template
didn't already do it (user_folder.py): the gateway, the API key (into the OS
keychain, as `cmcoder login`), the models. It writes ~/.cmcoder/settings.json,
keeping whatever else is in it. Every answer can also be given as an option,
for scripts: `cmcoder setup --gateway https://ai.corp/v1 --model qwen3-27b`.
"""

from __future__ import annotations

import asyncio
import getpass
import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console

from ..config.settings import Settings, SettingsError, config_dir, load_settings
from ..providers.auth import ApiKeyAuth, store_api_key
from ..providers.openai_compat import ProviderError
from .factory import build_provider

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)


def _read(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}
    except ValueError as e:
        err_console.print(
            f"[red]error:[/red] {path} isn't valid JSON ({e}); fix or remove it first."
        )
        raise typer.Exit(2) from e
    if not isinstance(data, dict):
        err_console.print(f"[red]error:[/red] {path} must contain a JSON object.")
        raise typer.Exit(2)
    return data


def _ask(question: str, default: str | None = None) -> str:
    return str(typer.prompt(question, default=default or None, show_default=bool(default))).strip()


def _choose(models: list[str], question: str, default: str | None, optional: bool) -> str | None:
    """A model by number or name; Enter keeps the default (or skips, if optional)."""
    hint = " (Enter to skip)" if optional and not default else ""
    while True:
        answer = str(
            typer.prompt(f"{question}{hint}", default=default or "", show_default=bool(default))
        ).strip()
        if not answer or answer == default:  # Enter: the default (typer fills it in)
            return default or (None if optional else "")
        if answer.isdigit() and 1 <= int(answer) <= len(models):
            return models[int(answer) - 1]
        if answer in models or not models:
            return answer
        console.print(f"[yellow]{answer!r} isn't in the list.[/yellow] A number or a name from it.")


async def _models(settings: Settings, name: str, key: str | None) -> list[str]:
    provider = build_provider(settings, name)
    if key:
        provider.auth = ApiKeyAuth(name, explicit_key=key, explicit_source="entered key")
    try:
        return sorted(await provider.list_models())
    finally:
        await provider.aclose()


def setup(
    gateway: Annotated[
        str | None,
        typer.Option("--gateway", help="The gateway's address, e.g. https://ai.corp/v1."),
    ] = None,
    kind: Annotated[
        str | None,
        typer.Option("--type", help="openai (LiteLLM and other OpenAI-compatible) or openwebui."),
    ] = None,
    name: Annotated[str, typer.Option("--provider", help="A name for the gateway.")] = "corp",
    model: Annotated[str | None, typer.Option("--model", help="The main model.")] = None,
    small: Annotated[
        str | None, typer.Option("--small-model", help="A small, fast model for quick jobs.")
    ] = None,
    vision: Annotated[
        str | None, typer.Option("--vision-model", help="A model that reads images.")
    ] = None,
    login: Annotated[bool, typer.Option("--login/--no-login", help="Ask for the API key.")] = True,
) -> None:
    path = config_dir() / "settings.json"
    data = _read(path)
    try:
        current = load_settings()
    except SettingsError:
        current = None
    locked = bool(current and current.managed_path and current.providers and _locked(current))
    providers: dict[str, Any] = dict(data.get("providers") or {})
    if locked:
        assert current is not None
        name = current.default_provider()
        console.print(f"Your company set the gateway: [bold]{name}[/bold].")
    else:
        old = providers.get(name, {}) if isinstance(providers.get(name), dict) else {}
        url = gateway or _ask("Gateway address (from your AI team)", old.get("baseUrl"))
        if not url.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            err_console.print(
                "[red]error:[/red] the address must start with https:// (plain http is only for "
                "a gateway on this computer)."
            )
            raise typer.Exit(2)
        url = url.rstrip("/")
        if kind is None:
            guess = old.get("type") or ("openai" if url.endswith("/v1") else "openwebui")
            # Given the address as an option (a script): don't ask the type either.
            kind = (
                guess
                if gateway
                else _ask("Type: openai (LiteLLM and the like) or openwebui", guess)
            )
        if kind not in ("openai", "openwebui"):
            err_console.print("[red]error:[/red] --type is openai or openwebui.")
            raise typer.Exit(2)
        providers[name] = {**old, "baseUrl": url, **({"type": kind} if kind != "openai" else {})}
        data["providers"] = providers
    if locked:
        assert current is not None
        settings = current
    else:
        settings = Settings.model_validate({**data, "providers": providers})

    key: str | None = None
    if login:
        key = getpass.getpass("API key (input hidden; paste with right-click): ").strip()
        if key:
            where = store_api_key(name, key)
            console.print(f"[green]Saved[/green] your key to {where}.")
    try:
        models = asyncio.run(_models(settings, name, key))
    except ProviderError as e:
        models = []
        console.print(f"[yellow]Couldn't list the gateway's models:[/yellow] {e}")
    if models:
        console.print(f"[green]The gateway answers[/green]: {len(models)} models.")
        for i, m in enumerate(models, 1):
            console.print(f"  {i:>3}. {m}")

    names = set(settings.providers)

    def bare(m: str | None) -> str | None:
        """The model's name without its gateway ("corp:qwen3:8b" is qwen3:8b)."""
        prefix, sep, rest = (m or "").partition(":")
        return (rest if sep and prefix in names else m) or None

    def ref(m: str) -> str:
        return f"{name}:{bare(m)}"

    old_model = bare(data.get("model"))
    main = model or _choose(models, "Main model (number or name)", old_model, optional=False)
    if not main:
        err_console.print("[red]error:[/red] a main model is needed.")
        raise typer.Exit(2)
    data["model"] = ref(main)
    asked = model is None  # interactive: offer the optional ones too
    for key_name, given, question in (
        ("smallFastModel", small, "Small, fast model for titles and summaries"),
        ("visionModel", vision, "Model that reads images (if your main one can't)"),
    ):
        old_value = bare(data.get(key_name))
        chosen = given or (_choose(models, question, old_value, optional=True) if asked else None)
        if chosen:
            data[key_name] = ref(chosen)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    console.print(f"[green]Saved[/green] {path}.")
    console.print("Check everything with:  cmcoder doctor")


def _locked(settings: Settings) -> bool:
    try:
        raw = json.loads(Path(str(settings.managed_path)).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    return bool(isinstance(raw, dict) and raw.get("lockProviders"))
