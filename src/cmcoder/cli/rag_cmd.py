"""`cmcoder index` and `cmcoder rag ...`: code search (Phase 5) from the terminal."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

from ..config.settings import (
    RagConfig,
    Settings,
    SettingsError,
    find_project_root,
    load_settings,
)
from ..providers.openai_compat import ProviderError
from ..rag.index import CodeIndex, open_index
from ..rag.setup import (
    SetupChoice,
    apply_setup,
    check_embedding_model,
    check_store,
    embedding_candidates,
    rag_block,
    set_enabled,
    status_lines,
)
from ..rag.stores import StoreError
from .factory import build_provider
from .symbols import configure, sym

console = Console(highlight=False)
err = Console(stderr=True, highlight=False)

rag_app = typer.Typer(
    help="Code search: set it up, see its state, turn it on or off.",
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _settings(trust_project: bool = False) -> tuple[Settings, Path]:
    try:
        cwd = Path.cwd().resolve()
        settings = load_settings(cwd, trust_project=trust_project)
        configure(settings.symbols)
        return settings, find_project_root(cwd)
    except SettingsError as e:
        err.print(f"[red]error:[/red] {e}")
        raise typer.Exit(2) from e


def _open(settings: Settings, root: Path) -> CodeIndex:
    try:
        index = open_index(settings, root, build_provider)
    except (SettingsError, StoreError) as e:
        err.print(f"[red]error:[/red] {e}")
        raise typer.Exit(1) from e
    if index is None:
        why = "it's turned off" if settings.rag.enabled is False else "no embedding model is set"
        err.print(f"Code search isn't set up ({why}). Run `cmcoder rag setup`.")
        raise typer.Exit(1)
    return index


async def print_status(index: CodeIndex) -> None:
    for line in await status_lines(index):
        console.print(escape(line))


async def run_update(index: CodeIndex, *, rebuild: bool = False) -> int:
    if rebuild:
        await index.clear()
    with Progress(
        TextColumn("Indexing"),
        # The bar's ━ is "?" in the classic Windows console: just the counts there.
        *([BarColumn()] if sym().name == "unicode" else []),
        MofNCompleteColumn(),
        TextColumn("files · {task.fields[chunks]} pieces"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as bar:
        task = bar.add_task("index", total=None, chunks=0)

        def show(p: Any) -> None:
            bar.update(task, total=p.total, completed=p.done, chunks=p.chunks)

        try:
            result = await index.update(show)
        except (StoreError, ProviderError) as e:
            err.print(f"[red]Indexing failed:[/red] {escape(str(e))}")
            hint = getattr(e, "hint", None)
            if hint:
                err.print(f"  {escape(hint)}")
            return 1
    console.print(
        f"Indexed {result.indexed} files ({result.chunks} pieces) in {result.seconds:.1f}s; "
        f"{result.unchanged} unchanged, {result.removed} removed"
        + (
            f", {len(result.skipped)} skipped (binary, too large or unreadable)"
            if result.skipped
            else ""
        )
        + "."
    )
    return 0


def index_command(
    status: Annotated[
        bool, typer.Option("--status", help="Show the index; change nothing.")
    ] = False,
    clear: Annotated[bool, typer.Option("--clear", help="Delete this project's index.")] = False,
    rebuild: Annotated[bool, typer.Option("--rebuild", help="Index everything again.")] = False,
) -> None:
    """Build or update this project's code index (only changed files)."""
    settings, root = _settings()

    async def go() -> int:
        index = _open(settings, root)
        try:
            if status:
                await print_status(index)
                return 0
            if clear:
                await index.clear()
                console.print("Deleted this project's index.")
                return 0
            return await run_update(index, rebuild=rebuild)
        finally:
            await index.close()

    raise typer.Exit(asyncio.run(go()))


@rag_app.command("status", help="Show code search's settings and this project's index.")
def status_command() -> None:
    index_command(status=True)


@rag_app.command("on", help="Turn code search on (your settings).")
def on_command() -> None:
    path = set_enabled(True)
    console.print(f"Code search is on ({path}).")


@rag_app.command("off", help="Turn code search off (your settings). The index is kept.")
def off_command() -> None:
    path = set_enabled(False)
    console.print(f"Code search is off ({path}). `cmcoder rag on` turns it back on.")


def _pick(prompt: str, options: list[str], default: int = 1) -> int:
    for i, text in enumerate(options, start=1):
        console.print(f"  [bold]{i}[/bold] {text}")
    while True:
        answer = typer.prompt(prompt, default=str(default))
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        console.print(f"Type a number from 1 to {len(options)}.")


@rag_app.command("setup", help="Set up code search: embedding model, where the index lives, scope.")
def setup_command(
    embedding_model: Annotated[
        str | None,
        typer.Option("--embedding-model", "-m", help="provider:model on your gateway."),
    ] = None,
    store: Annotated[
        str | None,
        typer.Option(help="local (built in), chroma (on this machine) or chroma-server."),
    ] = None,
    url: Annotated[str | None, typer.Option(help="The Chroma server's address.")] = None,
    scope: Annotated[
        str | None, typer.Option(help="user (your settings) or project (shared with the team).")
    ] = None,
    read_only: Annotated[
        bool, typer.Option("--read-only", help="Only search a shared index (CI keeps it current).")
    ] = False,
    index_now: Annotated[
        bool | None, typer.Option("--index/--no-index", help="Index the project now.")
    ] = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Don't ask; use the options given.")
    ] = False,
) -> None:
    settings, root = _settings()
    interactive = not yes and sys.stdin.isatty() and sys.stdout.isatty()
    if not interactive and not embedding_model:
        err.print("[red]error:[/red] not a terminal: give --embedding-model (and --yes).")
        raise typer.Exit(2)
    asyncio.run(
        _setup(
            settings, root, interactive, embedding_model, store, url, scope, read_only, index_now
        )
    )


