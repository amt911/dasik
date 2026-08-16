"""The curses screens: five primitives and the flow that strings them together.

Thin on purpose. Nothing here knows what a partition is — it collects a
:class:`Choices` and hands it to :mod:`recipes` and :mod:`compose`, which is
what lets the interesting half be tested without a terminal, and this half be
tested with a script of keystrokes against a fake screen.

Kept deliberately plain — `A_REVERSE` for the selected row and nothing else, no
colour pairs, no boxes drawn with ACS characters — because this runs on the
installer ISO, often over a serial console whose terminal cannot be relied on
for much more.
"""
from __future__ import annotations

import curses
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .inventory import DiskInfo
from .recipes import RECIPES, Options, custom_disk, find

_ENTER = (10, 13, curses.KEY_ENTER)
_ESC = 27
_BACKSPACE = (curses.KEY_BACKSPACE, 127, 8)
_QUIT = (ord("q"), ord("Q"))

_TITLE = "dasik — partition wizard"
_FOOTER = "[↑↓] move   [enter] choose   [q] quit — nothing is written until the end"

_FILESYSTEMS = ("fat32", "ext4", "btrfs", "xfs", "swap")
# Which GPT type code a filesystem implies. The wizard picks it rather than
# asking: an ESP that is not flagged `esp` is a boot failure nobody enjoys
# debugging, and a swap partition wants `linux-swap`.
_PART_TYPE = {"fat32": "esp", "swap": "linux-swap"}


@dataclass
class Choices:
    """Everything the screens collected. No side effects, no files."""

    device: str
    recipe_key: str
    options: Options
    passphrase: Optional[str] = None
    hostname: str = "archlinux"
    custom_partitions: List[Dict[str, Any]] = field(default_factory=list)


# --- primitives -------------------------------------------------------------- #

def _frame(screen, title: str, body: Sequence[str], selected: int = -1) -> None:
    height, width = screen.getmaxyx()
    screen.erase()
    screen.addstr(0, 0, _TITLE[: width - 1], curses.A_BOLD)
    screen.addstr(1, 0, title[: width - 1])
    row = 3
    for index, line in enumerate(body):
        if row >= height - 2:
            break
        attr = curses.A_REVERSE if index == selected else curses.A_NORMAL
        screen.addstr(row, 2, line[: width - 3], attr)
        row += 1
    screen.addstr(min(height - 1, row + 1), 0, _FOOTER[: width - 1])
    screen.refresh()


def menu(screen, title: str, rows: Sequence[str]) -> Optional[int]:
    """A row picker. Returns the index, or None if abandoned."""
    if not rows:
        return None
    index = 0
    while True:
        _frame(screen, title, rows, selected=index)
        key = screen.getch()
        if key in _QUIT or key == _ESC:
            return None
        if key in _ENTER:
            return index
        if key == curses.KEY_UP:
            index = max(0, index - 1)
        elif key == curses.KEY_DOWN:
            index = min(len(rows) - 1, index + 1)


def prompt(screen, title: str, label: str, default: str = "",
           secret: bool = False) -> Optional[str]:
    """A one-line editor. Empty input keeps *default*; ESC abandons.

    *secret* echoes asterisks — the LUKS passphrase is typed on a screen that
    may well be a projector, a serial log, or someone's shoulder.
    """
    buffer = ""
    while True:
        shown = "*" * len(buffer) if secret else buffer
        hint = f" [{default}]" if default and not secret else ""
        _frame(screen, title, [f"{label}{hint}:", f"  {shown}"])
        key = screen.getch()
        if key == _ESC:
            return None
        if key in _ENTER:
            return buffer if buffer else default
        if key in _BACKSPACE:
            buffer = buffer[:-1]
        elif 32 <= key < 127:
            buffer += chr(key)


def confirm(screen, title: str, question: str) -> bool:
    """y/n, defaulting to **no**: every caller here guards something destructive."""
    _frame(screen, title, [question, "", "y = yes,  anything else = no"])
    return screen.getch() in (ord("y"), ord("Y"))


def message(screen, title: str, lines: Sequence[str]) -> None:
    _frame(screen, title, list(lines) + ["", "press any key"])
    screen.getch()


# --- the flow ---------------------------------------------------------------- #

def _pick_disk(screen, disks: List[DiskInfo]) -> Optional[DiskInfo]:
    rows = [disk.describe() for disk in disks]
    index = menu(screen, "Which disk?", rows)
    return None if index is None else disks[index]


def _pick_recipe(screen) -> Optional[str]:
    rows = [f"{r.title} — {r.detail}" for r in RECIPES]
    rows.append("Custom — compose the partitions yourself")
    index = menu(screen, "Which layout?", rows)
    if index is None:
        return None
    return "custom" if index == len(RECIPES) else RECIPES[index].key


