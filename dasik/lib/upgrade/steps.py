"""The data model of `dasik upgrade`: what to run, and what came of it."""
from dataclasses import dataclass
from typing import Callable, Optional, Tuple


@dataclass(frozen=True)
class UpgradeStep:
    """One declared item to bring to the latest version.

    *commands* are ``(script, args)`` pairs run as *user* through ``su``;
    *skip* (a reason) means nothing runs; *version* reads the installed
    version, called before and after the commands.
    """
    domain: str
    item: str
    user: str
    commands: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()
    skip: Optional[str] = None
    version: Callable[[], Optional[str]] = lambda: None


@dataclass(frozen=True)
class UpgradeOutcome:
    """status: updated | current | refreshed | skipped | failed."""
    item: str
    status: str
    before: Optional[str]
    after: Optional[str]
    detail: str = ""
