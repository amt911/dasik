"""Write a captured config back THROUGH the directives that assembled it.

``resolve_includes`` turns many files into one document. This is the way back:
given the root path and the config ``sync`` captured, put each value in the file
it came from, so a config split across files survives being synced.

The rule that makes it safe to run unattended: **a directive whose resolved
value did not change is left alone, and its file is never opened for writing.**
On a converged machine the whole walk writes nothing at all.

The consequence worth spelling out is the secret: ``users[].hashed_password``
is captured from the target's ``/etc/shadow``, so on a converged machine it
equals what is behind ``{"$include_line": "secrets/hash"}`` and that file is not
touched. When the password *did* change, the new hash goes to the secret file —
which is gitignored — instead of being inlined into the committed config.

Writes are planned in full before any of them happens: a missing included file
raises before the first byte is written, because a writeback that got halfway
across five files is worse than one that did nothing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .includes import (
    CONCAT,
    INCLUDE,
    INCLUDE_LINE,
    INCLUDE_TEXT,
    ConfigIncludeError,
    _read,
    _resolve_path,
    resolve_includes,
)

_DIRECTIVES = (INCLUDE, INCLUDE_TEXT, INCLUDE_LINE, CONCAT)

# Keys that identify "the same entry" across a capture, so a list element keeps
# its directives when its *content* changed. `files` is the one that matters —
# a PAM body pulled in with $include_text has to keep being pulled in after the
# machine's copy of it drifted — but users/units cost nothing to cover.
_IDENTITY_KEYS = ("path", "name", "username", "unit")


def _dumps(value: Any) -> str:
    return json.dumps(value, indent=2) + "\n"


def _directive_of(node: Any) -> Optional[str]:
    """The directive *node* is, or None. Mirrors `_resolve`'s own rule: a
    directive must be the only key in its object."""
    if not isinstance(node, dict):
        return None
    present = [d for d in _DIRECTIVES if d in node]
    if len(present) != 1 or len(node) != 1:
        return None
    return present[0]


def _survives_as_text(value: str) -> bool:
    """Whether a file body read back yields exactly *value*.

    `Path.read_text` translates newlines, so a body containing CR comes back as
    LF: writing it to a file would silently change the value `sync` captured.
    Such a value stays in the JSON instead — inline and ugly beats wrong.
    """
    return value.replace("\r\n", "\n").replace("\r", "\n") == value


def _survives_as_line(value: str) -> bool:
    """Whether `$include_line` reads back exactly *value*.

    It yields the first line *stripped*, and refuses an empty one — so leading
    or trailing whitespace, an embedded line break, or "" cannot be stored
    there. "Line break" has to mean whatever `str.splitlines` means (it splits
    on \\v, \\f, \\x85 and U+2028 too), because that is what the resolver uses;
    checking only for \\n and \\r let "0\\x850" through and it read back as "0".
    """
    return (bool(value) and value.strip() == value
            and len(value.splitlines()) == 1)


def _identity(item: Any) -> Optional[Tuple[str, Any]]:
    if not isinstance(item, dict):
        return None
    for key in _IDENTITY_KEYS:
        if key in item:
            return (key, item[key])
    return None


class _Planner:
    """Accumulates the writes; nothing reaches the disk until `write_back` says so."""

    def __init__(self) -> None:
        self.writes: Dict[Path, str] = {}

    # -- files ------------------------------------------------------------- #

    def _stage(self, path: Path, text: str, current: str) -> None:
        if text != current:
            self.writes[path] = text

    # -- the walk ---------------------------------------------------------- #

    def plan(self, raw: Any, new: Any, base_dir: Path) -> Any:
        """Return what the *current* file should hold for this node."""
        directive = _directive_of(raw)
        if directive is not None:
            return self._plan_directive(directive, raw, new, base_dir)
        if isinstance(raw, dict) and isinstance(new, dict):
            return self._plan_dict(raw, new, base_dir)
        if isinstance(raw, list) and isinstance(new, list):
            return self._plan_list(raw, new, base_dir)
        return new

    def _plan_dict(self, raw: Dict[str, Any], new: Dict[str, Any],
                   base_dir: Path) -> Dict[str, Any]:
        # Existing keys keep their position (and their directives); a key sync
        # dropped disappears; a key no file declared is appended to this one.
        out = {k: self.plan(v, new[k], base_dir) for k, v in raw.items() if k in new}
        for key, value in new.items():
            if key not in raw:
                out[key] = value
        return out

    def _plan_list(self, raw: List[Any], new: List[Any], base_dir: Path) -> List[Any]:
        # The captured order wins — it is what the machine reports — but each
        # captured entry is matched back to the raw entry that produced it so
        # its directives survive. Exact value first, then identity key.
        available = list(range(len(raw)))
        resolved = [resolve_includes(item, base_dir) for item in raw]
        out: List[Any] = []
        for item in new:
            index = self._match(item, resolved, available)
            if index is None:
                out.append(item)
                continue
            available.remove(index)
            out.append(self.plan(raw[index], item, base_dir))
        return out

    @staticmethod
    def _match(item: Any, resolved: List[Any], available: List[int]) -> Optional[int]:
        for index in available:
            if resolved[index] == item:
                return index
        identity = _identity(item)
        if identity is None:
            return None
        for index in available:
            if _identity(resolved[index]) == identity:
                return index
        return None

    # -- directives --------------------------------------------------------- #

    def _plan_directive(self, directive: str, raw: Dict[str, Any], new: Any,
                        base_dir: Path) -> Any:
        if directive == CONCAT:
            return self._plan_concat(raw, new, base_dir)

        path = _resolve_path(raw[directive], base_dir)
        text = _read(path, raw[directive])

        if directive == INCLUDE_TEXT:
            if text == new:
                return raw
            if not isinstance(new, str) or not _survives_as_text(new):
                return new          # not representable as a file body
            self._stage(path, new, text)
            return raw

        if directive == INCLUDE_LINE:
            # Representability is checked FIRST: "" compares equal to a file
            # whose first line is already blank, and leaving the directive in
            # place there would leave a config `resolve_includes` then refuses
            # ("expected a secret on its first line").
            if not isinstance(new, str) or not _survives_as_line(new):
                return new
            lines = text.splitlines()
            if lines and lines[0].strip() == new:
                return raw
            # Only the first line is the secret; whatever a human wrote below it
            # (how it was generated, which machine it belongs to) is theirs.
            rest = "".join(f"\n{line}" for line in lines[1:])
            self._stage(path, new + rest + "\n", text)
            return raw

        # $include: the value lives in another JSON document, which may itself
        # be assembled from directives.
        try:
            inner = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigIncludeError(
                f"included file {raw[directive]} is not valid JSON: {exc}") from None
        if resolve_includes(inner, path.parent) == new:
            return raw
        self._stage(path, _dumps(self.plan(inner, new, path.parent)), text)
        return raw

    def _plan_concat(self, raw: Dict[str, Any], new: Any, base_dir: Path) -> Any:
        members = raw[CONCAT]
        if not isinstance(new, list) or not isinstance(members, list) or not members:
            return new
        resolved = [resolve_includes(m, base_dir) for m in members]
        if [entry for member in resolved for entry in member] == new:
            return raw

        # The members are concatenated in order, so the shares must partition
        # `new` into CONTIGUOUS blocks — otherwise the config reads back in a
        # different order than the machine reported, which is a difference
        # nobody made. Walk `new` in order, never going back to an earlier
        # member: an entry goes to the member that already held it when that
        # member is still ahead, and to the current one when it is new.
        available = [list(member) if isinstance(member, list) else []
                     for member in resolved]
        shares: List[List[Any]] = [[] for _ in members]
        current = 0
        for entry in new:
            owner = next((i for i in range(current, len(members))
                          if entry in available[i]), current)
            if entry in available[owner]:
                available[owner].remove(entry)
            shares[owner].append(entry)
            current = owner
        return {CONCAT: [self.plan(m, share, base_dir)
                         for m, share in zip(members, shares)]}


def write_back(root_path: "str | Path", new_config: Dict[str, Any]) -> List[Path]:
    """Persist *new_config* through the directive tree rooted at *root_path*.

    Returns every file actually written, root first. A file whose content did
    not change is not in the list and was not opened.
    """
    root = Path(root_path)
    current = root.read_text(encoding="utf-8")
    raw = json.loads(current)

    planner = _Planner()
    value = planner.plan(raw, new_config, root.parent)
    root_text = _dumps(value)

    written: List[Path] = []
    if root_text != current:
        written.append(root)
    written.extend(p for p in planner.writes if p != root)

    for path in written:
        text = root_text if path == root else planner.writes[path]
        path.write_text(text, encoding="utf-8")
    return written
