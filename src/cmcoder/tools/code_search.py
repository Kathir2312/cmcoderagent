"""CodeSearch: find code by meaning in the project's index (Phase 5)."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field

from ..providers.openai_compat import ProviderError
from .base import Tool, ToolContext, ToolInput, ToolResult, truncate_middle

DEFAULT_LIMIT = 6


class CodeSearchInput(ToolInput):
    query: str = Field(
        description="What the code does, in words (e.g. 'where the auth token is refreshed')."
    )
    path: str | None = Field(None, description="Only search in this folder or file.")
    limit: int = Field(DEFAULT_LIMIT, ge=1, le=20, description="How many pieces to return.")


class CodeSearchTool(Tool):
    name = "CodeSearch"
    description = (
        "Find code by meaning in the project's index: describe what the code does, and get "
        "the best-matching functions, classes and sections with their file and lines. Use it "
        "when you don't know the names to Grep for; use Grep for exact text. Results can be "
        "a little behind the files: Read a file before editing it."
    )
    Input = CodeSearchInput
    read_only = True

    def permission_target(self, args: CodeSearchInput, ctx: ToolContext) -> Path:
        return ctx.resolve(args.path or ".")

    def describe(self, args: CodeSearchInput, ctx: ToolContext) -> str:
        where = f" in {args.path}" if args.path else ""
        return f'CodeSearch("{args.query}"{where})'

    async def run(self, args: CodeSearchInput, ctx: ToolContext) -> ToolResult:
        index = ctx.code_index
        if index is None:
            return ToolResult("Code search isn't set up for this project.", is_error=True)
        path = None
        if args.path:
            target = ctx.resolve(args.path)
            try:
                path = target.relative_to(ctx.project_root).as_posix()
            except ValueError:
                return ToolResult("The path must be inside the project.", is_error=True)
            path = None if path == "." else path
        from ..rag.stores import StoreError

        try:
            hits = await index.search(args.query, args.limit, path)
        except (StoreError, ProviderError) as e:
            return ToolResult(f"Code search failed: {e}. Use Grep instead.", is_error=True)
        if not hits:
            return ToolResult("No matches in the index.", summary="0 matches")
        parts = []
        for hit in hits:
            c = hit.chunk
            name = f" ({c.symbol})" if c.symbol else ""
            numbered = "\n".join(
                f"{c.start_line + i:6}\t{line}" for i, line in enumerate(c.text.split("\n"))
            )
            parts.append(f"### {c.location}{name}  [match {hit.score:.2f}]\n{numbered}")
        body = "\n\n".join(parts)
        return ToolResult(
            truncate_middle(body, ctx.max_output_chars),
            summary=f"{len(hits)} matches, best {hits[0].chunk.location}",
        )
