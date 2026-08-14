"""Action: configure system locales (locale.gen, locale.conf, vconsole.conf).

Composite v3 domain "locales": the desired state is the (selected_locales,
LANG, KEYMAP) record; one MODIFY when any field drifts. Target-aware.
"""
from __future__ import annotations
import re
from typing import Any, Dict, List, Optional
from .composite_action import CompositeV3Action
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import ConfigValidationError

_LOCALE_GEN = "/etc/locale.gen"
_LOCALE_CONF = "/etc/locale.conf"
_VCONSOLE_CONF = "/etc/vconsole.conf"


class LocaleAction(CompositeV3Action):
    """Configure locales declaratively (composite v3 domain)."""

    _DOMAIN = "locales"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._selected_locales: List[str] = cfg.get("selected_locales", [])
        self._desired_locale: str = cfg.get("desired_locale", "")
        self._desired_tty_layout: str = cfg.get("desired_tty_layout", "")
        # LocaleModel requires all three fields, so an empty dict never reaches
        # here from a user config — only from the reconciler, which hands
        # empty_config() for a domain a previous generation owned. See plan().
        self._declared: bool = bool(cfg)

    @property
    def name(self) -> str:
        return "Locale Configuration"

    @property
    def is_optional(self) -> bool:
        return True

    # --- target-aware paths ------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _p(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    # --- composite state ---------------------------------------------- #

    def _desired_state(self) -> dict:
        return {
            "selected_locales": sorted(self._selected_locales),
            "desired_locale": self._desired_locale,
            "desired_tty_layout": self._desired_tty_layout,
        }

    def _read(self, canonical: str) -> Optional[str]:
        try:
            with open(self._p(canonical), "r") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def _actual_state(self) -> Optional[dict]:
        gen = self._read(_LOCALE_GEN)
        conf = self._read(_LOCALE_CONF)
        vconsole = self._read(_VCONSOLE_CONF)
        if gen is None or conf is None or vconsole is None:
            return None
        uncommented = re.findall(r"^[a-z]+_\S+ \S+", gen, re.MULTILINE)
        lang = ""
        for line in conf.splitlines():
            if line.startswith("LANG="):
                lang = line.split("=", 1)[1].strip()
        keymap = ""
        for line in vconsole.splitlines():
            if line.startswith("KEYMAP="):
                keymap = line.split("=", 1)[1].strip()
        return {
            "selected_locales": sorted(uncommented),
            "desired_locale": lang,
            "desired_tty_layout": keymap,
        }

    def plan(self, managed):
        """Nothing declared is not "no locales".

        Dropping a `locales` block a generation owned makes the reconciler plan
        the empty config, whose desired state is `selected_locales: []`,
        `LANG=`, `KEYMAP=` — and applying that comments out every entry in
        locale.gen and empties locale.conf/vconsole.conf. There is no "unset the
        locale" operation, so leave the machine alone.
        """
        return super().plan(managed) if self._declared else []

    def _import_fragment(self, value) -> dict:
        # Report the machine. Falling back to the desired state captured
        # `{"selected_locales": [], "desired_locale": ""}` from a target whose
        # files could not be read — an empty block that says nothing and applies
        # as "wipe the locales".
        state = self._actual_state()
        return {self._DOMAIN: state} if state is not None else {}

    def _missing_from_locale_gen(self, text: str) -> List[str]:
        """Declared locales that /etc/locale.gen does not list at all.

        Enabling a locale means uncommenting its line; one that is not in the
        file cannot be enabled, so writing LANG for it produces a machine that
        never matches the config and a plan that repeats the same change for
        ever. Both the commented and the already-enabled form count as present.
        """
        lines = {line.lstrip("#").strip() for line in text.splitlines() if line.strip()}
        return [loc for loc in self._selected_locales if loc.strip() not in lines]

    def _set_value(self) -> None:  # pragma: no cover - writes /etc + runs locale-gen
        gen_path = self._p(_LOCALE_GEN)
        with open(gen_path, "r") as f:
            text = f.read()
        missing = self._missing_from_locale_gen(text)
        if missing:
            raise ConfigValidationError(
                f"{_LOCALE_GEN} on the target does not list {', '.join(missing)}. "
                "A locale that is not in that file cannot be enabled, so nothing "
                "was written — the apply would have reported success and the same "
                "change would come back on every plan. Check the spelling against "
                f"the target's {_LOCALE_GEN} (the charset half matters: "
                "`en_US.UTF-8 UTF-8`).")
        text = re.sub(r"(^[a-z]+)", r"#\1", text, 0, re.MULTILINE)  # comment all
        for loc in self._selected_locales:
            text = text.replace(f"#{loc}", f"{loc}")
        with open(gen_path, "w") as f:
            f.write(text)
        with open(self._p(_LOCALE_CONF), "w") as f:
            f.write(f"LANG={self._desired_locale}")
        with open(self._p(_VCONSOLE_CONF), "w") as f:
            f.write(f"KEYMAP={self._desired_tty_layout}")
        t = self._target()
        if t is not None:
            Command.execute("locale-gen", [], target=t, check=True)
        else:
            Command.execute("locale-gen", [], True, check=True)
