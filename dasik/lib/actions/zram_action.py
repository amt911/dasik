"""Action: declarative zram (systemd zram-generator).

v3 scalar domain "zram": the desired state is the content of
``/etc/systemd/zram-generator.conf``. The config mirrors the file's ini
structure — a mapping of device section -> options, e.g.::

    "zram": {"zram0": {"zram-size": "min(ram / 2, 8192)", "swap-priority": 100}}

Idempotent: desired and actual are both rendered to a CANONICAL ini (sorted
sections + keys) before comparing, so re-applying an unchanged config is a no-op
regardless of key ordering/whitespace in the on-disk file. ``sync`` captures the
current zram config even from an empty seed (ScalarV3Action.import_state reads the
actual file).
"""
from __future__ import annotations
import configparser
import os
from typing import Any, Dict, Optional
from .scalar_action import ScalarV3Action

_CONF = "/etc/systemd/zram-generator.conf"


def _render(sections: Dict[str, Dict[str, Any]]) -> str:
    if not sections:
        return ""
    out = []
    for dev in sorted(sections):
        out.append(f"[{dev}]")
        for key in sorted(sections[dev]):
            out.append(f"{key} = {sections[dev][key]}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _parse(text: str) -> Dict[str, Dict[str, str]]:
    cp = configparser.ConfigParser()
    setattr(cp, "optionxform", str)   # keep key case verbatim (zram keys are lowercase)
    cp.read_string(text)
    return {s: dict(cp[s]) for s in cp.sections()}


class ZramAction(ScalarV3Action):
    """Manage /etc/systemd/zram-generator.conf declaratively."""

    _DOMAIN = "zram"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._zram: Dict[str, Dict[str, Any]] = cfg.get("zram") or {}

    @property
    def name(self) -> str:
        return "Zram Configuration"

    @property
    def is_optional(self) -> bool:
        return True

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _path(self) -> str:
        t = self._target()
        return t.path(_CONF) if t is not None else "/mnt" + _CONF

    def _desired_value(self) -> Optional[str]:
        return _render(self._zram) or None

    def _actual_value(self) -> Optional[str]:
        try:
            with open(self._path(), "r") as f:
                return _render(_parse(f.read())) or None
        except (FileNotFoundError, configparser.Error):
            return None

    def _set_value(self) -> None:
        path = self._path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(_render(self._zram))

    def _import_fragment(self, value: str) -> dict:
        return {"zram": _parse(value)} if value else {}
