"""Actions: declarative ``/etc/systemd/*.conf`` (oomd, system manager, user manager).

Three v3 scalar domains — ``oomd``, ``systemd_system_conf``,
``systemd_user_conf`` — each holding one systemd config section::

    "oomd": {"DefaultMemoryPressureDurationSec": "20s", "SwapUsedLimit": "90%"}
    "systemd_system_conf": {"DefaultTimeoutStopSec": "10s"}
    "systemd_user_conf": {"DefaultTimeoutStopSec": "10s"}

These files are pacman **backup files**, which is why nothing captured them
before: ``DropFilesAction`` discovery deliberately skips package-owned paths,
and ``/etc/systemd`` is not one of its sections either. A machine carrying
``DefaultMemoryPressureDurationSec=20s`` in ``/etc/systemd/oomd.conf`` synced to
a config that silently dropped it.

Reads and writes are deliberately asymmetric:

* **Write** a drop-in, ``<conf>.d/10-dasik.conf`` — systemd's supported override
  mechanism, and it keeps dasik out of pacman's ``.pacnew`` business.
* **Read** the EFFECTIVE configuration: the package file first, then every
  ``<conf>.d/*.conf`` in lexicographic order, later files overriding earlier
  ones — the order systemd itself applies. Reading only our own drop-in would
  make a value set in the package file invisible, which is the original bug.

Idempotent by canonical rendering (sorted keys, normalized spacing), so key
order or whitespace in the on-disk file never produces a phantom change.
"""
from __future__ import annotations
import configparser
import glob
import os
from typing import Any, Dict, List, Optional
from .scalar_action import ScalarV3Action
from ..exceptions.exceptions import ConfigValidationError
from ..state.change import Change, Op

_DROPIN_NAME = "10-dasik.conf"
_DROPIN_ITEM = f"drop-in {_DROPIN_NAME}"


def _render(section: str, settings: Dict[str, Any]) -> str:
    if not settings:
        return ""
    lines = [f"[{section}]"]
    lines += [f"{key} = {settings[key]}" for key in sorted(settings)]
    return "\n".join(lines) + "\n"


def _parse_section(text: str, section: str) -> Dict[str, str]:
    # strict=False: a hand-edited file repeating a key must read as "the last
    # one wins", not crash a plan. interpolation=None: systemd values are
    # percentages ("SwapUsedLimit=90%") and specifiers ("%t"), which configparser
    # would otherwise try to expand and reject.
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    setattr(cp, "optionxform", str)   # systemd keys are CamelCase — keep them
    cp.read_string(text)
    return dict(cp[section]) if cp.has_section(section) else {}


