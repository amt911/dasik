import re
from typing import Any, Dict, Optional
from pathlib import Path
from .scalar_action import ScalarV3Action
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import ConfigValidationError

_LOCALTIME = "/etc/localtime"
_ZONEINFO_MARKER = "/zoneinfo/"
# region/city become the symlink target /usr/share/zoneinfo/<region>/<city>; each
# path component must be a plain zoneinfo name so a value like "../../etc" can't
# turn /etc/localtime into a traversal to an arbitrary file. city may carry a
# subpath (America/Argentina/Buenos_Aires); '..' can't match (no '.' in the class).
_TZ_COMPONENT = re.compile(r"[A-Za-z0-9_+-]+")


def _validate_zone_part(value: Optional[str], field: str) -> None:
    if value is None:
        return
    if not all(_TZ_COMPONENT.fullmatch(part) for part in value.split("/")):
        raise ConfigValidationError(
            f"Invalid timezone {field} {value!r}: each component must match "
            f"[A-Za-z0-9_+-]+ (no '..', spaces, or path metacharacters)."
        )


class TimezoneAction(ScalarV3Action):
    """Configure system timezone (scalar v3 domain)."""

    _DOMAIN = "timezone"

    def __init__(self, config: Dict[str, Any], context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        # Optional so sync can bootstrap from an empty config (no `timezone`
        # slice): actual()/import_state() read the system, not these.
        self.region: Optional[str] = cfg.get("region")
        self.city: Optional[str] = cfg.get("city")
        _validate_zone_part(self.region, "region")
        _validate_zone_part(self.city, "city")

    @property
    def name(self) -> str:
        return "Timezone Configuration"

    @property
    def is_optional(self) -> bool:
        return True

    # --- target helpers ----------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _localtime_path(self) -> str:
        t = self._target()
        return t.path(_LOCALTIME) if t is not None else "/mnt" + _LOCALTIME

    # --- scalar hooks ------------------------------------------------- #

    def _desired_value(self) -> Optional[str]:
        return f"{self.region}/{self.city}"

    def _actual_value(self) -> Optional[str]:
        link = Path(self._localtime_path())
        if not link.exists() or not link.is_symlink():
            return None
        try:
            target = link.readlink().as_posix()
        except Exception:
            return None
        idx = target.find(_ZONEINFO_MARKER)
        if idx == -1:
            return None
        return target[idx + len(_ZONEINFO_MARKER):] or None

    def _set_value(self) -> None:
        value = self._desired_value()
        link = f"/usr/share/zoneinfo/{value}"
        t = self._target()
        if t is not None:
            Command.execute("ln", ["-sf", link, _LOCALTIME], target=t)
            Command.execute("hwclock", ["--systohc"], target=t)
        else:
            Command.execute("ln", ["-sf", link, _LOCALTIME], True)
            Command.execute("hwclock", ["--systohc"], True)

    def _import_fragment(self, value: str) -> dict:
        region, _, city = value.partition("/")
        return {"timezone": {"region": region, "city": city}}
