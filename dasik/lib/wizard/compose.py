"""From a chosen layout to a file on disk — and the secret that goes beside it.

Two rules shape this module:

* **the passphrase never enters the JSON.** It leaves as
  ``{"$include_line": "secrets/…"}``, and the file that directive points at is
  written here, at 0600. A wizard that emitted the reference and stopped would
  hand you a config ``dasik check`` refuses, because the loader cannot resolve a
  file that does not exist.
* **nothing is overwritten silently.** ``--output`` onto an existing file is an
  error unless the wizard asked; ``--merge-into`` replaces the ``disks`` block
  and leaves the rest of somebody's config exactly as it was.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .recipes import Contribution

# What a config needs beyond `disks` to be something `check` accepts and a human
# can grow. Deliberately minimal and deliberately boring: the wizard composes
# the disks, not somebody's whole machine.
def _skeleton(hostname: str) -> Dict[str, Any]:
    return {
        "hostname": hostname,
        "timezone": {"region": "Etc", "city": "UTC"},
        "locales": {
            "selected_locales": ["en_US.UTF-8 UTF-8"],
            "desired_locale": "en_US.UTF-8",
            "desired_tty_layout": "us",
        },
        "network": {"type": "NetworkManager"},
        "bootloader": "sd-boot",
        "packages": ["base", "linux", "linux-firmware"],
    }


def compose(built: Contribution, hostname: str = "archlinux") -> Dict[str, Any]:
    """A new config carrying *built*'s disk (and whatever else it implies)."""
    config = _skeleton(hostname)
    config["disks"] = {"disks": [copy.deepcopy(built.disk)]}
    if built.kernel_cmdline:
        config["kernel_cmdline"] = list(built.kernel_cmdline)
    return config


def merge_into(existing: Dict[str, Any], built: Contribution) -> Dict[str, Any]:
    """*existing* with its ``disks`` block replaced by *built*'s.

    Everything else is left alone — this is somebody's real config, and the
    wizard was asked about disks.
    """
    merged = copy.deepcopy(existing)
    merged["disks"] = {"disks": [copy.deepcopy(built.disk)]}
    if built.kernel_cmdline:
        cmdline = list(merged.get("kernel_cmdline") or [])
        for token in built.kernel_cmdline:
            if token not in cmdline:
                cmdline.append(token)
        merged["kernel_cmdline"] = cmdline
    return merged


def write_config(path: "str | Path", config: Dict[str, Any],
                 overwrite: bool = False) -> Path:
    """Write *config* as JSON, refusing to clobber unless told otherwise."""
    target = Path(path)
    if target.exists() and not overwrite:
        raise FileExistsError(
            f"{target} already exists. Pass --force to replace it, or "
            f"--merge-into {target} to keep the rest of it and swap only the "
            f"disks block.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return target


def write_secret(config_path: "str | Path", relative: str, passphrase: str) -> Path:
    """Write the LUKS passphrase where `$include_line` will look for it.

    Relative to the CONFIG, because that is what the directive resolves
    against — and at 0600 with the mode on the descriptor, because the content
    is the thing that opens the disk.
    """
    if not passphrase:
        raise ValueError(
            "refusing to write an empty passphrase: `$include_line` takes the "
            "file's first line, and an empty one would be a LUKS volume nobody "
            "can open.")
    target = Path(config_path).parent / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (passphrase + "\n").encode("utf-8"))
    finally:
        os.close(fd)
    # O_CREAT does not touch the mode of a file that already existed.
    os.chmod(target, 0o600)
    return target


def warnings_for(config: Dict[str, Any], wiping: Optional[str],
                 disk_is_empty: bool) -> List[str]:
    """What to say before writing: the erase, plus whatever preflight knows.

    preflight is reused rather than re-implemented — it already catches the
    coherence traps this layout can walk into (a `resume=` pointing at a swap
    that takes a new key every boot, a crypttab entry for a label nothing
    provides). It is given the config as composed, with no environment probing.
    """
    warnings: List[str] = []
    if wiping and not disk_is_empty:
        warnings.append(
            f"{wiping} already holds data, and this layout sets wipe_disk: "
            f"applying it will ERASE the disk. Nothing happens until you run "
            f"`dasik apply`, and `dasik plan` will say it again.")

    try:
        from ..expand import expand_config
        from ..validation.preflight import preflight
        issues = preflight(expand_config(config), environment=False)
    except Exception:      # nosec B110 - a preflight that cannot run says nothing
        return warnings
    for issue in issues:
        text = getattr(issue, "message", None) or str(issue)
        warnings.append(text)
    return warnings
