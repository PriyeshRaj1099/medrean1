"""Seed (or re-seed) the RAG knowledge base with the bundled documents.

Usage:
    python scripts/seed_knowledge.py            # index only new/changed docs
    python scripts/seed_knowledge.py --force    # re-chunk + re-embed everything
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai.registry import get_embedding_provider  # noqa: E402
from database.session import dispose_engine, get_session_factory  # noqa: E402
from rag.kb_loader import index_seed_knowledge  # noqa: E402


async def main(force: bool) -> int:
    session_factory = get_session_factory()
    provider = get_embedding_provider()
    print(f"Embedding provider: {provider.name} (model: {provider.model})")
    async with session_factory() as db:
        result = await index_seed_knowledge(db, provider, force=force)
    await dispose_engine()
    print(
        "Seed complete: {documents_total} documents bundled, "
        "{documents_indexed} indexed this run, {chunks_total} chunks in store.".format(**result)
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed the MediLens knowledge base")
    parser.add_argument("--force", action="store_true", help="re-embed all documents")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.force)))