async def _setup(
    settings: Settings,
    root: Path,
    interactive: bool,
    model: str | None,
    store: str | None,
    url: str | None,
    scope: str | None,
    read_only: bool,
    index_now: bool | None,
) -> None:
    if not settings.providers:
        err.print("No gateway is configured yet: set it up first (see `cmcoder doctor`).")
        raise typer.Exit(2)
    # 1. The embedding model.
    if model is None:
        console.print("[bold]1. Embedding model[/bold] (turns code into vectors; on your gateway)")
        with console.status(spinner=sym().spinner, status="Asking the gateway for its models…"):
            likely, other, errors = await embedding_candidates(settings, build_provider)
        for name, e in errors.items():
            console.print(f"[yellow]Couldn't list the models of {name}:[/yellow] {escape(e)}")
        options = likely + other
        if not options:
            model = typer.prompt("Embedding model (provider:model)")
        else:
            if not likely:
                console.print(
                    "[dim]None of the names looks like an embedding model; all models:[/dim]"
                )
            shown = options[:20]
            choice = _pick("Model", [*shown, "Another (type its name)"])
            model = shown[choice] if choice < len(shown) else typer.prompt("provider:model")
    model = str(model)
    try:
        with console.status(spinner=sym().spinner, status=f"Checking {model}…"):
            ref, dim = await check_embedding_model(settings, model, build_provider)
    except (ProviderError, SettingsError) as e:
        err.print(f"[red]{model} doesn't work as an embedding model:[/red] {escape(str(e))}")
        raise typer.Exit(1) from e
    console.print(f"[green]{sym().ok}[/green] {ref} answers ({dim} dimensions).")
    # 2. Where the index lives.
    kinds = ["local", "chroma", "chroma-server"]
    if store is None:
        if interactive:
            console.print("\n[bold]2. Where the index lives[/bold]")
            store = kinds[
                _pick(
                    "Store",
                    [
                        "On this machine, built in (nothing to install)",
                        "Chroma on this machine (not in the standalone program)",
                        "A Chroma server (can be shared by a team)",
                    ],
                )
            ]
        else:
            store = "local"
    if store not in kinds:
        err.print(f"[red]error:[/red] --store must be one of {', '.join(kinds)}")
        raise typer.Exit(2)
    api_key = os.environ.get("CMCODER_CHROMA_API_KEY")
    if store == "chroma-server":
        if url is None:
            url = typer.prompt("Chroma server address (e.g. https://chroma.example.com:8000)")
        if interactive and not api_key:
            api_key = (
                typer.prompt(
                    "Its API key (Enter for none)", default="", hide_input=True, show_default=False
                )
                or None
            )
        if interactive and not read_only:
            read_only = typer.confirm(
                "Only search it (someone else, e.g. CI, keeps it up to date)?", default=False
            )
    choice = SetupChoice(ref, store, url, api_key, "user", read_only)  # type: ignore[arg-type]
    try:
        with console.status(spinner=sym().spinner, status="Checking the store…"):
            where = await check_store(choice)
    except StoreError as e:
        err.print(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(1) from e
    console.print(f"[green]{sym().ok}[/green] The index will be kept in {escape(where)}.")
    # 3. Whose settings.
    if scope is None:
        if interactive:
            console.print("\n[bold]3. Settings to write[/bold]")
            scope = ["user", "project"][
                _pick(
                    "Scope",
                    [
                        "Mine (~/.cmcoder/settings.json)",
                        f"This project's ({root / '.cmcoder' / 'settings.json'}, shared through git)",
                    ],
                )
            ]
        else:
            scope = "user"
    if scope not in ("user", "project"):
        err.print("[red]error:[/red] --scope must be user or project")
        raise typer.Exit(2)
    choice.scope = scope  # type: ignore[assignment]
    path = apply_setup(choice, root)
    console.print(f"[green]{sym().ok}[/green] Saved in {path}.")
    if api_key and store == "chroma-server":
        console.print("  The server's API key went to your OS keychain, not the file.")
    if scope == "project" and store == "chroma-server":
        console.print(
            "  [dim]Teammates get the server's address only once they trust the project "
            "(`cmcoder trust`), and each needs their own key.[/dim]"
        )
    # 4. Index now.
    if read_only:
        console.print("A read-only index is filled elsewhere: nothing to index here.")
        return
    if index_now is None:
        index_now = (
            typer.confirm("\nIndex this project now?", default=True) if interactive else False
        )
    if index_now:
        fresh = load_settings(root)
        # What was just chosen (a project's rag.store would otherwise wait for trust).
        fresh.rag = RagConfig.model_validate(
            {**fresh.rag.model_dump(by_alias=True), **rag_block(choice)}
        )
        index = _open(fresh, root)
        try:
            await run_update(index)
        finally:
            await index.close()
    else:
        console.print("Run `cmcoder index` when you're ready.")
