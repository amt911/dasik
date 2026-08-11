"""Pre-flight for the target root itself, run before any action touches it.

Every command against a target whose root is not ``/`` goes through
``arch-chroot <root>`` (see ``Command.execute``). That binary lives in
``arch-install-scripts`` — always present on the install ISO, rarely on an
installed system. Since ``plan``/``apply`` default to ``--target /mnt``, running
``dasik plan`` on a normal host aborted deep inside a probe with a bare
"Binary not found: arch-chroot", never mentioning that day-2 management wants
``--target /``. This check runs first and says so.
"""
from __future__ import annotations

import os
from shutil import which
from typing import Optional

from .target import Target

_CHROOT_PKG = "arch-install-scripts"


def check_target(target: Target) -> Optional[str]:
    """An actionable error message, or ``None`` when the target is usable.

    Only the chroot requirement is checked. An empty ``/mnt`` is NOT an error:
    that is exactly the state of a fresh install ISO before the disk actions
    have mounted anything, so it is reported as a hint inside the message and
    never as a failure of its own.
    """
    if not target.is_chroot:
        return None
    if which("arch-chroot") is not None:
        return None

    lines = [
        f"arch-chroot not found: every command against {target.root} runs inside "
        f"it (install it with `pacman -S {_CHROOT_PKG}`).",
        "To manage the RUNNING system instead of an install target, re-run with "
        "--target /",
    ]
    if not os.path.isdir(target.path("/etc")):
        lines.append(f"(nothing is mounted at {target.root} right now)")
    return "\n".join(lines)
