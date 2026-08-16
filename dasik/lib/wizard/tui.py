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

from ..models.disk_model import Partition
from .inventory import DiskInfo
from .recipes import RECIPES, Options, custom_disk, find

_ENTER = (10, 13, curses.KEY_ENTER)
_ESC = 27
_BACKSPACE = (curses.KEY_BACKSPACE, 127, 8)
_QUIT = (ord("q"), ord("Q"))
# What is left of an arrow key when its escape sequence arrives in pieces:
# ESC [ B. On a slow serial line the ESC can reach the application alone,
# ncurses' ESCDELAY expires, and getch() hands back a bare 27 followed by the
# rest as ordinary characters. A menu that quit on ESC therefore quit on an
# arrow — the very keys it tells you to use. Proved with a pty that delivers
# [10, 27, 91] where a terminal would have said KEY_DOWN.
_SEQUENCE_LEAD = (ord("["), ord("O"))
# Everything a sequence may carry, and the subset that ENDS one.
_SEQUENCE_TAIL = tuple(ord(c) for c in "ABCDFHPQRS~0123456789;")
_SEQUENCE_FINAL = tuple(ord(c) for c in "ABCDFHPQRS~")

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


def menu(screen, title: str, rows: Sequence[str],
         details: Optional[Sequence[Sequence[str]]] = None) -> Optional[int]:
    """A row picker. Returns the index, or None if abandoned.

    *details* is one block of lines per row, shown under the list for whichever
    row the cursor is on — so a layout can be understood while choosing it
    rather than after.
    """
    if not rows:
        return None
    index = 0
    while True:
        body = list(rows)
        if details and index < len(details) and details[index]:
            detail = details[index]
            body = body + [""] + [f"  {line}" for line in
                                  ([detail] if isinstance(detail, str) else detail)]
        _frame(screen, title, body, selected=index)
        key = screen.getch()
        if key in _QUIT:
            return None
        if key == _ESC or key in _SEQUENCE_LEAD or key in _SEQUENCE_TAIL:
            # A menu is left with `q`, never with ESC: see _SEQUENCE_LEAD.
            continue
        if key in _ENTER:
            return index
        if key == curses.KEY_UP:
            index = max(0, index - 1)
        elif key == curses.KEY_DOWN:
            index = min(len(rows) - 1, index + 1)


def size_error(value: str) -> Optional[str]:
    """The model's own complaint about a size, or None if it likes it.

    Asked HERE rather than three screens later: a stray key in the ESP size
    field used to reach the recipe build and take the whole session down with a
    pydantic traceback (seen on a VM run).
    """
    return _partition_complaint({"label": "probe", "size": value,
                                 "filesystem": "ext4", "partition_type": "linux"})


def label_error(value: str) -> Optional[str]:
    """The model's own complaint about a label, or None."""
    return _partition_complaint({"label": value, "size": "rest",
                                 "filesystem": "ext4", "partition_type": "linux"})


def _partition_complaint(fields: Dict[str, Any]) -> Optional[str]:
    """Ask the model, so the prompt is exactly as strict as the schema.

    A second, stricter set of rules in the UI is the divergence that took 247
    lines to remove from the action shims (#238); a looser one lets a typo
    reach `parted` with the disk already wiped.
    """
    try:
        Partition.model_validate(fields)
    except Exception as e:      # noqa: BLE001 - pydantic's message is the answer
        return _first_complaint(e)
    return None


def _first_complaint(error: Exception) -> str:
    """The one useful line out of a pydantic ValidationError."""
    for line in str(error).splitlines():
        line = line.strip()
        if line.startswith("Value error,"):
            return line[len("Value error,"):].strip()
        if line.startswith(("String should", "Input should", "Value should")):
            return line
    return str(error).splitlines()[-1].strip()


def prompt(screen, title: str, label: str, default: str = "",
           secret: bool = False, validate=None) -> Optional[str]:
    """A one-line editor. Empty input keeps *default*; ESC abandons.

    *secret* echoes asterisks — the LUKS passphrase is typed on a screen that
    may well be a projector, a serial log, or someone's shoulder.

    *validate* returns a complaint or None. A value it rejects is shown back
    with the reason and asked again, rather than travelling on to fail
    somewhere the user cannot see.
    """
    buffer = ""
    complaint = ""
    while True:
        shown = "*" * len(buffer) if secret else buffer
        hint = f" [{default}]" if default and not secret else ""
        body = [f"{label}{hint}:", f"  {shown}"]
        if complaint:
            body += ["", f"  ! {complaint}"]
        _frame(screen, title, body)
        key = screen.getch()
        if key == _ESC:
            # Cancel — unless this is the head of a split arrow, in which case
            # its leftovers belong in nobody's buffer. Peeked, never waited for:
            # blocking here would mean ESC did nothing until the next keypress.
            if _swallow_sequence(screen):
                continue
            return None
        if key in _ENTER:
            value = buffer if buffer else default
            if validate is not None:
                complaint = validate(value) or ""
                if complaint:
                    buffer = ""
                    continue
            return value
        if key in _BACKSPACE:
            buffer = buffer[:-1]
        elif 32 <= key < 127:
            buffer += chr(key)


