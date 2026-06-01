"""Action: write declarative files (udev rules, modprobe, profile.d, /etc/environment).

v3 domain "files": each entry is an explicit {name, content}; the on-disk
filename is the chosen name (stable identity). CREATE/DELETE by canonical path
(set-math) + MODIFY on content drift. actual() is scoped to declared paths that
exist (no directory glob). Registered config_key="__root__".
"""
from __future__ import annotations
import hashlib
import os
from typing import Any, Dict, List
from .abstract_action import AbstractAction
from ..state.change import Change, Op


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# (config key, target directory) for the per-file sections.
_SECTIONS = [
    ("udev_rules", "/etc/udev/rules.d"),
    ("modprobe_conf", "/etc/modprobe.d"),
    ("profile_d", "/etc/profile.d"),
]
_ENV_PATH = "/etc/environment"
_FILES_DOMAIN = "files"


class DropFilesAction(AbstractAction):
    """Write config snippets into /etc/... directories on the target."""

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._sections = {key: cfg.get(key, []) for key, _ in _SECTIONS}
        self.etc_env_lines: List[str] = cfg.get("etc_environment", [])
        self._etc_files: List[Any] = cfg.get("files", [])

    @property
    def name(self) -> str:
        return "Drop Config Files"

    @property
    def is_optional(self) -> bool:
        return True

    # -- paths / desired state ----------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _abs(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    @staticmethod
    def _entry_fields(entry: Any) -> tuple:
        """Accept a dict or a FileEntry-like object."""
        if isinstance(entry, dict):
            return entry["name"], entry["content"]
        return entry.name, entry.content

    @staticmethod
    def _path_fields(entry: Any) -> tuple:
        """Accept a dict or an EtcFile-like object (arbitrary /etc path)."""
        if isinstance(entry, dict):
            return entry["path"], entry["content"]
        return entry.path, entry.content

    def _desired(self) -> Dict[str, str]:
        """Canonical absolute path -> verbatim content."""
        desired: Dict[str, str] = {}
        for key, directory in _SECTIONS:
            for entry in self._sections.get(key, []):
                name, content = self._entry_fields(entry)
                desired[f"{directory}/{name}"] = content
        if self.etc_env_lines:
            desired[_ENV_PATH] = "\n".join(self.etc_env_lines) + "\n"
        for entry in self._etc_files:
            path, content = self._path_fields(entry)
            desired[path] = content
        return desired

    def _read(self, canonical: str) -> str:
        with open(self._abs(canonical), "r") as f:
            return f.read()

    def _exists(self, canonical: str) -> bool:
        return os.path.exists(self._abs(canonical))

    def actual(self) -> set:
        """Declared paths that exist on disk (no directory glob)."""
        if self._target() is None:
            return set()
        return {p for p in self._desired() if self._exists(p)}

    def _needs_write(self, canonical: str, desired: str) -> bool:
        if not self._exists(canonical):
            return True
        return _sha256(self._read(canonical)) != _sha256(desired)

    # -- v3 contract --------------------------------------------------- #

    def plan(self, managed):
        from ..state.set_math import compute_changes
        desired = self._desired()
        actual = self.actual()
        changes, _drift = compute_changes(
            _FILES_DOMAIN,
            desired=list(desired.keys()),
            managed=managed,
            actual=actual,
            op_install=Op.CREATE,
            op_remove=Op.DELETE,
        )
        for p in sorted(set(desired) & actual):
            if self._read(p) != desired[p]:
                changes.append(Change(_FILES_DOMAIN, Op.MODIFY, p, reason="content drift"))
        return changes

    def managed_keys(self) -> dict:
        return {_FILES_DOMAIN: sorted(self._desired().keys())}

    def import_state(self, managed=None) -> dict:
        actual = self.actual()
        result: Dict[str, Any] = {}
        for key, directory in _SECTIONS:
            entries = []
            for entry in self._sections.get(key, []):
                name, content = self._entry_fields(entry)
                canonical = f"{directory}/{name}"
                if canonical in actual:
                    content = self._read(canonical)     # refresh manual edits
                entries.append({"name": name, "content": content})
            result[key] = entries

        if _ENV_PATH in actual:
            text = self._read(_ENV_PATH)
            result["etc_environment"] = [ln for ln in text.split("\n") if ln != ""]
        else:
            result["etc_environment"] = list(self.etc_env_lines)

        files_out = []
        for entry in self._etc_files:
            path, content = self._path_fields(entry)
            if path in actual:
                content = self._read(path)
            files_out.append({"path": path, "content": content})
        result["files"] = files_out
        return result

    def apply(self, changes) -> None:
        if self._target() is None:
            return
        desired = self._desired()
        writes = [c.item for c in changes if c.op in (Op.CREATE, Op.MODIFY)]
        deletes = [c.item for c in changes if c.op is Op.DELETE]

        for canonical in writes:                    # additive first
            path = self._abs(canonical)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(desired.get(canonical, ""))

        for canonical in deletes:
            path = self._abs(canonical)
            if os.path.exists(path):
                os.remove(path)

    # -- legacy is_needed / execute / verify (old executor path) ------- #

    def is_needed(self) -> bool:
        return any(self._needs_write(p, c) for p, c in self._desired().items())

    def execute(self) -> None:
        for canonical, content in self._desired().items():
            if self._needs_write(canonical, content):
                path = self._abs(canonical)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as f:
                    f.write(content)
                print(f"  Wrote {path}")

    def verify(self) -> bool:
        return not any(self._needs_write(p, c) for p, c in self._desired().items())
