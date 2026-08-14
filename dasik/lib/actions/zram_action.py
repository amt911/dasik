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

    def plan(self, managed):
        """The scalar comparison, plus the removal the base class cannot express.

        `ScalarV3Action.plan` only ever proposes a MODIFY towards a non-empty
        desired value, so an undeclared block proposed nothing at all and
        /etc/systemd/zram-generator.conf stayed on the machine — inert while
        zram-generator is uninstalled, and awake again the day the package comes
        back. Every other quiet domain (oomd, the systemd *.conf drop-ins,
        plymouthd.conf) takes its file back; this one now does too, and only
        when the manifest says the file is dasik's.
        """
        from ..state.change import Change, Op

        if self._zram:
            return super().plan(managed)
        if managed and self._actual_value():
            return [Change(self._DOMAIN, Op.REMOVE, _CONF,
                           reason="no longer declared")]
        return []

    def apply(self, changes) -> None:
        from ..state.change import Op

        removals = [c for c in changes if c.op is Op.REMOVE]
        if removals:
            try:
                os.remove(self._path())
            except OSError:
                pass
            return
        super().apply(changes)

    def _import_fragment(self, value: str) -> dict:
        return {"zram": _parse(value)} if value else {}

    def import_state(self, managed=None) -> dict:
        """Report the machine, never the config.

        ScalarV3Action falls back to the DESIRED value when the target reads as
        nothing, which is right for a domain where "nothing read" is a failure
        rather than a state — a machine always has a timezone. Here it is a
        state: no /etc/systemd/zram-generator.conf means no zram. Keeping the
        fallback let sync report a device nobody configured, and re-applying
        that captured config looked like a no-op it was not.

        A declared block the machine does not have is CLEARED rather than
        omitted: ConfigWriter.merge only ever overwrites a key, never deletes
        one, so silence would leave the stale declaration standing. An
        undeclared domain still captures nothing, so a bootstrap sync adds no
        empty zram block.
        """
        value = self._actual_value()
        if value:
            return self._import_fragment(value)
        return {"zram": {}} if self._zram else {}
