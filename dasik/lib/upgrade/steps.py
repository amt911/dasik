"""The data model of `dasik upgrade`: what to run, and what came of it."""
from dataclasses import dataclass
from typing import Callable, Optional, Tuple


@dataclass(frozen=True)
class UpgradeStep:
    """One declared item to bring to the latest version.

    *commands* are ``(script, args)`` pairs run as *user* through ``su``;
    *skip* (a reason) means nothing runs; *version* reads the installed
    version, called before and after the commands. *fingerprint* digests what
    the item has on disk, read at the same two moments, so an item without a
    version — or one whose version did not move — still says if it changed.
    """
    domain: str
    item: str
    user: str
    commands: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()
    skip: Optional[str] = None
    version: Callable[[], Optional[str]] = lambda: None
    fingerprint: Callable[[], Optional[str]] = lambda: None
    #: item id of a step this one depends on; if that one failed, this one is
    #: skipped without running anything.
    after: Optional[str] = None
    #: substrings that mark a failure even when the command exits 0 (a CLI that
    #: swallows its network error).
    failure_text: Tuple[str, ...] = ()


@dataclass(frozen=True)
class UpgradeOutcome:
    """status: updated | current | refreshed | skipped | failed.

    *contents* is set when the fingerprint decided: "changed" or "unchanged".
    """
    item: str
    status: str
    before: Optional[str]
    after: Optional[str]
    detail: str = ""
    contents: Optional[str] = None
