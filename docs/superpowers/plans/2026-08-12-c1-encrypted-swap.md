# C1 — Encrypted swap with a random key: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a config declare a swap partition that is re-encrypted with a fresh random key on every boot, the way the Arch wiki's `Dm-crypt/Swap_encryption` describes it — detectable by `plan`, capturable by `sync`, and refused when the same config also asks for hibernation.

**Architecture:** A new per-partition field `swap_encryption: "none" | "random"`. `DiskPartitionAction` formats such a partition as a 1 MiB ext2 filesystem whose only job is to carry a persistent `LABEL` (a swap re-`mkswap`ed each boot cannot keep a UUID). A new `EncryptedSwapAction` owns the `/etc/crypttab` line and the `/etc/fstab` line; when the initramfs generator is dracut — which is already the single owner of `/etc/crypttab` — the line is derived inside `DracutBackend.crypttab()` instead, so the file never has two writers. `preflight` aborts when a random-key swap meets hibernation, because a key discarded at shutdown cannot decrypt a resume image.

**Tech Stack:** Python ≥3.10, pydantic v2, pytest (+ hypothesis, mutmut), `Command.execute` wrappers around `mkfs.ext2` / `blkid`.

## Global Constraints

- Everything runs against the mounted install target at `/mnt`; actions reach it through `self._target()` / `Target.path(...)`, never bare absolute paths.
- Shell out through `Command.execute(cmd, args, run_as_chroot=...)` — never `subprocess` directly.
- Never run `execute()`/`apply()` against real hardware in a test. Mock `Command.execute`.
- New logic in `models/`, `actions/` (`plan`/`import_state`), `validation/` is TDD: red, green, refactor.
- Coverage gate is 80% and must not be lowered; `mypy dasik` and `bandit` stay clean; `scripts/mutation.sh` stays clean.
- Key source is `/dev/urandom` (never `/dev/random`: it blocks before the entropy pool is initialised, which at boot is a machine that hangs).
- Crypttab option string default: `swap,offset=2048,cipher=aes-xts-plain64,size=512,sector-size=4096`. `offset=2048` is 2048 × 512 B = the 1 MiB the ext2 occupies.
- Derived names come from the partition label: partition `label: "swap"` ⇒ mapper `/dev/mapper/swap`, ext2 label `cryptswap`.
- Commit after every task with a Conventional Commits subject.

---

### Task 1: The model field

**Files:**
- Modify: `dasik/lib/models/disk_model.py` (add enum + field + validator on `Partition`, around lines 27-42 and 129-203)
- Test: `tests/lib/models/test_disk_model.py`

**Interfaces:**
- Produces: `SwapEncryption` (str Enum, members `NONE = "none"`, `RANDOM = "random"`) and `Partition.swap_encryption: SwapEncryption`, default `SwapEncryption.NONE`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/lib/models/test_disk_model.py
import pytest
from pydantic import ValidationError
from dasik.lib.models.disk_model import Partition, SwapEncryption


def _swap(**over):
    base = {"label": "swap", "size": "8GiB", "filesystem": "swap"}
    base.update(over)
    return base


def test_swap_encryption_defaults_to_none():
    assert Partition(**_swap()).swap_encryption is SwapEncryption.NONE


def test_swap_encryption_random_is_accepted_on_a_swap_partition():
    assert Partition(**_swap(swap_encryption="random")).swap_encryption is SwapEncryption.RANDOM


def test_swap_encryption_random_is_refused_on_a_non_swap_filesystem():
    with pytest.raises(ValidationError, match="swap_encryption"):
        Partition(**_swap(filesystem="ext4", swap_encryption="random"))


def test_swap_encryption_random_conflicts_with_luks():
    with pytest.raises(ValidationError, match="encrypt"):
        Partition(**_swap(swap_encryption="random", encrypt=True, luks_name="cryptswap"))
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/lib/models/test_disk_model.py -k swap_encryption -v`
Expected: FAIL — `ImportError: cannot import name 'SwapEncryption'`.

- [ ] **Step 3: Implement**

```python
# dasik/lib/models/disk_model.py — next to the other enums
class SwapEncryption(str, Enum):
    """How a swap partition is encrypted.

    ``random`` is plain dm-crypt re-keyed on every boot (crypttab's ``swap``
    option). It is NOT ``encrypt: true``, which is LUKS with a persistent key:
    a random key is discarded at shutdown, so nothing written to that swap can
    ever be read back — which is exactly why it forbids hibernation.
    """
    NONE = "none"
    RANDOM = "random"
```

```python
# dasik/lib/models/disk_model.py — on Partition, after `format`
    swap_encryption: SwapEncryption = Field(
        default=SwapEncryption.NONE,
        description="Swap encryption mode. 'random' re-encrypts the swap with a "
                    "fresh key on every boot (no hibernation possible); 'none' "
                    "leaves it plain. Orthogonal to `encrypt`, which is LUKS."
    )
```

```python
# dasik/lib/models/disk_model.py — inside _validate_encryption, before `return self`
        if self.swap_encryption is SwapEncryption.RANDOM:
            if self.filesystem != FileSystemType.SWAP:
                raise ValueError(
                    "swap_encryption='random' only applies to a swap partition "
                    f"(this one is {self.filesystem.value})."
                )
            if self.encrypt:
                raise ValueError(
                    "swap_encryption='random' and encrypt=True are different "
                    "mechanisms and cannot both apply: random re-keys the swap "
                    "every boot (no hibernation), LUKS keeps one key (hibernation "
                    "works). Pick one."
                )