def _ask_options(screen, device: str, recipe_key: str,
                 wipe: bool) -> "Optional[tuple[Options, Optional[str]]]":
    """The tunables the chosen recipe actually uses, and nothing else."""
    esp = prompt(screen, "Sizes", "ESP size", default="512MiB")
    if esp is None:
        return None
    values: Dict[str, Any] = {"esp_size": esp, "wipe": wipe}
    passphrase: Optional[str] = None

    if recipe_key != "ext4":
        luks = prompt(screen, "Encryption", "LUKS mapper name", default="cryptroot")
        if luks is None:
            return None
        values["luks_name"] = luks
        passphrase = prompt(screen, "Encryption", "LUKS passphrase",
                            default="", secret=True)
        if passphrase is None:
            return None

    if recipe_key in ("luks-btrfs-swap", "luks-btrfs-hibernate"):
        swap = prompt(screen, "Swap", "swap size", default="8GiB")
        if swap is None:
            return None
        values["swap_size"] = swap

    return Options(device=device, **values), passphrase


def _ask_partitions(screen, device: str) -> Optional[List[Dict[str, Any]]]:
    """The custom path: one partition at a time, validated as a set at the end."""
    partitions: List[Dict[str, Any]] = []
    while True:
        number = len(partitions) + 1
        label = prompt(screen, f"Partition {number}", "label", default="")
        if label is None:
            return None
        size = prompt(screen, f"Partition {number}", "size (e.g. 512MiB, 50%, rest)",
                      default="rest")
        if size is None:
            return None
        index = menu(screen, f"Partition {number}: filesystem", list(_FILESYSTEMS))
        if index is None:
            return None
        filesystem = _FILESYSTEMS[index]
        mountpoint = prompt(screen, f"Partition {number}", "mountpoint (blank for none)",
                            default="")
        if mountpoint is None:
            return None
        partitions.append({
            "label": label,
            "size": size,
            "filesystem": filesystem,
            "partition_type": _PART_TYPE.get(filesystem, "linux"),
            "mountpoint": mountpoint or None,
            "format": True,
        })
        if not confirm(screen, "Partitions", "Add another partition?"):
            return partitions


def _review_lines(device: str, disk_stanza: Dict[str, Any],
                  warnings: Sequence[str]) -> List[str]:
    lines = [f"Disk: {device}",
             f"Wipe: {'YES — this ERASES the disk' if disk_stanza['wipe_disk'] else 'no'}",
             ""]
    for part in disk_stanza["partitions"]:
        bits = [f"  {part['label']:<10}", f"{part['size']:>8}",
                f"{part['filesystem']:<6}"]
        if part.get("mountpoint"):
            bits.append(f"-> {part['mountpoint']}")
        if part.get("encrypt"):
            bits.append(f"[LUKS {part.get('luks_name')}]")
        if part.get("swap_encryption") == "random":
            bits.append("[random key]")
        lines.append(" ".join(bits))
        for subvol in part.get("btrfs_subvolumes") or []:
            lines.append(f"      {subvol['name']:<14} -> {subvol['mountpoint']}")
    if warnings:
        lines += ["", "Warnings:"] + [f"  ! {w}" for w in warnings]
    lines += ["", "enter = write the config,  q = abandon"]
    return lines


def run_wizard(screen, disks: List[DiskInfo]) -> Optional[Choices]:
    """Drive every screen. Returns the choices, or None if abandoned.

    Writes nothing: the caller turns this into a file, which is what keeps the
    exploratory half and the destructive half apart (issue #190).
    """
    try:
        curses.curs_set(0)
    except Exception:      # nosec B110 - a terminal with no cursor control
        pass

    if not disks:
        message(screen, "Nothing to do",
                ["No disks found. `lsblk -J` reported none —",
                 "are you running this as root, on the machine you mean to install?"])
        return None

    disk = _pick_disk(screen, disks)
    if disk is None:
        return None

    recipe_key = _pick_recipe(screen)
    if recipe_key is None:
        return None

    wipe = False
    if not disk.is_empty:
        if not confirm(screen, "This disk is not empty",
                       f"{disk.describe()} — erase it?"):
            # A populated disk without wipe_disk is refused by plan() anyway
            # (dasik never silently reformats), so composing it would produce a
            # config that cannot install and does not say why.
            return None
        wipe = True

    if recipe_key == "custom":
        partitions = _ask_partitions(screen, disk.path)
        if partitions is None:
            return None
        try:
            stanza = custom_disk(disk.path, partitions, wipe=wipe)
        except Exception as e:      # noqa: BLE001 - the model's own complaint
            message(screen, "That layout will not do", str(e).splitlines()[:10])
            return None
        options = Options(device=disk.path, wipe=wipe)
        passphrase = None
    else:
        asked = _ask_options(screen, disk.path, recipe_key, wipe)
        if asked is None:
            return None
        options, passphrase = asked
        stanza = find(recipe_key).build(options).disk

    hostname = prompt(screen, "Machine", "hostname", default="archlinux")
    if hostname is None:
        return None

    warnings = []
    if wipe:
        warnings.append(f"{disk.path} holds data and will be ERASED by `dasik apply`.")
    _frame(screen, "Review", _review_lines(disk.path, stanza, warnings))
    key = screen.getch()
    if key in _QUIT or key == _ESC:
        return None

    return Choices(device=disk.path, recipe_key=recipe_key, options=options,
                   passphrase=passphrase, hostname=hostname,
                   custom_partitions=list(stanza["partitions"])
                   if recipe_key == "custom" else [])
