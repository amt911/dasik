"""ConfigWriter — splice reconciliation fragments back into the config (spec §3.7).

Pure dict manipulation + JSON serialization. The set-math (which packages to
add as drift, which to drop) is computed upstream (in each action's
``import_state`` and in ``Reconciler.sync``); ConfigWriter only writes the
already-computed per-domain values into a copy of the config, leaving
``metadata`` and any unknown keys untouched.

Limitation: JSON has no comments, so any hand-written comments / logical
grouping in the source config are lost on rewrite (acceptable for slice 1).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


class ConfigWriter:
    @staticmethod
    def merge(existing: dict[str, Any], fragments: dict[str, Any]) -> dict[str, Any]:
        """Return a new config dict with ``fragments`` spliced over ``existing``.

        - Existing keys keep their position; ``fragments`` overrides their value.
        - New keys (e.g. bootstrapping ``packages`` into a config that lacked it)
          are appended.
        - ``metadata`` and any unknown keys not in ``fragments`` pass through
          untouched.
        - Inputs are never mutated; spliced ``fragments`` values are deep-copied
          so the returned config does not alias them.
        """
        merged = dict(existing)
        for key, value in fragments.items():
            merged[key] = copy.deepcopy(value)
        return merged

    @staticmethod
    def write(config: dict[str, Any], path: str | Path) -> None:
        """Serialize ``config`` to ``path`` as indented JSON (trailing newline)."""
        Path(path).write_text(json.dumps(config, indent=2) + "\n")