```

- [ ] **Step 4: Run them and watch them pass**

Run: `pytest tests/lib/models/test_disk_model.py -k swap_encryption -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/models/disk_model.py tests/lib/models/test_disk_model.py
git commit -m "feat(disks): a swap partition can declare random-key encryption"
```

---

### Task 2: Pure helpers — names, crypttab line, fstab line

**Files:**
- Create: `dasik/lib/actions/swap_encryption.py`
- Test: `tests/lib/actions/test_swap_encryption_helpers.py`

**Interfaces:**
- Produces:
  - `random_swap_partitions(config: dict) -> list[dict]` — every partition stanza with `swap_encryption == "random"`, across all disks, in config order.
  - `swap_names(part: dict) -> tuple[str, str]` — `(mapper_name, fs_label)`, e.g. `("swap", "cryptswap")`.
  - `crypttab_line(part: dict) -> str`
  - `fstab_line(part: dict) -> str`
  - `CRYPTTAB_OPTIONS: str` — the default option string.
- Consumed by Tasks 4, 5, 6, 7.

- [ ] **Step 1: Write the failing test**

```python
# tests/lib/actions/test_swap_encryption_helpers.py
from dasik.lib.actions.swap_encryption import (
    crypttab_line, fstab_line, random_swap_partitions, swap_names)


def _cfg(*parts):
    return {"disks": {"disks": [{"device": "/dev/vda", "partitions": list(parts)}]}}


RANDOM_SWAP = {"label": "swap", "filesystem": "swap", "swap_encryption": "random"}


def test_random_swap_partitions_finds_only_the_declared_ones():
    cfg = _cfg({"label": "root", "filesystem": "btrfs"},
               RANDOM_SWAP,
               {"label": "swap2", "filesystem": "swap"})
    assert random_swap_partitions(cfg) == [RANDOM_SWAP]


def test_random_swap_partitions_is_empty_without_disks():
    assert random_swap_partitions({}) == []


def test_names_derive_from_the_partition_label():
    assert swap_names(RANDOM_SWAP) == ("swap", "cryptswap")
    assert swap_names({"label": "swap2"}) == ("swap2", "cryptswap2")


def test_crypttab_line_matches_the_wiki_procedure():
    assert crypttab_line(RANDOM_SWAP) == (
        "swap LABEL=cryptswap /dev/urandom "
        "swap,offset=2048,cipher=aes-xts-plain64,size=512,sector-size=4096")


def test_fstab_line_names_the_mapper_device():
    assert fstab_line(RANDOM_SWAP) == "/dev/mapper/swap none swap defaults 0 0"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/lib/actions/test_swap_encryption_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError: dasik.lib.actions.swap_encryption`.

- [ ] **Step 3: Implement**

```python
# dasik/lib/actions/swap_encryption.py
"""Random-key swap: the pure derivations, shared by every writer.

A swap encrypted with a key drawn from /dev/urandom is re-created on every boot,
so `mkswap` erases whatever UUID it had. The wiki's answer (Dm-crypt/Swap
encryption#UUID and LABEL) is to put a 1 MiB ext2 filesystem in FRONT of the
swap purely to carry a persistent LABEL, and to start the encrypted area after
it with `offset=2048` (2048 sectors x 512 B = 1 MiB). Addressing the device by
that label is what keeps crypttab from reformatting the wrong disk after a
partition renumbering.

Everything here is a pure function of the config so that DiskPartitionAction,
DracutBackend, EncryptedSwapAction and preflight all derive the SAME strings.
"""
from __future__ import annotations
from typing import Any, Dict, List, Tuple

KEY_SOURCE = "/dev/urandom"
# The 1 MiB ext2 label filesystem lives in the first 2048 sectors of 512 B.
LABEL_OFFSET_SECTORS = 2048
LABEL_FS_SIZE = "1M"
CRYPTTAB_OPTIONS = (f"swap,offset={LABEL_OFFSET_SECTORS},"
                    "cipher=aes-xts-plain64,size=512,sector-size=4096")


