"""Command-line entry point: `cmcoder`."""

from __future__ import annotations

import asyncio
import getpass
import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from .. import __version__
from ..compat import stdin_has_data, use_utf8_stdio
from ..config.settings import Settings, SettingsError, env_api_key_source, load_settings
from ..core.permissions import MODES
from ..protocol.events import protocol_json_schema
from ..providers.auth import ApiKeyAuth, delete_api_key, mask_key, store_api_key
from ..providers.openai_compat import ProviderError

SUBCOMMANDS = {"doctor", "login", "logout", "models", "protocol-schema", "version"}

console = Console(highlight=False)
err_console = Console(stderr=True, highlight=False)

main_app = typer.Typer(
    add_completion=False, context_settings={"help_option_names": ["-h", "--help"]}
)
sub_app = typer.Typer(
    add_completion=False, context_settings={"help_option_names": ["-h", "--help"]}
)


def _load(cwd: Path | None = None) -> Settings:
    try:
        return load_settings(cwd)
    except SettingsError as e:
        err_console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(2) from e


def key_problem(key: str) -> str | None:
    """Catch common paste mistakes before saving a key."""
    if not key:
        return "empty key"
    if any(ord(c) < 32 or ord(c) == 127 for c in key):
        return (
            "the key contains control characters. In Command Prompt, Ctrl+V does not paste into "
            "hidden input: paste with a right-click (or Ctrl+Shift+V in Windows Terminal)."
        )
    if any(c.isspace() for c in key):
        return "the key contains spaces; paste only the key itself."
    return None


def _split_rules(values: list[str] | None) -> list[str]:
    """Accept --allowedTools "Read,Bash(git diff:*)" as well as repeated flags."""
    out: list[str] = []
    for v in values or []:
        depth, cur = 0, ""
        for ch in v:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth = max(0, depth - 1)
            if ch in ", " and depth == 0:
                if cur.strip():
                    out.append(cur.strip())
                cur = ""
            else:
                cur += ch
        if cur.strip():
            out.append(cur.strip())
    return out


@main_app.command(
    help="Agentic coding assistant. Run without arguments for an interactive session."
)
def main(
    prompt: Annotated[
        str | None, typer.Argument(help="Prompt to start with (or to run with -p).")
    ] = None,
    print_mode: Annotated[
        bool, typer.Option("--print", "-p", help="Run non-interactively and print the result.")
    ] = False,
    output_format: Annotated[
        str, typer.Option("--output-format", help="With -p: text, json or stream-json.")
    ] = "text",
    model: Annotated[
        str | None, typer.Option("--model", "-m", help='Model ("provider:model" or a model name).')
    ] = None,
    permission_mode: Annotated[
        str | None, typer.Option("--permission-mode", help=f"One of: {', '.join(MODES)}.")
    ] = None,
    allowed_tools: Annotated[
        list[str] | None,
        typer.Option(
            "--allowedTools", "--allowed-tools", help='Allow rules, e.g. "Bash(npm test:*)".'
        ),
    ] = None,
    disallowed_tools: Annotated[
        list[str] | None,
        typer.Option("--disallowedTools", "--disallowed-tools", help="Deny rules."),
    ] = None,
    max_turns: Annotated[
        int | None, typer.Option("--max-turns", help="Max model calls per turn.")
    ] = None,
    append_system_prompt: Annotated[
        str | None,
        typer.Option("--append-system-prompt", help="Extra text appended to the system prompt."),
    ] = None,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show tool calls (-p) and reasoning.")
    ] = False,
    continue_session: Annotated[
        bool,
        typer.Option("--continue", "-c", help="Continue the latest conversation in this project."),
    ] = False,
    resume: Annotated[
        str | None,
        typer.Option(
            "--resume", "-r", help="Resume a saved conversation by id (or the start of its id)."
        ),
    ] = None,
    version: Annotated[bool, typer.Option("--version", help="Print the version and exit.")] = False,
) -> None:
    from .factory import AgentOptions

    if version:
        console.print(__version__)
        raise typer.Exit()
    if permission_mode and permission_mode not in MODES:
        err_console.print(f"[red]error:[/red] --permission-mode must be one of {', '.join(MODES)}")
        raise typer.Exit(2)
    if output_format not in ("text", "json", "stream-json"):
        err_console.print("[red]error:[/red] --output-format must be text, json or stream-json")
        raise typer.Exit(2)

    settings = _load()
    opts = AgentOptions(
        cwd=Path.cwd(),
        model=model,
        permission_mode=permission_mode,
        allowed_tools=_split_rules(allowed_tools),
        disallowed_tools=_split_rules(disallowed_tools),
        max_turns=max_turns,
        append_system_prompt=append_system_prompt,
        continue_session=continue_session,
        resume=resume,
    )
    try:
        if print_mode:
            from .headless import run_headless

            text = prompt or ""
            if stdin_has_data():
                piped = sys.stdin.read()
                text = f"{piped}\n\n{text}".strip() if piped.strip() else text
            if not text:
                err_console.print("[red]error:[/red] -p needs a prompt (argument or stdin)")
                raise typer.Exit(2)
            code = asyncio.run(run_headless(settings, opts, text, output_format, verbose))  # type: ignore[arg-type]
        else:
            from .repl import Repl

            code = asyncio.run(Repl(settings, opts, verbose=verbose).main(prompt))
    except (SettingsError, ProviderError, ValueError) as e:
        err_console.print(f"[red]error:[/red] {e}")
        raise typer.Exit(2) from e
    raise typer.Exit(code)