def _peek(screen) -> Optional[int]:
    """The next key if one is already waiting, else None. Never blocks."""
    nodelay = getattr(screen, "nodelay", None)
    if nodelay is None:
        return None
    nodelay(True)
    try:
        key = screen.getch()
    finally:
        nodelay(False)
    return None if key in (-1, None) else key


def _swallow_sequence(screen) -> bool:
    """After an ESC: eat the rest of an escape sequence, if that is what it was.

    Returns True when the ESC turned out to be an arrow (or another sequence)
    and has been consumed whole, False when it was a real ESC keypress.
    """
    lead = _peek(screen)
    if lead not in _SEQUENCE_LEAD:
        return False
    while True:
        tail = _peek(screen)
        if tail is None or tail in _SEQUENCE_FINAL:
            return True


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
    rows = [r.title for r in RECIPES]
    details: List[Sequence[str]] = [[r.detail, ""] + r.summary() for r in RECIPES]
    rows.append("Custom — compose the partitions yourself")
    details.append(["One partition at a time: label, size, filesystem, mountpoint.",
                    "Checked as a set before it is accepted."])
    index = menu(screen, "Which layout?", rows, details=details)
    if index is None:
        return None
    return "custom" if index == len(RECIPES) else RECIPES[index].key


def _ask_options(screen, device: str, recipe_key: str,
                 wipe: bool) -> "Optional[tuple[Options, Optional[str]]]":
    """The tunables the chosen recipe actually uses, and nothing else."""
    esp = prompt(screen, "Sizes", "ESP size", default="512MiB",
                 validate=size_error)
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
        swap = prompt(screen, "Swap", "swap size", default="8GiB",
                      validate=size_error)
        if swap is None:
            return None
        values["swap_size"] = swap

    return Options(device=device, **values), passphrase


def _ask_partitions(screen, device: str) -> Optional[List[Dict[str, Any]]]:
    """The custom path: one partition at a time, validated as a set at the end."""
    partitions: List[Dict[str, Any]] = []
    while True:
        number = len(partitions) + 1
        label = prompt(screen, f"Partition {number}", "label", default="",
                       validate=label_error)
        if label is None:
            return None
        size = prompt(screen, f"Partition {number}", "size (e.g. 512MiB, 50%, rest)",
                      default="rest", validate=size_error)
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
        # NOT a yes/no that abandons on "no". Saying no used to end the session,
        # which left no way to just look at what a layout would be — and looking
        # is what an assistant that never applies is for. Both answers compose;
        # only one of them arms the destructive flag.
        choice = menu(
            screen, "This disk is not empty",
            [f"ERASE {disk.path} — set wipe_disk, so `dasik apply` repartitions it",
             f"Simulate — compose the layout WITHOUT erasing {disk.path}"],
            details=[
                [f"{disk.describe()}", "",
                 "Nothing happens now. `dasik plan` will announce the erase, and",
                 "only `dasik apply` carries it out."],
                ["The config is written with wipe_disk: false, so `dasik plan`",
                 "SKIPS this disk with a warning — dasik never silently",
                 "reformats a populated one. Useful to see the block, to keep it",
                 "for later, or to edit the flag yourself when you mean it."]])
        if choice is None:
            return None
        wipe = choice == 0

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
    elif not disk.is_empty:
        warnings.append(
            f"Simulation: {disk.path} holds data and wipe_disk is false, so "
            f"`dasik plan` will SKIP this disk rather than repartition it.")
    _frame(screen, "Review", _review_lines(disk.path, stanza, warnings))
    key = screen.getch()
    if key in _QUIT or key == _ESC:
        return None

    return Choices(device=disk.path, recipe_key=recipe_key, options=options,
                   passphrase=passphrase, hostname=hostname,
                   custom_partitions=list(stanza["partitions"])
                   if recipe_key == "custom" else [])
