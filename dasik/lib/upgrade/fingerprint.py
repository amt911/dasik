"""What a step changed on disk, for the items no version number describes."""
import hashlib
import os
from typing import Iterable, Optional

_IGNORED = {".git"}   # a fetch rewrites it even when nothing new arrived


def tree_fingerprint(paths: Iterable[str]) -> Optional[str]:
    """A digest of every file under *paths* (names, bytes, symlink targets).

    None when none of *paths* exists, so "not there" is never mistaken for
    "unchanged", and None when something cannot be read: a measurement error is
    not an upgrade failure. A top-level path that is a link is followed (its
    contents are the item); links inside are hashed by target, never followed.
    """
    try:
        return _fingerprint(paths)
    except OSError:
        return None


def _fingerprint(paths: Iterable[str]) -> Optional[str]:
    digest = hashlib.sha256()
    found = False
    for index, top in enumerate(paths):
        if not os.path.exists(top):
            continue
        top = os.path.realpath(top)
        found = True
        digest.update(f"\0root{index}\0".encode())
        for rel, full in _entries(top):
            digest.update(rel.encode("utf-8", "surrogateescape") + b"\0")
            if os.path.islink(full):
                digest.update(b"L" + os.readlink(full).encode(
                    "utf-8", "surrogateescape"))
            elif os.path.isfile(full):
                digest.update(b"F")
                with open(full, "rb") as handle:
                    for chunk in iter(lambda: handle.read(1 << 16), b""):
                        digest.update(chunk)
            digest.update(b"\0")
    return digest.hexdigest() if found else None


def _entries(top: str):
    """``(relative path, full path)`` of every file and symlink, sorted."""
    if os.path.isfile(top):
        yield ".", top
        return
    for root, dirs, files in os.walk(top):
        dirs[:] = sorted(d for d in dirs if d not in _IGNORED
                         and not os.path.islink(os.path.join(root, d)))
        links = [d for d in os.listdir(root)
                 if os.path.islink(os.path.join(root, d))
                 and os.path.isdir(os.path.join(root, d))]
        for name in sorted(files + links):
            full = os.path.join(root, name)
            yield os.path.relpath(full, top), full
