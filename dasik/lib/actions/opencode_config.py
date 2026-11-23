"""Read and write opencode's global config (``~/.config/opencode``).

opencode loads BOTH ``opencode.json`` and ``opencode.jsonc`` (json first, jsonc
second, so jsonc wins a clash), accepts MCP servers flat (``mcp.<name>``) or
nested (``mcp.servers.<name>``), and plugins under the legacy ``plugin`` key or
the new ``plugins`` one. This module is the single reader/writer of that
config; it is pure file IO, with no subprocess.
"""

import errno
import json
import os
import stat
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

JSON_REL = ".config/opencode/opencode.json"
JSONC_REL = ".config/opencode/opencode.jsonc"
SCHEMA_URL = "https://opencode.ai/config.json"

_READ_ORDER = (JSON_REL, JSONC_REL)  # later wins, as in opencode


class OpencodeConfigError(Exception):
    """opencode's config cannot be safely read or written."""


@dataclass(frozen=True)
class Found:
    rel: str
    nested: bool
    entry: Any


def _strip_comments(text: str) -> str:
    out: List[str] = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                raise ValueError("unterminated /* comment")
            i = end + 2
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _strip_trailing_commas(text: str) -> str:
    out: List[str] = []
    in_str = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif c == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1
                continue
            out.append(c)
        else:
            out.append(c)
        i += 1
    return "".join(out)


def parse_jsonc(text: str) -> Any:
    """Parse JSON with comments and trailing commas; ValueError if unparseable."""
    return json.loads(_strip_trailing_commas(_strip_comments(text)))


def _open_dir(parent_fd: int, name: str, rel: str) -> int:
    """Open a subdirectory refusing a link; OpencodeConfigError otherwise."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        try:
            is_link = stat.S_ISLNK(
                os.stat(name, dir_fd=parent_fd, follow_symlinks=False).st_mode
            )
        except OSError:
            is_link = False
        if is_link:
            raise OpencodeConfigError(
                f"cannot use {rel}: {name} is a symlink, and dasik will not "
                "follow a directory somebody redirected."
            ) from exc
        if exc.errno == errno.ENOTDIR:
            raise OpencodeConfigError(
                f"cannot use {rel}: {name} exists and is not a directory."
            ) from exc
        raise


def _open_chain(home: str, rel: str, uid: int = -1, gid: int = -1,
                create: bool = False) -> int:
    """fd of the directory holding ``rel``, walked without following links.

    Every later stat/read/write goes through this fd, so swapping a component
    for a link after the check cannot redirect us. With ``create``, missing
    directories are made and chowned through their own fd (never by path).
    """
    fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in rel.split("/")[:-1]:
            try:
                nxt = _open_dir(fd, name, rel)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(name, 0o755, dir_fd=fd)
                nxt = _open_dir(fd, name, rel)
                os.fchown(nxt, uid, gid)
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_file(dir_fd: int, base: str) -> str:
    """Read ``base`` in dir_fd, refusing a symlink (OSError ELOOP)."""
    fd = os.open(base, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    with os.fdopen(fd, encoding="utf-8") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise OSError(f"{base} is not a regular file")
        return f.read()


def _load(home: str, rel: str) -> Any:
    """The parsed file, or None when missing, linked, or unparseable."""
    try:
        dfd = _open_chain(home, rel)
    except (OSError, OpencodeConfigError):
        return None
    try:
        return parse_jsonc(_read_file(dfd, os.path.basename(rel)))
    except (OSError, ValueError):
        return None
    finally:
        os.close(dfd)


def read_mcp(home: str) -> Dict[str, Found]:
    found: Dict[str, Found] = {}
    for rel in _READ_ORDER:
        data = _load(home, rel)
        mcp = data.get("mcp") if isinstance(data, dict) else None
        if not isinstance(mcp, dict):
            continue
        for name, entry in mcp.items():
            if name == "servers" and isinstance(entry, dict):
                continue
            found[name] = Found(rel, False, entry)
        servers = mcp.get("servers")
        if isinstance(servers, dict):
            for name, entry in servers.items():
                found[name] = Found(rel, True, entry)
    return found


def read_plugins(home: str) -> Dict[str, Found]:
    found: Dict[str, Found] = {}
    for rel in _READ_ORDER:
        data = _load(home, rel)
        if not isinstance(data, dict):
            continue
        for key, nested in (("plugin", False), ("plugins", True)):
            items = data.get(key)
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, str):
                    found[item] = Found(rel, nested, item)
    return found


def edit(
    home: str, rel: str, mutate: Callable[[dict], None], uid: int, gid: int
) -> None:
    """Load ``home/rel``, apply ``mutate`` and write it back atomically.

    Missing directories and a new file are created owned by (uid, gid); a new
    file starts as ``{"$schema": ...}`` at 0644, an existing one keeps its mode.
    Runs as root inside a directory the user owns, so nothing is resolved by
    path twice: the directory chain is opened with O_NOFOLLOW, the file is read
    and the temp file created relative to that directory fd, and ownership is
    set on the temp file's fd BEFORE the rename (never chown by path).
    """
    base = os.path.basename(rel)
    path = os.path.join(home, rel)
    dfd = _open_chain(home, rel, uid, gid, create=True)
    try:
        mode = 0o644
        data: Any
        try:
            # O_NONBLOCK: a FIFO at this name must not hang root's open.
            rfd = os.open(
                base, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd
            )
        except FileNotFoundError:
            data = {"$schema": SCHEMA_URL}
        except OSError as exc:
            raise OpencodeConfigError(
                f"cannot edit {rel}: {base} is a symlink (or unreadable), and "
                f"dasik will not write through it: {exc}"
            ) from exc
        else:
            try:
                with os.fdopen(rfd, encoding="utf-8") as f:
                    st = os.fstat(f.fileno())
                    if not stat.S_ISREG(st.st_mode):
                        raise OpencodeConfigError(
                            f"cannot edit {rel}: {path} is not a regular file."
                        )
                    mode = st.st_mode & 0o7777
                    text = f.read()
                data = parse_jsonc(text) if text.strip() else {}
            except (OSError, ValueError) as exc:
                raise OpencodeConfigError(f"cannot parse {path}: {exc}") from exc
            if not isinstance(data, dict):
                raise OpencodeConfigError(f"{path}: top level is not an object")

        mutate(data)

        tmp = f".opencode-{os.urandom(6).hex()}.tmp"
        tfd = os.open(
            tmp,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=dfd,
        )
        try:
            os.fchmod(tfd, mode)
            os.fchown(tfd, uid, gid)
            with os.fdopen(tfd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.replace(tmp, base, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.remove(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
    finally:
        os.close(dfd)
