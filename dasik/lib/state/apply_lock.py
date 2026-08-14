"""One apply at a time, per target.

Nothing stopped two applies from running against the same machine. Both read the
same manifest, both mutate the same system, and whichever finishes last writes
the ownership record — so everything the other one installed is now unowned, and
the next plan proposes to remove it. pacman's own lock would refuse the second
transaction, but files, units, disks and the manifest have no such protection.

`flock` rather than a pid file: the kernel releases it when the process dies, so
a power cut leaves a stale FILE but never a stale LOCK. The file lives under the
target, so applying to /mnt from a live ISO does not block whatever manages /.
"""
from __future__ import annotations

import fcntl
import os
from typing import Any, Optional

_LOCK_PATH = "/var/lib/dasik/apply.lock"


class ApplyLockBusy(RuntimeError):
    """Raised when another dasik holds the lock for this target."""


class ApplyLock:
    """Context manager around an exclusive, non-blocking flock."""

    def __init__(self, target: Any):
        root = getattr(target, "root", None)
        self.path = (target.path(_LOCK_PATH) if hasattr(target, "path")
                     else (root or "") + _LOCK_PATH)
        self._fd: Optional[int] = None

    def __enter__(self) -> "ApplyLock":
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        except OSError:
            # The lock is a safety net, not a gate. A target where the file
            # cannot even be created is one where the apply is about to fail on
            # its own — with a better message than "could not lock".
            self._fd = None
            return self
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            holder = ""
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    holder = f.read().strip()
            except OSError:
                pass
            os.close(fd)
            who = f" (pid {holder})" if holder else ""
            raise ApplyLockBusy(
                f"another dasik is already applying to this target{who}. "
                f"Two applies on one machine race each other: the second one to "
                f"finish records what it owns and the first one's work becomes "
                f"unowned. Wait for it, or remove {self.path} if you are sure "
                f"nothing is running.") from e
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        os.fsync(fd)
        self._fd = fd
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._fd is not None:
            # The kernel drops the lock on close, and on death — which is what
            # makes a leftover file harmless.
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
