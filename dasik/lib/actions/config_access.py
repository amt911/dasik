"""Shared dict-or-pydantic-model field reader.

A v3 action's config slice arrives as either a plain ``dict`` (a JSON block
parsed straight through) or a pydantic model instance, depending on the
caller — and reading one field off either shape without caring which is a
two-line function that kept getting rewritten identically across every
action that needed it: ``mcp_servers_action.py``, ``ai_skills_action.py``,
``uv_tools_action.py``, ``pacman_repositories_action.py`` and
``pacman_repos_state.py`` all carried a byte-identical private ``_field``.
Extracted here (AGENTS.md "Reuse before you write": the third copy is
already the signal to extract — by the time this one was written there were
five) so every call site shares ONE definition instead of several that could
silently drift apart.
"""
from typing import Any


def field(entry: Any, key: str, default: Any = None) -> Any:
    """Read *key* from a dict or from a pydantic model, whichever arrived."""
    if isinstance(entry, dict):
        return entry.get(key, default)
    return getattr(entry, key, default)