@sub_app.command(help="Check settings, network, TLS, API key and models.")
def doctor(
    model: Annotated[
        list[str] | None, typer.Option("--model", "-m", help="Model(s) to check.")
    ] = None,
    no_probe: Annotated[
        bool, typer.Option("--no-probe", help="Skip test requests to the models.")
    ] = False,
) -> None:
    from .doctor import Doctor

    settings = _load()
    refs = model or [m for m in (settings.model, settings.small_fast_model) if m]
    code = asyncio.run(Doctor(settings, console).run(refs, probe=not no_probe))
    raise typer.Exit(code)


def _provider_name(settings: Settings, provider: str | None) -> str:
    if provider:
        if provider not in settings.providers:
            err_console.print(f"[red]error:[/red] unknown provider {provider!r}")
            raise typer.Exit(2)
        return provider
    if not settings.providers:
        err_console.print(
            "[red]error:[/red] no provider configured. Add `providers` to ~/.cmcoder/settings.json "
            "(see docs/settings.example.json) or set CMCODER_BASE_URL."
        )
        raise typer.Exit(2)
    if settings.model:
        return settings.resolve_model()[0]
    return settings.default_provider()


@sub_app.command(help="Store the API key for a provider in the OS keychain.")
def login(
    provider: Annotated[str | None, typer.Option("--provider", help="Provider name.")] = None,
) -> None:
    settings = _load()
    name = _provider_name(settings, provider)
    console.print(f"Provider [bold]{name}[/bold] ({settings.providers[name].base_url})")
    key = getpass.getpass("API key (input hidden; paste with right-click): ").strip()
    if problem := key_problem(key):
        err_console.print(f"[red]error:[/red] {problem}")
        raise typer.Exit(2)
    where = store_api_key(name, key)
    console.print(f"[green]Saved[/green] {mask_key(key)} ({len(key)} characters) to {where}.")
    if not key.startswith("sk-"):
        console.print(
            "[yellow]Note:[/yellow] LiteLLM keys usually start with `sk-`; this one doesn't. "
            "Check that you pasted the whole key."
        )
    env_key, env_var = env_api_key_source()
    if env_key and env_key != key:
        console.print(
            f"[yellow]Note:[/yellow] {env_var} is set ({mask_key(env_key)}) and overrides the "
            f"stored key. Clear it (`set {env_var}=` on Windows, `unset {env_var}` elsewhere) "
            "to use the key you just entered."
        )

    async def verify() -> None:
        from .factory import build_provider

        p = build_provider(settings, name)
        # Check the key that was just entered, whatever the environment says.
        p.auth = ApiKeyAuth(name, explicit_key=key, explicit_source="entered key")
        try:
            models = await p.list_models()
            console.print(f"[green]Key works[/green]: {len(models)} models available.")
        except ProviderError as e:
            console.print(f"[yellow]Could not verify the key:[/yellow] {e}")
        finally:
            await p.aclose()

    asyncio.run(verify())


@sub_app.command(help="Remove the stored API key for a provider.")
def logout(
    provider: Annotated[str | None, typer.Option("--provider", help="Provider name.")] = None,
) -> None:
    settings = _load()
    name = _provider_name(settings, provider)
    delete_api_key(name)
    console.print(f"Removed the stored key for [bold]{name}[/bold].")


@sub_app.command(help="List the models the server offers.")
def models(
    provider: Annotated[str | None, typer.Option("--provider", help="Provider name.")] = None,
) -> None:
    from .factory import build_provider

    settings = _load()
    name = _provider_name(settings, provider)

    async def go() -> int:
        p = build_provider(settings, name)
        try:
            ids = await p.list_models()
            info = await p.model_info()
        except ProviderError as e:
            err_console.print(f"[red]error:[/red] {e}")
            return 1
        finally:
            await p.aclose()
        configured = {settings.model, settings.small_fast_model}
        for mid in ids:
            mi = info.get(mid, {})
            ctx = f"  context {mi['max_input_tokens']}" if mi.get("max_input_tokens") else ""
            mark = (
                "  [cyan](configured)[/cyan]"
                if mid in configured or f"{name}:{mid}" in configured
                else ""
            )
            console.print(f"{mid}{ctx}{mark}")
        return 0

    raise typer.Exit(asyncio.run(go()))


@sub_app.command(
    "protocol-schema", help="Print the Agent Protocol JSON Schema (for generating TS types)."
)
def protocol_schema() -> None:
    print(json.dumps(protocol_json_schema(), indent=2))


@sub_app.command(help="Print the version.")
def version() -> None:
    console.print(__version__)


def run() -> None:
    use_utf8_stdio()
    if len(sys.argv) > 1 and sys.argv[1] in SUBCOMMANDS:
        sub_app()
    else:
        main_app()


if __name__ == "__main__":
    run()