class SystemdConfAction(ScalarV3Action):
    """Base for one systemd config file + its drop-in directory."""

    _DOMAIN: str = ""
    _KEY: str = ""            # root-level config key
    _MAIN: str = ""           # canonical path of the package's file
    _SECTION: str = ""        # the ini section this block maps to
    _LABEL: str = ""

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._settings: Dict[str, Any] = cfg.get(self._KEY) or {}

    @property
    def name(self) -> str:
        return self._LABEL

    @property
    def is_optional(self) -> bool:
        return True

    # --- paths --------------------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _p(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    def _dropin_path(self) -> str:
        return self._p(f"{self._MAIN}.d/{_DROPIN_NAME}")

    def _sources(self) -> List[str]:
        """Every file systemd would read, in the order it applies them."""
        return [self._p(self._MAIN)] + sorted(
            glob.glob(self._p(f"{self._MAIN}.d/*.conf")))

    # --- ScalarV3Action hooks ------------------------------------------ #

    def _effective(self) -> Dict[str, str]:
        merged: Dict[str, str] = {}
        for path in self._sources():
            try:
                with open(path, "r") as f:
                    merged.update(_parse_section(f.read(), self._SECTION))
            except (OSError, configparser.Error, UnicodeDecodeError):
                continue
        return merged

    def _desired_value(self) -> Optional[str]:
        return _render(self._SECTION, self._settings) or None

    def _actual_value(self) -> Optional[str]:
        return _render(self._SECTION, self._effective()) or None

    def _set_value(self) -> None:
        path = self._dropin_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(_render(self._SECTION, self._settings))

    def _import_fragment(self, value: str) -> dict:
        settings = _parse_section(value, self._SECTION)
        return {self._KEY: settings} if settings else {}

    def import_state(self, managed=None) -> dict:
        """Report the machine, never the config.

        ScalarV3Action falls back to the DESIRED value when the target reads as
        nothing, which is right for a domain where "nothing read" is a failure
        rather than a state — a machine always has a timezone. Here it is a
        state: a stock oomd.conf is exactly "no setting". Keeping the fallback
        would let sync report a value nobody ever applied, and re-applying that
        captured config would then look like a no-op it is not.
        """
        value = self._actual_value()
        if value:
            return self._import_fragment(value)
        # Declared, but the machine has nothing: report that by CLEARING the
        # block. ConfigWriter.merge only ever overwrites a key — it cannot
        # delete one — so staying silent here would leave the stale declaration
        # standing in the captured config. An undeclared domain still captures
        # nothing, so a bootstrap sync invents no empty blocks.
        return {self._KEY: {}} if self._settings else {}

    # --- a drop-in that outranks ours ---------------------------------- #

    def _later_dropins(self) -> List[str]:
        """Drop-ins systemd applies AFTER dasik's — they override it."""
        return sorted(
            path for path in glob.glob(self._p(f"{self._MAIN}.d/*.conf"))
            if os.path.basename(path) > _DROPIN_NAME)

    def _refuse_if_outranked(self) -> None:
        """dasik writes ``10-dasik.conf`` and systemd applies drop-ins in
        lexicographic order, so a foreign ``99-user.conf`` holding one of the
        declared keys wins forever: apply writes our file, the effective value
        stays theirs, and the next plan proposes the same change again. That is
        the one thing this tool promises not to do, and no file dasik does not
        own can fix it — so refuse before mutating anything, and say which file
        and which key.
        """
        if not self._settings:
            return
        for path in self._later_dropins():
            try:
                with open(path, "r") as f:
                    theirs = _parse_section(f.read(), self._SECTION)
            except (OSError, configparser.Error, UnicodeDecodeError):
                continue
            stolen = sorted(key for key, value in theirs.items()
                            if key in self._settings
                            and value != str(self._settings[key]))
            if stolen:
                raise ConfigValidationError(
                    f"{path} sets {', '.join(stolen)} and systemd applies it "
                    f"AFTER dasik's {_DROPIN_NAME}, so the declared value could "
                    f"never take effect. Rename or remove that drop-in (or drop "
                    f"the key from the `{self._KEY}` block).")

    # --- the disable direction ----------------------------------------- #
    #
    # ScalarV3Action has no removal — a scalar is set or replaced. That leaves
    # a dropped block as a declaration the tool ignores: the drop-in survives
    # and the machine keeps the setting forever. Ownership decides, as
    # everywhere else: only a drop-in a previous generation recorded is ours to
    # delete.

    def plan(self, managed: Any):
        self._refuse_if_outranked()
        changes = super().plan(managed)
        if not self._settings and managed and os.path.exists(self._dropin_path()):
            changes.append(Change(self._DOMAIN, Op.REMOVE, _DROPIN_ITEM,
                                  reason="block no longer declared"))
        return changes

    def apply(self, changes) -> None:
        if self._target() is None:
            return
        for change in changes:
            if change.op is Op.REMOVE:
                try:
                    os.remove(self._dropin_path())
                except FileNotFoundError:
                    pass
        super().apply([c for c in changes if c.op is not Op.REMOVE])


class OomdAction(SystemdConfAction):
    """/etc/systemd/oomd.conf — systemd-oomd's pressure limits."""

    _DOMAIN = "oomd"
    _KEY = "oomd"
    _MAIN = "/etc/systemd/oomd.conf"
    _SECTION = "OOM"
    _LABEL = "Systemd OOMd Configuration"


class SystemdSystemConfAction(SystemdConfAction):
    """/etc/systemd/system.conf — the system service manager."""

    _DOMAIN = "systemd_system_conf"
    _KEY = "systemd_system_conf"
    _MAIN = "/etc/systemd/system.conf"
    _SECTION = "Manager"
    _LABEL = "Systemd System Manager Configuration"


class SystemdUserConfAction(SystemdConfAction):
    """/etc/systemd/user.conf — the per-user service manager."""

    _DOMAIN = "systemd_user_conf"
    _KEY = "systemd_user_conf"
    _MAIN = "/etc/systemd/user.conf"
    _SECTION = "Manager"
    _LABEL = "Systemd User Manager Configuration"
