"""Code search quality: questions in plain words with a known right answer.

Indexes a copy of cmcoder's own source (src/cmcoder) with your embedding
model, asks each question in evals/retrieval/questions.json and checks where
the expected function or class comes in the results.

  uv run python evals/retrieval.py                       # rag.embeddingModel from your settings
  uv run python evals/retrieval.py --model corp:bge-m3   # another model
  uv run python evals/retrieval.py --mock                # the mock gateway (checks the harness only)

Reports hit@1, hit@5 and MRR (mean reciprocal rank: 1 for first place, 1/2
for second, ...; 0 if not in the top 10).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from cmcoder.cli.factory import build_provider
from cmcoder.config.settings import load_settings
from cmcoder.rag.index import open_index

ROOT = Path(__file__).resolve().parent
QUESTIONS = ROOT / "retrieval" / "questions.json"
SOURCE = ROOT.parent / "src" / "cmcoder"
K = 10


def rank(hits: list, file: str, symbol: str) -> int | None:
    for i, hit in enumerate(hits, start=1):
        c = hit.chunk
        if c.path.endswith(file) and (
            symbol in (c.symbol or "") or f"def {symbol}" in c.text or f"class {symbol}" in c.text
        ):
            return i
    return None


async def evaluate(model: str | None) -> int:
    questions = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    work = Path(tempfile.mkdtemp(prefix="cmcoder-retrieval-"))
    try:
        project = work / "cmcoder"
        shutil.copytree(SOURCE, project, ignore=shutil.ignore_patterns("__pycache__"))
        (project / ".git").mkdir()  # the project root
        settings = load_settings(project)
        if model:
            settings.rag.embedding_model = model
        if not settings.rag.embedding_model:
            print("No embedding model: set rag.embeddingModel, or use --model / --mock.")
            return 2
        settings.rag.enabled = True
        settings.rag.store.type = "local"
        settings.rag.store.url = None
        index = open_index(settings, project, build_provider)
        assert index is not None
        try:
            started = time.monotonic()
            result = await index.update()
            print(
                f"Indexed {result.indexed} files ({result.chunks} pieces) with "
                f"{index.model_ref} in {time.monotonic() - started:.1f}s\n"
            )
            ranks: list[int | None] = []
            for q in questions:
                hits = await index.search(q["q"], K)
                r = rank(hits, q["file"], q["symbol"])
                ranks.append(r)
                where = f"#{r}" if r else "missed"
                top = hits[0].chunk.location if hits else "-"
                print(f"{where:>7}  {q['q'][:62]:<62}  top: {top}")
        finally:
            await index.clear()
            await index.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    n = len(ranks)
    hit1 = sum(1 for r in ranks if r == 1) / n
    hit5 = sum(1 for r in ranks if r and r <= 5) / n
    mrr = sum(1 / r for r in ranks if r) / n
    print(f"\nhit@1 {hit1:.0%} · hit@5 {hit5:.0%} · MRR {mrr:.2f} ({n} questions)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--model", help="embedding model (provider:model); default: your settings")
    ap.add_argument("--mock", action="store_true", help="use the mock gateway (harness check)")
    args = ap.parse_args()
    if not args.mock:
        return asyncio.run(evaluate(args.model))
    from cmcoder.testing.mock_server import MockServer, MockState

    config = Path(tempfile.mkdtemp(prefix="cmcoder-retrieval-config-"))
    with MockServer(MockState([], api_key="sk-eval")) as server:
        os.environ.update(
            {
                "CMCODER_BASE_URL": server.base_url,
                "CMCODER_API_KEY": "sk-eval",
                "CMCODER_MODEL": "qwen3-27b",
                "CMCODER_CONFIG_DIR": str(config),
            }
        )
        for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            os.environ.pop(var, None)
        try:
            return asyncio.run(evaluate("default:text-embedding-3-small"))
        finally:
            shutil.rmtree(config, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
