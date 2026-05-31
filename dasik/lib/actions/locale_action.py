"""Action: configure system locales (locale.gen, locale.conf, vconsole.conf).

Ported from the legacy ``_before_check``/``do_action`` form to the
AbstractAction contract (issue #66). Registered with config_key='locales',
so it receives the locales sub-dict directly.

Idempotent: only rewrites when the uncommented locales, LANG, or KEYMAP
differ from the desired configuration.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any, Dict, List
from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command

_LOCALE_GEN = "/mnt/etc/locale.gen"
_LOCALE_CONF = "/mnt/etc/locale.conf"
_VCONSOLE_CONF = "/mnt/etc/vconsole.conf"


class LocaleAction(AbstractAction):
    """Configure locales declaratively."""

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

    def is_needed(self) -> bool:
        with open(_LOCALE_GEN, "r") as locale_gen:
            locale_gen_str = locale_gen.read()

        uncommented = re.findall(r"^[a-z]+_\S+ \S+", locale_gen_str, re.MULTILINE)
        if len(uncommented) != len(self._selected_locales):
            return True

        for loc in self._selected_locales:
            if re.search(rf"^{re.escape(loc)}", locale_gen_str, re.MULTILINE) is None:
                return True

        if not Path(_LOCALE_CONF).exists():
            return True
        with open(_LOCALE_CONF, "r") as f:
            if re.search(re.escape(self._desired_locale), f.read()) is None:
                return True

        if not Path(_VCONSOLE_CONF).exists():
            return True
        with open(_VCONSOLE_CONF, "r") as f:
            if re.search(re.escape(self._desired_tty_layout), f.read()) is None:
                return True

        return False

    def execute(self) -> None:  # pragma: no cover - writes /mnt + runs locale-gen
        self._comment_all_entries()
        with open(_LOCALE_GEN, "r+") as locale_gen:
            text = locale_gen.read()
            locale_gen.seek(0)
            for loc in self._selected_locales:
                text = text.replace(f"#{loc}", f"{loc}")
            locale_gen.write(text)

        with open(_LOCALE_CONF, "w") as f:
            f.write(f"LANG={self._desired_locale}")
        with open(_VCONSOLE_CONF, "w") as f:
            f.write(f"KEYMAP={self._desired_tty_layout}")

        print(Command.execute("locale-gen", [], True).stdout.decode())

    def _comment_all_entries(self) -> None:  # pragma: no cover - writes /mnt
        with open(_LOCALE_GEN, "r+") as locale_gen:
            text = locale_gen.read()
            locale_gen.seek(0)
            text = re.sub(r"(^[a-z]+)", r"#\1", text, 0, re.MULTILINE)
            locale_gen.write(text)

    def verify(self) -> bool:
        return not self.is_needed()