def random_swap_partitions(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every partition stanza declaring `swap_encryption: random`, in config order."""
    out: List[Dict[str, Any]] = []
    disks = config.get("disks", {})
    if not isinstance(disks, dict):
        return out
    for disk in disks.get("disks", []) or []:
        for part in disk.get("partitions", []) or []:
            if str(part.get("swap_encryption", "none")) == "random":
                out.append(part)
    return out


def swap_names(part: Dict[str, Any]) -> Tuple[str, str]:
    """(device-mapper name, ext2 label) derived from the partition label.

    Derived rather than configurable so two random swaps on one machine cannot
    collide, and so nothing has to be threaded through four call sites.
    """
    label = str(part.get("label") or "swap")
    return label, f"crypt{label}"


def crypttab_line(part: Dict[str, Any]) -> str:
    mapper, fs_label = swap_names(part)
    return f"{mapper} LABEL={fs_label} {KEY_SOURCE} {CRYPTTAB_OPTIONS}"


def fstab_line(part: Dict[str, Any]) -> str:
    mapper, _ = swap_names(part)
    return f"/dev/mapper/{mapper} none swap defaults 0 0"
```

- [ ] **Step 4: Run it and watch it pass**

Run: `pytest tests/lib/actions/test_swap_encryption_helpers.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/swap_encryption.py tests/lib/actions/test_swap_encryption_helpers.py
git commit -m "feat(swap): derive the crypttab/fstab lines of a random-key swap"
```

---

### Task 3: Format the label partition instead of the swap

**Files:**
- Modify: `dasik/lib/actions/disk_partition_action.py:1095-1096` (the `SWAP` branch of `_format_partition`)
- Test: `tests/lib/actions/test_disk_partition_format.py` (add to the existing file if present; otherwise create)

**Interfaces:**
- Consumes: `swap_names` from Task 2.
- Produces: no new symbols — a behaviour change in `_format_partition`.

- [ ] **Step 1: Write the failing test**

```python
# tests/lib/actions/test_disk_partition_format.py
from unittest.mock import patch
from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.models.disk_model import Partition


def _action():
    action = DiskPartitionAction({}, None)
    action.partition_map = {"swap": "/dev/vda2"}
    return action


def test_a_plain_swap_partition_is_mkswapped():
    part = Partition(label="swap", size="8GiB", filesystem="swap")
    with patch("dasik.lib.actions.disk_partition_action.Command.execute") as run:
        _action()._format_partition("/dev/vda", part)
    run.assert_called_once_with("mkswap", ["-L", "swap", "/dev/vda2"])


def test_a_random_key_swap_gets_a_1MiB_ext2_label_filesystem_instead():
    part = Partition(label="swap", size="8GiB", filesystem="swap",
                     swap_encryption="random")
    with patch("dasik.lib.actions.disk_partition_action.Command.execute") as run:
        _action()._format_partition("/dev/vda", part)
    run.assert_called_once_with(
        "mkfs.ext2", ["-F", "-L", "cryptswap", "/dev/vda2", "1M"])
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/lib/actions/test_disk_partition_format.py -v`
Expected: the second test FAILs — `mkswap` was called.

- [ ] **Step 3: Implement**

```python
# dasik/lib/actions/disk_partition_action.py — replace the SWAP branch
        elif partition.filesystem == FileSystemType.SWAP:
            if partition.swap_encryption is SwapEncryption.RANDOM:
                # No mkswap here: crypttab's `swap` option runs mkswap itself on
                # every boot, on the mapper device. What this partition needs is
                # the 1 MiB ext2 filesystem that carries the persistent LABEL the
                # crypttab entry addresses — the encrypted area starts after it
                # (offset=2048). Formatting it as swap instead would leave the
                # crypttab line pointing at a label that does not exist.
                _mapper, fs_label = swap_names({"label": partition.label})
                Command.execute("mkfs.ext2",
                                ["-F", "-L", fs_label, part_device, LABEL_FS_SIZE])
            else:
                Command.execute("mkswap", ["-L", partition.label, part_device])
```

Add the imports at the top of the module:

```python
from .swap_encryption import LABEL_FS_SIZE, swap_names
from ..models.disk_model import SwapEncryption
```

(`disk_partition_action.py` already imports `FileSystemType` and friends from
`..models.disk_model`; extend that import instead of adding a second line.)

- [ ] **Step 4: Run it and watch it pass**

Run: `pytest tests/lib/actions/test_disk_partition_format.py -v && pytest tests/lib/actions -k disk -q`
Expected: all pass — the existing disk tests must stay green.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/disk_partition_action.py tests/lib/actions/test_disk_partition_format.py
git commit -m "feat(disks): format a random-key swap as its 1 MiB ext2 label partition"
```

---

### Task 4: dracut derives the line, and hibernation stops counting it

**Files:**
- Modify: `dasik/lib/actions/initramfs/dracut.py:132-203` (`crypttab()`)
- Modify: `dasik/lib/actions/initramfs/base.py:52-71` (`detect_hibernation`)
- Test: `tests/lib/actions/initramfs/test_dracut_crypttab.py`, `tests/lib/actions/initramfs/test_detect.py`

**Interfaces:**
- Consumes: `crypttab_line`, `random_swap_partitions` from Task 2.
- Produces: `DracutBackend.crypttab()` output now contains the random-swap line after the derived LUKS entries.

- [ ] **Step 1: Write the failing tests**

```python
# tests/lib/actions/initramfs/test_dracut_crypttab.py
from dasik.lib.actions.initramfs.dracut import DracutBackend


def _cfg(**over):
    cfg = {
        "initramfs": "dracut",
        "disks": {"disks": [{"device": "/dev/vda", "partitions": [
            {"label": "root", "filesystem": "btrfs", "encrypt": True,
             "luks_name": "cryptroot", "mountpoint": "/"},
            {"label": "swap", "filesystem": "swap", "swap_encryption": "random"},
        ]}]},
    }
    cfg.update(over)
    return cfg


def test_crypttab_carries_the_random_swap_line():
    text = DracutBackend(_cfg(), None).crypttab()
    assert ("swap LABEL=cryptswap /dev/urandom "
            "swap,offset=2048,cipher=aes-xts-plain64,size=512,sector-size=4096") in text


def test_the_derived_swap_line_is_not_duplicated_by_a_captured_one():
    cfg = _cfg(files=[{"path": "/etc/crypttab",
                       "content": "swap LABEL=cryptswap /dev/urandom swap,offset=2048\n"}])
    text = DracutBackend(cfg, None).crypttab()
    assert text.count("swap LABEL=cryptswap") == 1
```

```python
# tests/lib/actions/initramfs/test_detect.py  (add to the existing file)
from dasik.lib.actions.initramfs.base import detect_hibernation


def test_a_random_key_swap_is_not_a_hibernation_device():
    cfg = {"disks": {"disks": [{"partitions": [
        {"label": "swap", "filesystem": "swap", "swap_encryption": "random"}]}]}}
    assert detect_hibernation(cfg) is False


def test_a_plain_swap_still_asks_for_the_resume_module():
    cfg = {"disks": {"disks": [{"partitions": [
        {"label": "swap", "filesystem": "swap"}]}]}}
    assert detect_hibernation(cfg) is True
```

- [ ] **Step 2: Run them and watch them fail**

Run: `pytest tests/lib/actions/initramfs -k "random_swap or hibernation" -v`
Expected: both new tests FAIL (no swap line in the crypttab; `detect_hibernation` returns True).

- [ ] **Step 3: Implement**

```python
# dasik/lib/actions/initramfs/dracut.py — inside crypttab(), after the LUKS loop
        # A random-key swap is plain dm-crypt, not LUKS, so it never appears in
        # the loop above — but /etc/crypttab has exactly one owner and this is
        # it. Derived (not captured) so the line always matches what
        # DiskPartitionAction formatted and what preflight validated.
        for part in random_swap_partitions(self.config):
            mapper, _ = swap_names(part)
            derived[mapper] = crypttab_line(part)
```

```python
# dasik/lib/actions/initramfs/dracut.py — imports
from ..swap_encryption import crypttab_line, random_swap_partitions, swap_names
```

```python
# dasik/lib/actions/initramfs/base.py — inside detect_hibernation's partition loop
                if part.get("filesystem") == "swap":
                    # A random-key swap cannot hold a hibernation image: the key
                    # is discarded at shutdown, so resume would have nothing to
                    # read it with. Declaring the resume module for it would only
                    # slow the boot down looking for an image that cannot exist.
                    if str(part.get("swap_encryption", "none")) == "random":
                        continue
                    return True
```

- [ ] **Step 4: Run them and watch them pass**

Run: `pytest tests/lib/actions/initramfs -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/initramfs tests/lib/actions/initramfs
git commit -m "feat(initramfs): dracut owns the random-swap crypttab line, and it never asks for resume"
```

---

### Task 5: `EncryptedSwapAction` — fstab, and crypttab when dracut is not there

**Files:**
- Create: `dasik/lib/actions/encrypted_swap_action.py`
- Modify: `dasik/lib/actions/actions_handler_v2.py` (register in phase 4, after `DropFilesAction`)
- Test: `tests/lib/actions/test_encrypted_swap_action.py`

**Interfaces:**
- Consumes: `crypttab_line`, `fstab_line`, `random_swap_partitions`, `swap_names` (Task 2).
- Produces: `EncryptedSwapAction` with domain `"swap_encryption"`, items = mapper names; `plan()`, `apply()`, `managed_keys()`, `actual()`, `import_state()` (returns `{}` — capture belongs to the `disks` block, Task 6).

- [ ] **Step 1: Write the failing test**

```python
# tests/lib/actions/test_encrypted_swap_action.py
import os
from types import SimpleNamespace

import pytest

from dasik.lib.actions.encrypted_swap_action import EncryptedSwapAction
from dasik.lib.state.change import Op


class _Target:
    def __init__(self, root):
        self.root = str(root)

    def path(self, canonical):
        return os.path.join(self.root, canonical.lstrip("/"))


@pytest.fixture
def target(tmp_path):
    os.makedirs(tmp_path / "etc", exist_ok=True)
    (tmp_path / "etc" / "fstab").write_text("UUID=abc / btrfs defaults 0 0\n")
    return _Target(tmp_path)


def _cfg(**over):
    cfg = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
        {"label": "swap", "filesystem": "swap", "swap_encryption": "random"}]}]}}
    cfg.update(over)
    return cfg


def _action(cfg, target):
    return EncryptedSwapAction(cfg, SimpleNamespace(target=target))


def test_plan_installs_a_declared_swap_that_the_target_lacks(target):
    changes = _action(_cfg(), target).plan(managed=[])
    assert [(c.op, c.item) for c in changes] == [(Op.INSTALL, "swap")]


def test_apply_writes_both_lines(target):
    action = _action(_cfg(), target)
    action.apply(action.plan(managed=[]))
    fstab = open(target.path("/etc/fstab")).read()
    crypttab = open(target.path("/etc/crypttab")).read()
    assert "/dev/mapper/swap none swap defaults 0 0" in fstab
    assert "swap LABEL=cryptswap /dev/urandom" in crypttab


def test_a_second_plan_after_apply_is_silent(target):
    action = _action(_cfg(), target)
    action.apply(action.plan(managed=[]))
    assert _action(_cfg(), target).plan(managed=["swap"]) == []


def test_dracut_owns_the_crypttab_so_this_action_only_writes_fstab(target):
    cfg = _cfg(initramfs="dracut")
    action = _action(cfg, target)
    action.apply(action.plan(managed=[]))
    assert "/dev/mapper/swap" in open(target.path("/etc/fstab")).read()
    assert not os.path.exists(target.path("/etc/crypttab"))


def test_an_owned_swap_no_longer_declared_is_removed(target):
    action = _action(_cfg(), target)
    action.apply(action.plan(managed=[]))
    dropped = _action({}, target)
    changes = dropped.plan(managed=["swap"])
    assert [(c.op, c.item) for c in changes] == [(Op.REMOVE, "swap")]
    dropped.apply(changes)
    assert "/dev/mapper/swap" not in open(target.path("/etc/fstab")).read()
    assert "LABEL=cryptswap" not in open(target.path("/etc/crypttab")).read()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/lib/actions/test_encrypted_swap_action.py -v`
Expected: FAIL — `ModuleNotFoundError: dasik.lib.actions.encrypted_swap_action`.

- [ ] **Step 3: Implement**

```python
# dasik/lib/actions/encrypted_swap_action.py
"""Action: the /etc/fstab and /etc/crypttab lines of a random-key swap.

The partition itself is formatted by DiskPartitionAction (a 1 MiB ext2 label
filesystem). What is left are two lines nobody else writes:

* **fstab** — `genfstab` runs during the install and can only see what is
  mounted. /dev/mapper/swap does not exist yet: it is created at the FIRST boot,
  by the crypttab entry. So the swap line has to be appended afterwards.
* **crypttab** — owned by DracutBackend whenever the generator is dracut (it
  composes the derived root entry there). With mkinitcpio nobody composes it, so
  this action does; the two never write it at the same time.
"""
from __future__ import annotations
import os
from typing import Any, Dict, List

from .abstract_action import AbstractAction
from .swap_encryption import crypttab_line, fstab_line, random_swap_partitions, swap_names
from ..state.change import Change, Op

_FSTAB = "/etc/fstab"
_CRYPTTAB = "/etc/crypttab"


class EncryptedSwapAction(AbstractAction):
    """Own the fstab (and, without dracut, crypttab) lines of a random-key swap."""

    _DOMAIN = "swap_encryption"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._cfg = cfg
        self._parts = random_swap_partitions(cfg)
        # dracut composes /etc/crypttab itself (derived root entry + the swap
        # line, see DracutBackend.crypttab). Writing it here too would mean two
        # owners rewriting the same file on alternating applies.
        self._dracut_owns_crypttab = cfg.get("initramfs") == "dracut"

    @property
    def name(self) -> str:
        return "Encrypted Swap"

    @property
    def is_optional(self) -> bool:
        return True

    @classmethod
    def empty_config(cls):
        return {}

    # --- paths --------------------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _p(self, canonical: str) -> str:
        t = self._target()
        return t.path(canonical) if t is not None else "/mnt" + canonical

    def _read(self, canonical: str) -> str:
        try:
            with open(self._p(canonical), "r") as f:
                return f.read()
        except FileNotFoundError:
            return ""

    # --- desired vs actual --------------------------------------------- #

    def _desired(self) -> Dict[str, Dict[str, str]]:
        """mapper name -> {"fstab": line, "crypttab": line}."""
        out: Dict[str, Dict[str, str]] = {}
        for part in self._parts:
            mapper, _ = swap_names(part)
            out[mapper] = {"fstab": fstab_line(part), "crypttab": crypttab_line(part)}
        return out

    def _converged(self, mapper: str, lines: Dict[str, str]) -> bool:
        if lines["fstab"] not in self._read(_FSTAB).splitlines():
            return False
        if self._dracut_owns_crypttab:
            return True
        return lines["crypttab"] in self._read(_CRYPTTAB).splitlines()

    def actual(self) -> set:
        if self._target() is None:
            return set()
        return {m for m, lines in self._desired().items() if self._converged(m, lines)}

    # --- v3 contract ---------------------------------------------------- #

    def plan(self, managed):
        desired = self._desired()
        actual = self.actual()
        changes: List[Change] = []
        for mapper in desired:
            if mapper not in actual:
                changes.append(Change(self._DOMAIN, Op.INSTALL, mapper,
                                      reason="crypttab + fstab entry"))
        # Ownership decides removal, as everywhere else: a mapper this tool never
        # recorded is somebody else's swap and is left alone.
        for mapper in managed or []:
            if mapper not in desired and self._mentions(mapper):
                changes.append(Change(self._DOMAIN, Op.REMOVE, mapper,
                                      reason="no longer declared"))
        return changes

    def _mentions(self, mapper: str) -> bool:
        return (f"/dev/mapper/{mapper} " in self._read(_FSTAB)
                or any(line.split()[:1] == [mapper]
                       for line in self._read(_CRYPTTAB).splitlines() if line.strip()))

    def apply(self, changes) -> None:
        if not changes or self._target() is None:
            return
        desired = self._desired()
        for change in changes:
            if change.op is Op.REMOVE:
                self._drop(change.item)
            else:
                self._write(desired[change.item])

    def _append(self, canonical: str, line: str) -> None:
        path = self._p(canonical)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        current = self._read(canonical)
        if line in current.splitlines():
            return
        with open(path, "a") as f:
            if current and not current.endswith("\n"):
                f.write("\n")
            f.write(line + "\n")

    def _write(self, lines: Dict[str, str]) -> None:
        self._append(_FSTAB, lines["fstab"])
        if not self._dracut_owns_crypttab:
            self._append(_CRYPTTAB, lines["crypttab"])

    def _drop(self, mapper: str) -> None:
        for canonical, matches in (
            (_FSTAB, lambda ln: ln.split()[:1] == [f"/dev/mapper/{mapper}"]),
            (_CRYPTTAB, lambda ln: ln.split()[:1] == [mapper]),
        ):
            current = self._read(canonical)
            if not current:
                continue
            kept = [ln for ln in current.splitlines() if not (ln.strip() and matches(ln))]
            with open(self._p(canonical), "w") as f:
                f.write("\n".join(kept) + ("\n" if kept else ""))

    def managed_keys(self) -> dict:
        return {self._DOMAIN: list(self._desired().keys())}

    def import_state(self, managed=None) -> dict:
        """Nothing: a random-key swap is a property of a PARTITION, and the
        `disks` block has exactly one author — DiskPartitionAction.import_state,
        which captures `swap_encryption` alongside the rest of the layout. Two
        actions emitting `disks` would clobber each other (ConfigWriter.merge
        overwrites a key, it cannot merge two halves of one)."""
        return {}

    # --- legacy executor bridge ----------------------------------------- #

    def is_needed(self) -> bool:
        return bool(self.plan(managed=[]))

    def execute(self) -> None:
        self.apply(self.plan(managed=[]))
```

Register it (phase 4, after `DropFilesAction`, so a verbatim `files` crypttab is
already on disk when this merges its line into it, and long before phase 5 builds
the initramfs around that file):

```python
# dasik/lib/actions/actions_handler_v2.py — import with the others
    from .encrypted_swap_action import EncryptedSwapAction
```

```python
# dasik/lib/actions/actions_handler_v2.py — right after the DropFilesAction entry
    # The fstab/crypttab lines of a random-key swap. After DropFiles (which may
    # write a verbatim /etc/crypttab) and after BaseInstall (genfstab), because
    # both files must exist before a line is merged into them.
    register_action(
        action_class=EncryptedSwapAction,
        config_key='__root__',   # reads `disks` — the mode lives per partition
        is_optional=True,
    )
```

- [ ] **Step 4: Run it and watch it pass**

Run: `pytest tests/lib/actions/test_encrypted_swap_action.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/encrypted_swap_action.py dasik/lib/actions/actions_handler_v2.py tests/lib/actions/test_encrypted_swap_action.py
git commit -m "feat(swap): own the fstab and crypttab lines of a random-key swap"
```

---

### Task 6: `sync` captures the mode

**Files:**
- Modify: `dasik/lib/actions/disk_partition_action.py` (the discovery that describes a partition — the `_role_label` / `_describe_partition` neighbourhood, around lines 340-520)
- Test: `tests/lib/actions/test_disk_partition_import.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: discovered partition dicts may carry `"swap_encryption": "random"` and `"filesystem": "swap"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/lib/actions/test_disk_partition_import.py
def test_an_ext2_partition_named_by_a_random_swap_crypttab_captures_as_swap(monkeypatch, tmp_path):
    """The live machine shows ext2 with LABEL=cryptswap — the swap itself only
    exists behind /dev/mapper. The crypttab entry is what identifies it."""
    action = _import_action(monkeypatch, tmp_path, lsblk=[
        {"name": "vda2", "fstype": "ext2", "label": "cryptswap", "mountpoint": None},
    ], crypttab="swap LABEL=cryptswap /dev/urandom swap,offset=2048\n")
    part = _only_partition(action.import_state())
    assert part["filesystem"] == "swap"
    assert part["swap_encryption"] == "random"


def test_a_plain_ext2_partition_is_not_mistaken_for_a_swap(monkeypatch, tmp_path):
    action = _import_action(monkeypatch, tmp_path, lsblk=[
        {"name": "vda2", "fstype": "ext2", "label": "cryptswap", "mountpoint": None},
    ], crypttab="")
    part = _only_partition(action.import_state())
    assert part.get("swap_encryption", "none") == "none"
```

Write `_import_action` / `_only_partition` as local helpers mirroring the
existing fixtures in that test module: they must monkeypatch the `lsblk`/`findmnt`
probes the action already uses and write `crypttab` to `<target>/etc/crypttab`.

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/lib/actions/test_disk_partition_import.py -k random -v`
Expected: FAIL — the captured partition says `ext2`, with no `swap_encryption`.

- [ ] **Step 3: Implement**

Read `/etc/crypttab` once per import, build `{fs_label: mapper}` for every entry
whose key source is `/dev/urandom` and whose options contain `swap`, then, while
describing a partition:

```python
# dasik/lib/actions/disk_partition_action.py
    def _random_swap_labels(self) -> "set[str]":
        """ext2 labels a crypttab `swap` entry re-encrypts on every boot.

        Reading the machine, not the config: the partition itself looks like a
        1 MiB ext2 filesystem, and only the crypttab line says it is really the
        front of a random-key swap. Without this, sync captured it as an ext2
        data partition and re-applying the captured config silently dropped the
        swap.
        """
        labels: "set[str]" = set()
        for raw in self._read_target_file("/etc/crypttab").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split()
            if len(fields) < 4 or fields[2] != "/dev/urandom":
                continue
            if "swap" not in fields[3].split(","):
                continue
            device = fields[1]
            if device.startswith("LABEL="):
                labels.add(device.split("=", 1)[1])
        return labels
```

and in the per-partition description:

```python
        if (label or "") in self._random_swap_labels():
            described["filesystem"] = "swap"
            described["swap_encryption"] = "random"
```

`_read_target_file` is the module's existing helper for reading a path under the
target; if it does not exist, add it next to the other `_p(...)` helpers:

```python
    def _read_target_file(self, canonical: str) -> str:
        try:
            with open(self._p(canonical), "r") as f:
                return f.read()
        except (FileNotFoundError, OSError):
            return ""
```

- [ ] **Step 4: Run it and watch it pass**

Run: `pytest tests/lib/actions/test_disk_partition_import.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/actions/disk_partition_action.py tests/lib/actions/test_disk_partition_import.py
git commit -m "feat(sync): capture a random-key swap as the partition mode, not as ext2"
```

---

### Task 7: preflight refuses the impossible combinations

**Files:**
- Modify: `dasik/lib/validation/preflight.py` (new `_check_random_swap`, registered with the other checks; extend `_declared_block_ids`)
- Test: `tests/lib/validation/test_preflight_random_swap.py`

**Interfaces:**
- Consumes: `random_swap_partitions`, `swap_names`, `crypttab_line` (Task 2).
- Produces: `_check_random_swap(config) -> List[Issue]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/lib/validation/test_preflight_random_swap.py
from dasik.lib.validation.preflight import preflight


def _cfg(**over):
    cfg = {"disks": {"disks": [{"device": "/dev/vda", "partitions": [
        {"label": "swap", "filesystem": "swap", "swap_encryption": "random"}]}]}}
    cfg.update(over)
    return cfg


def _codes(issues, level):
    return {i.code for i in issues if i.level == level}


def test_a_random_swap_with_a_resume_parameter_is_an_error():
    issues = preflight(_cfg(kernel_cmdline=["resume=/dev/mapper/swap"]))
    assert "random_swap_hibernation" in _codes(issues, "error")


def test_a_random_swap_alone_is_accepted():
    assert "random_swap_hibernation" not in _codes(preflight(_cfg()), "error")


def test_a_verbatim_crypttab_that_omits_the_derived_line_is_an_error():
    issues = preflight(_cfg(files=[{"path": "/etc/crypttab",
                                    "content": "# nothing here\n"}]))
    assert "random_swap_crypttab_conflict" in _codes(issues, "error")


def test_the_derived_label_is_not_reported_as_an_undeclared_device():
    issues = preflight(_cfg(files=[{"path": "/etc/crypttab",
                                    "content": "swap LABEL=cryptswap /dev/urandom "
                                               "swap,offset=2048\n"}]))
    assert "crypttab_undeclared_device" not in _codes(issues, "error")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `pytest tests/lib/validation/test_preflight_random_swap.py -v`
Expected: three FAIL (no such code; and the existing `_check_crypttab` flags
`LABEL=cryptswap` as an undeclared device).

- [ ] **Step 3: Implement**

```python
# dasik/lib/validation/preflight.py
def _check_random_swap(config: Dict[str, Any]) -> List[Issue]:
    """A random-key swap is incompatible with hibernation, by construction.

    The key is drawn from /dev/urandom at every boot and discarded at shutdown,
    so a resume image written with the previous key can never be read back. The
    kernel would find garbage where the image should be. This is provable from
    the config alone, which makes it an error rather than a warning — and it has
    to be an error, because the failure mode is a machine that hibernates
    successfully and loses the session on the way back.
    """
    parts = random_swap_partitions(config)
    if not parts:
        return []
    issues: List[Issue] = []
    resume = [w for token in config.get("kernel_cmdline", []) or []
              for w in str(token).split() if w.startswith("resume=")]
    if resume:
        issues.append(Issue(
            "error", "random_swap_hibernation",
            f"a swap declares swap_encryption='random' while the kernel cmdline "
            f"asks to resume from it ({resume[0]}): the random key is discarded "
            f"at shutdown, so the hibernation image can never be decrypted. Use "
            f"an encrypted LUKS swap (`encrypt: true`) to hibernate."))

    verbatim = _crypttab_content(config)
    if verbatim:
        present = set(verbatim.splitlines())
        for part in parts:
            line = crypttab_line(part)
            mapper, _ = swap_names(part)
            if not any(ln.strip().split()[:1] == [mapper] for ln in present if ln.strip()):
                issues.append(Issue(
                    "error", "random_swap_crypttab_conflict",
                    f"the config declares its own /etc/crypttab in `files`, so "
                    f"dasik will not merge the derived entry into it, and the "
                    f"random-key swap {mapper!r} would never be opened. Add this "
                    f"line to that file: {line}"))
    return issues
```

Extend `_declared_block_ids` so the derived ext2 label counts as declared:

```python
    # The 1 MiB label filesystem a random-key swap is addressed by. Without it
    # the crypttab check reports the tool's own derived entry as pointing at an
    # undeclared device — and worse, as destructive, since it carries `swap`.
    for part in random_swap_partitions(config):
        ids.add(swap_names(part)[1])
```

Import at the top and register `_check_random_swap` in the list of checks
`preflight()` runs (the same place `_check_crypttab` is listed):

```python
from ..actions.swap_encryption import crypttab_line, random_swap_partitions, swap_names
```

- [ ] **Step 4: Run it and watch it pass**

Run: `pytest tests/lib/validation -v`
Expected: all pass, existing preflight tests included.

- [ ] **Step 5: Commit**

```bash
git add dasik/lib/validation/preflight.py tests/lib/validation/test_preflight_random_swap.py
git commit -m "fix(preflight): a random-key swap cannot hibernate, and its label is declared"
```

---

### Task 8: The two matrices, the sample config, and the round trips

**Files:**
- Create: `config/vm-swap-random.json`
- Modify: `tests/lib/test_feature_detectability.py`
- Modify: `tests/lib/test_feature_sync_capture.py`
- Test: both of the above

**Interfaces:**
- Consumes: everything from Tasks 1-7.

- [ ] **Step 1: Write the sample config**

```json
{
  "hostname": "vm-swap-random",
  "bootloader": "sd-boot",
  "initramfs": "dracut",
  "enable_microcode": false,
  "timezone": {"region": "Europe", "city": "Madrid"},
  "disks": {
    "disks": [
      {
        "device": "/dev/vda",
        "partition_table": "gpt",
        "wipe_disk": true,
        "partitions": [
          {"label": "esp", "size": "512MiB", "filesystem": "fat32",
           "partition_type": "esp", "mountpoint": "/boot"},
          {"label": "swap", "size": "2GiB", "filesystem": "swap",
           "partition_type": "linux-swap", "swap_encryption": "random"},
          {"label": "root", "size": "rest", "filesystem": "ext4", "mountpoint": "/"}
        ]
      }
    ]
  },
  "users": [{"username": "test", "password": "test", "groups": ["wheel"]}],
  "packages": ["base", "linux", "linux-firmware"]
}
```

- [ ] **Step 2: Write the failing matrix tests**

```python
# tests/lib/test_feature_detectability.py  (append)
def test_random_swap_missing_on_the_target_is_planned(tmp_target):
    changes = plan_for(SWAP_RANDOM_CONFIG, tmp_target)
    assert any(c.domain == "swap_encryption" and c.op is Op.INSTALL for c in changes)


def test_random_swap_present_on_the_target_plans_nothing(tmp_target):
    apply_config(SWAP_RANDOM_CONFIG, tmp_target)
    assert plan_for(SWAP_RANDOM_CONFIG, tmp_target) == []


def test_random_swap_owned_but_no_longer_declared_is_removed(tmp_target):
    apply_config(SWAP_RANDOM_CONFIG, tmp_target)
    changes = plan_for(strip_block(SWAP_RANDOM_CONFIG), tmp_target,
                       managed={"swap_encryption": ["swap"]})
    assert any(c.domain == "swap_encryption" and c.op is Op.REMOVE for c in changes)


def test_an_unowned_swap_line_someone_else_wrote_is_left_alone(tmp_target):
    write_fstab_line(tmp_target, "/dev/mapper/other none swap defaults 0 0")
    changes = plan_for(strip_block(SWAP_RANDOM_CONFIG), tmp_target, managed={})
    assert not [c for c in changes if c.domain == "swap_encryption"]
```

```python
# tests/lib/test_feature_sync_capture.py  (append)
def test_a_machine_with_a_random_swap_captures_the_mode(tmp_target):
    captured = sync_from(tmp_target, crypttab=RANDOM_SWAP_CRYPTTAB, lsblk=EXT2_CRYPTSWAP)
    part = only_swap_partition(captured)
    assert part["swap_encryption"] == "random"


def test_a_machine_without_one_invents_nothing(tmp_target):
    captured = sync_from(tmp_target, crypttab="", lsblk=EXT2_CRYPTSWAP)
    assert "swap_encryption" not in only_partition(captured)


def test_the_captured_config_validates_and_replans_to_nothing(tmp_target):
    captured = sync_from(tmp_target, crypttab=RANDOM_SWAP_CRYPTTAB, lsblk=EXT2_CRYPTSWAP)
    JsonModel(**captured)                       # `check` must accept it
    assert plan_for(expand_config(captured), tmp_target) == []
```

Reuse the module's existing helpers (`plan_for`, `apply_config`, `sync_from`,
`strip_block`, …) rather than inventing new ones; add `SWAP_RANDOM_CONFIG` next
to the other feature fixtures at the top of each file, loading
`config/vm-swap-random.json`.

- [ ] **Step 3: Run them and watch them fail, then pass**

Run: `pytest tests/lib/test_feature_detectability.py tests/lib/test_feature_sync_capture.py -k swap -v`
Expected: FAIL first (helpers/fixtures missing), then all pass once wired.

- [ ] **Step 4: Drive the verbs against the sample config**

```bash
.venv/bin/dasik check config/vm-swap-random.json          # rc 0
.venv/bin/dasik plan  config/vm-swap-random.json          # shows [swap_encryption] + [disks]
```

`plan` fails off Arch hardware with `CommandNotFoundException` for the disk
probes — that is expected and is what the VM pass in the final task is for.
Record the actual output in the PR body either way.

- [ ] **Step 5: Commit**

```bash
git add config/vm-swap-random.json tests/lib/test_feature_detectability.py tests/lib/test_feature_sync_capture.py
git commit -m "test(swap): the detectability and capture matrices for a random-key swap"
```

---

### Task 9: Docs and the gates

**Files:**
- Create: `docs/wiki/Swap.md`
- Modify: `docs/wiki/_Sidebar.md`, `docs/wiki/Disks.md` (link), `docs/config-reference.md`

- [ ] **Step 1: Write the wiki page**

`docs/wiki/Swap.md` covers, in this order: the two modes and the one question
that decides between them (do you hibernate?); the random-key mode with the
config snippet, what dasik writes (`mkfs.ext2 -L cryptswap … 1M`, the crypttab
line, the fstab line) and why the 1 MiB filesystem exists at all; the LUKS mode
with the `laptop-p14s.json` snippet and the note that the resume module reaches
the initramfs automatically; what `sync` captures for each; and the error
message preflight raises when both are asked for at once.

- [ ] **Step 2: Add the sidebar entry and the cross-links**

In `docs/wiki/_Sidebar.md`, add `Swap` under the disks/boot group. In
`docs/wiki/Disks.md`, link to it from wherever swap partitions are first
mentioned.

- [ ] **Step 3: Document the field**

In `docs/config-reference.md`, add `swap_encryption` to the partition field
table: type `"none" | "random"`, default `"none"`, captured by `sync` = yes.

- [ ] **Step 4: Run every gate**

```bash
pytest --cov=dasik          # >= 80%
mypy dasik                  # clean
bandit -r dasik -q          # rc 0
scripts/mutation.sh         # clean
```

- [ ] **Step 5: Commit**

```bash
git add docs/wiki/Swap.md docs/wiki/_Sidebar.md docs/wiki/Disks.md docs/config-reference.md
git commit -m "docs(swap): the two encrypted-swap modes and when each one is right"
```

---

## Self-review notes

* **Spec coverage.** Every C1 requirement maps to a task: the model field (T1),
  the derived strings (T2), the 1 MiB ext2 (T3), dracut's crypttab and the resume
  exclusion (T4), the fstab/crypttab ownership and the REMOVE direction (T5), the
  `sync` capture (T6), the hibernation abort and the label-declaration fix (T7),
  both matrices plus the sample config (T8), the wiki and the gates (T9). Mode B
  (LUKS swap that hibernates) needs no code and is verified in the shared VM pass
  at the end of block C.
* **Deliberately not here.** Hoisting the crypttab composition out of
  `DracutBackend` into a shared writer. It would be the tidier structure, but it
  is a refactor of the boot path — the most dangerous code in the repo — for no
  behaviour the plan needs. Task 5 sidesteps it with a single ownership flag.
* **Watch out for.** Task 3 changes what `_format_partition` does for a
  filesystem the existing suite already covers; run the whole `-k disk` selection,
  not just the new test. And `_process_disk` formats every partition
  unconditionally (that was the #147 fix), so the ext2 label filesystem is created
  on every fresh install — which is correct, and is also why `plan()` in Task 5
  must key off the crypttab/fstab lines rather than off the partition itself.
