"""Prompt template loader. Templates live in /prompts as plain markdown files."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from backend.core.config import get_settings


class PromptNotFoundError(Exception):
    pass


@lru_cache(maxsize=64)
def _load(prompt_path: str, mtime: float) -> str:
    del mtime  # cache key only
    return Path(prompt_path).read_text(encoding="utf-8")


def load_prompt(name: str) -> str:
    """Load prompts/<name>.md (cached until the file changes)."""
    settings = get_settings()
    path = Path(settings.prompts_dir) / f"{name}.md"
    if not path.exists():
        raise PromptNotFoundError(f"Prompt template '{name}' not found at {path}")
    return _load(str(path), path.stat().st_mtime)


def render_prompt(name: str, **variables: object) -> str:
    """Load a template and substitute {{variable}} placeholders."""
    template = load_prompt(name)
    for key, value in variables.items():
        template = template.replace("{{" + key + "}}", str(value))
    return template
