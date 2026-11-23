"""One reader of a target's /etc/passwd, shared by every action that needs homes/ids."""

from typing import Dict, Tuple


def passwd_entries(path: str) -> Dict[str, Tuple[str, int, int]]:
    """``{username: (home, uid, gid)}`` from the passwd file at ``path``.

    An unreadable file gives ``{}``; malformed lines are skipped.
    """
    entries: Dict[str, Tuple[str, int, int]] = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return entries
    for line in lines:
        parts = line.rstrip("\n").split(":")
        if len(parts) < 6:
            continue
        try:
            entries[parts[0]] = (parts[5], int(parts[2]), int(parts[3]))
        except ValueError:
            continue
    return entries
