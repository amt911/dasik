"""Action: configure system locales (locale.gen, locale.conf, vconsole.conf).

Composite v3 domain "locales": the desired state is the (selected_locales,
LANG, KEYMAP) record; one MODIFY when any field drifts. Target-aware.
"""
from __future__ import annotations
import re
from typing import Any, Dict, List, Optional
from .composite_action import CompositeV3Action
from ..command_worker.command_worker import Command

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

    def _import_fragment(self, value) -> dict:
        return {self._DOMAIN: self._actual_state() or self._desired_state()}

    def _set_value(self) -> None:  # pragma: no cover - writes /etc + runs locale-gen
        gen_path = self._p(_LOCALE_GEN)
        with open(gen_path, "r") as f:
            text = f.read()
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
