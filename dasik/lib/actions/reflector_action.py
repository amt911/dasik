"""Action: capture the `reflector` block back from the machine (v3 domain "reflector").

``/etc/xdg/reflector/reflector.conf`` is delivered by the expand toggle as a
plain file, and DropFilesAction only DISCOVERS files in the /etc directories it
lists in ``_SECTIONS`` (/etc/xdg is not one of them, and adding it would sweep
up every desktop package's config). So the mirrorlist policy was the one part
of the block that never came back from a ``sync``: package and timer returned,
the options were lost.

Like ``CpuAction`` this is CAPTURE-ONLY — ``plan()`` is empty and exists to
mark the class as v3 so ``Reconciler.sync`` visits it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .abstract_action import AbstractAction

_CONF = "/etc/xdg/reflector/reflector.conf"


def _parse(text: str) -> Dict[str, List[str]]:
    """``--flag value`` / ``--flag=value`` lines → {flag: [values…]}.

    Comma-separated values are split: the package's own conf ships
    ``--country France,Germany`` on a single line.
    """
    options: Dict[str, List[str]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("--"):
            continue
        separator = "=" if "=" in line.split(" ", 1)[0] else " "
        flag, _, rest = line.partition(separator)
        name = flag.lstrip("-").strip()
        values = [v for v in rest.replace(",", " ").split() if v]
        options.setdefault(name, []).extend(values)
    return options


class ReflectorAction(AbstractAction):
    """Reconstruct the `reflector` declaration from the live conf file."""

    _DOMAIN = "reflector"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        self._cfg: Dict[str, Any] = config if isinstance(config, dict) else {}

    @classmethod
    def empty_config(cls):
        """Root-level action: bootstrap from an empty mapping, not a list."""
        return {}

    @property
    def name(self) -> str:
        return "Reflector"

    @property
    def is_optional(self) -> bool:
        return True

    def _path(self) -> str:
        target = getattr(self.context, "target", None) if self.context else None
        return target.path(_CONF) if target is not None else _CONF

    # --- v3 contract --------------------------------------------------- #

    def plan(self, managed: Any) -> list:
        """Nothing to converge — the toggle's `files` contribution writes the
        conf. Overridden only so the Reconciler treats this as a v3 action."""
        return []

    def managed_keys(self) -> dict:
        """Owns no manifest domain: it never applies anything."""
        return {}

    # --- capture -------------------------------------------------------- #

    def import_state(self, managed=None) -> dict:
        """The `reflector` block this machine runs, or ``{}`` when unconfigured."""
        try:
            with open(self._path(), "r") as f:
                options = _parse(f.read())
        except OSError:
            return {}
        if not options:
            return {}

        block: Dict[str, Any] = {"countries": options.get("country", [])}
        if "protocol" in options:
            block["protocols"] = options["protocol"]
        # Absent --latest means the machine keeps ALL mirrors: defaulting it
        # back to 20 would add a filter it never had. None re-emits no line.
        block["latest"] = _first_int(options.get("latest"))
        for key, flag in (("sort", "sort"), ("save", "save")):
            if options.get(flag):
                block[key] = options[flag][0]
        return {self._DOMAIN: block}


def _first_int(values: Optional[List[str]]) -> Optional[int]:
    if not values:
        return None
    try:
        return int(values[0])
    except ValueError:
        return None
