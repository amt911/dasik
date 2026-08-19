"""Action: hardware-token LUKS unlock (v3 domain "luks_token").

TPM2 and FIDO2 keyslots, owned as domain items instead of being a side effect of
formatting a disk. ``DiskPartitionAction._process_disk`` enrolls right after
``luksFormat``, and that path is only reached when ``plan()`` decides a disk
needs INSTALL — a fresh or wiped one. On an already-installed machine that meant:

* adding ``unlock_fido2: true`` did **nothing** but put ``fido2-device=auto`` on
  the kernel command line, pointing at a token nobody had enrolled;
* a failed enrolment was never retried, and the documented fix was "wipe the
  disk and install again";
* dropping the flag removed the parameter and left the keyslot in the header.

The shape is :class:`LuksKeyfileAction`'s, for the same reason: the state of the
volume decides, so a converged system plans nothing and an installed one can
still gain a token.

**REMOVE is guarded.** Wiping the last keyslot that opens a volume is how a disk
is lost, so a removal that would leave no passphrase behind is refused out loud
and the keyslot stays. That is deliberately the opposite trade-off from a silent
success: an un-wiped token is recoverable, an unopenable disk is not.

**FIDO2 is a count, TPM2 is a flag.** A machine has one TPM; a person has as
many keys as they have. ``unlock_fido2: 2`` is two keyslots, carried as two
domain items (``cryptroot:fido2``, ``cryptroot:fido2#2`` — the first keeps its
old name so older manifests still own it). The header answers "how many
``systemd-fido2`` tokens", never "which key": systemd stores a credential per
enrolment, not a label, so a list of names would promise an identity no probe
could confirm and no ``sync`` could read back.

Two consequences shape the code below:

* ``systemd-cryptenroll --fido2-device=auto`` needs EXACTLY ONE key plugged in,
  so keys are enrolled one at a time with the human asked in between — and able
  to answer "skip", because declaring three keys and owning two must cost a
  keystroke, not the install. A skip records NOTHING, so the next ``plan`` asks
  again. Nobody is asked when there is no terminal; ``luks_token_policy`` decides
  there instead.
* ``--wipe-slot=fido2`` wipes EVERY fido2 keyslot, so a removal names the
  keyslot NUMBER — otherwise going from three keys to two would take all three.
"""
from __future__ import annotations

import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import CommandExecutionError
from ..models.disk_model import fido2_count
from ..state.change import Change, Op

# kind -> (config flag, the systemd-cryptenroll enrol flag, its token name in
# `cryptsetup luksDump`). The order is the order they are planned in.
_KINDS: Tuple[Tuple[str, str, str, str], ...] = (
    ("tpm2", "unlock_tpm2", "--tpm2-device=auto", "systemd-tpm2"),
    ("fido2", "unlock_fido2", "--fido2-device=auto", "systemd-fido2"),
)


class LuksTokenAction(AbstractAction):
    """Enrol (and, carefully, remove) the hardware tokens a config declares."""

    _DOMAIN = "luks_token"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        self._cfg: Dict[str, Any] = config if isinstance(config, dict) else {}

    @classmethod
    def empty_config(cls):
        """Root-level action: bootstrap from an empty mapping, not a list."""
        return {}

    @property
    def name(self) -> str:
        return "LUKS Hardware Tokens"

    @property
    def is_optional(self) -> bool:
        return True

    # NOTE: none of the cryptsetup/systemd-cryptenroll calls below take a
    # target. They run on the HOST, exactly as LuksKeyfileAction's do: the
    # device is a host path (/dev/…) and the mapping is a host mapping, whether
    # this is an install driving /mnt or a day-2 run against /.

    # --- the declarations ------------------------------------------------- #

    def _declared(self) -> List[Tuple[Dict[str, Any], str, str, int]]:
        """``(partition, luks_name, kind, count)`` per declared token unlock.

        The count is what makes several FIDO2 keys expressible: one keyslot per
        physical key. TPM2 is one machine, so it is one slot and no more.
        """
        out: List[Tuple[Dict[str, Any], str, str, int]] = []
        disks = self._cfg.get("disks", {})
        if not isinstance(disks, dict):
            return out
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if not part.get("encrypt"):
                    continue
                name = part.get("luks_name", "cryptroot")
                for kind, flag, _enrol, _token in _KINDS:
                    count = (fido2_count(part) if kind == "fido2"
                             else (1 if part.get(flag) else 0))
                    if count:
                        out.append((part, name, kind, count))
        return out

    @staticmethod
    def _item(luks_name: str, kind: str, index: int = 1) -> str:
        """``cryptroot:fido2`` for the first keyslot, ``…:fido2#2`` for the next.

        The first keeps the name it had while this was a boolean, so a manifest
        written back then still owns its keyslot instead of silently losing it.
        """
        suffix = "" if index <= 1 else f"#{index}"
        return f"{luks_name}:{kind}{suffix}"

    # --- probes ------------------------------------------------------------ #

    def _luks_device(self, luks_name: str) -> Optional[str]:
        """The block device behind an open mapping, from ``cryptsetup status``."""
        try:
            result = Command.execute("cryptsetup", ["status", luks_name])
        except Exception:            # noqa: BLE001 - not open / no cryptsetup
            return None
        stdout = getattr(result, "stdout", b"") or b""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        for line in stdout.splitlines():
            if "device:" in line:
                return line.split("device:")[1].strip()
        return None

    def _dump(self, device: str) -> str:
        """``cryptsetup luksDump`` for *device*, or '' when it cannot be read."""
        try:
            result = Command.execute("cryptsetup", ["luksDump", device])
        except Exception:            # noqa: BLE001 - no cryptsetup / not LUKS
            return ""
        stdout = getattr(result, "stdout", b"") or b""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        return stdout

    @staticmethod
    def _enrolled(dump: str) -> set:
        """Which token kinds the header carries."""
        return {kind for kind, _flag, _enrol, token in _KINDS if token in dump}

    @staticmethod
    def _enrolled_count(dump: str, kind: str) -> int:
        """HOW MANY tokens of *kind* the header carries.

        The whole widening from a boolean rests here: two keys are two
        ``systemd-fido2`` tokens, and counting them is the only question the
        header can answer — it stores a credential per key, never a name.
        """
        token = next(t for k, _f, _e, t in _KINDS if k == kind)
        return sum(1 for line in dump.splitlines()
                   if line.strip().endswith(token))

    @staticmethod
    def _slots_of(dump: str, kind: str) -> List[int]:
        """The keyslots bound to *kind*'s tokens, ascending.

        ``--wipe-slot=fido2`` takes EVERY fido2 keyslot, so going from three
        keys to two has to name a number instead.
        """
        token = next(t for k, _f, _e, t in _KINDS if k == kind)
        slots: List[int] = []
        section = ""
        current_is_kind = False
        for raw in dump.splitlines():
            if raw and not raw[0].isspace():
                section = raw.strip().rstrip(":").lower()
                continue
            if section != "tokens":
                continue
            line = raw.strip()
            if re.match(r"^\d+:\s", line):
                current_is_kind = line.endswith(token)
                continue
            m = re.match(r"^Keyslot:\s+(\d+)", line)
            if m and current_is_kind:
                slots.append(int(m.group(1)))
        return sorted(slots)

    @staticmethod
    def _slots(dump: str) -> Tuple[set, set]:
        """``(every keyslot, the keyslots a token is bound to)``.

        The difference is what a human can type — and the reason the removal
        guard can be answered from the header alone.
        """
        all_slots: set = set()
        token_slots: set = set()
        section = ""
        for raw in dump.splitlines():
            if raw and not raw[0].isspace():
                section = raw.strip().rstrip(":").lower()
                continue
            line = raw.strip()
            if section == "keyslots":
                m = re.match(r"^(\d+):\s", line)
                if m:
                    all_slots.add(int(m.group(1)))
            elif section == "tokens":
                m = re.match(r"^Keyslot:\s+(\d+)", line)
                if m:
                    token_slots.add(int(m.group(1)))
        return all_slots, token_slots

    def _passphrase_would_remain(self, dump: str) -> bool:
        """Is there a keyslot no token owns — i.e. one somebody can type into?"""
        all_slots, token_slots = self._slots(dump)
        return bool(all_slots - token_slots)

    def _encrypted_volumes(self) -> List[str]:
        """Every encrypted volume the config names, flags or no flags.

        Not ``_declared()``: the drop case is exactly the one where the flag is
        gone and the token is still in the header.
        """
        names: List[str] = []
        disks = self._cfg.get("disks", {})
        if not isinstance(disks, dict):
            return names
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if part.get("encrypt"):
                    name = part.get("luks_name", "cryptroot")
                    if name not in names:
                        names.append(name)
        return names

    def actual(self) -> set:
        """The tokens the machine's headers really carry.

        Not decoration: ``Reconciler._owned_after_sync`` records
        ``actual & (claimable | declared)``, so an action that leaves the base's
        empty set here is disowned by **every** sync — after which dropping the
        flag plans nothing and the keyslot stays for ever. Found on a VM:
        enrol day-2 (worked), sync, drop the flag, plan mute.
        """
        out: set = set()
        for luks_name in self._encrypted_volumes():
            device = self._luks_device(luks_name)
            if not device:
                continue
            dump = self._dump(device)
            for kind, _flag, _enrol, _token in _KINDS:
                for index in range(1, self._enrolled_count(dump, kind) + 1):
                    out.add(self._item(luks_name, kind, index))
        return out

    # --- v3 contract -------------------------------------------------------- #

    def plan(self, managed) -> List[Change]:
        changes: List[Change] = []
        desired: List[str] = []
        dumps: Dict[str, str] = {}

        for part, luks_name, kind, count in self._declared():
            device = self._luks_device(luks_name)
            dump = None
            if device:
                dump = dumps.setdefault(device, self._dump(device))
            enrolled = self._enrolled_count(dump, kind) if dump is not None else 0
            for index in range(1, count + 1):
                item = self._item(luks_name, kind, index)
                desired.append(item)
                if device is None:
                    changes.append(Change(
                        self._DOMAIN, Op.INSTALL, item,
                        reason=f"LUKS volume {luks_name!r} is not open, so the header "
                               f"cannot be read — the token may not be enrolled"))
                    continue
                if index <= enrolled:
                    continue        # that many are already in the header
                if part.get("luks_password") is None:
                    changes.append(Change(
                        self._DOMAIN, Op.INSTALL, item,
                        reason="not enrolled, and no luks_password to authorise it "
                               "with (sync never captures the passphrase — declare "
                               "it for this apply, or enrol by hand)"))
                    continue
                changes.append(Change(self._DOMAIN, Op.INSTALL, item,
                                      reason="not enrolled in the LUKS header"))

        # Declared off (or fewer than before) but previously owned: wipe the
        # keyslot — unless doing so would leave nothing that opens the volume.
        for item in managed or []:
            if item in desired:
                continue
            luks_name, _, kind_part = item.partition(":")
            kind = kind_part.split("#")[0]
            if kind not in {k for k, *_ in _KINDS}:
                continue
            device = self._luks_device(luks_name)
            if not device:
                continue
            dump = dumps.setdefault(device, self._dump(device))
            # Which slot number this item stands for is not recorded anywhere —
            # the header has no names — so the check is a count: an owned item
            # beyond what the header still carries is already gone.
            index = int(kind_part.split("#")[1]) if "#" in kind_part else 1
            if index > self._enrolled_count(dump, kind):
                continue
            if not self._passphrase_would_remain(dump):
                print(f"NOTE: keeping the {kind} keyslot on {luks_name}: it is the "
                      f"only keyslot in the header, and wiping it would leave the "
                      f"volume with no passphrase and no way in. Add one with "
                      f"`cryptsetup luksAddKey` first.")
                continue
            changes.append(Change(
                self._DOMAIN, Op.REMOVE, item,
                reason="no longer declared — the keyslot is wiped "
                       "(a passphrase keyslot remains)",
                destructive=True))
        return changes

    def managed_keys(self) -> dict:
        return {self._DOMAIN: [self._item(name, kind, index)
                               for _part, name, kind, count in self._declared()
                               for index in range(1, count + 1)]}

    def apply(self, changes) -> None:
        # A skip is per volume+kind: saying "I do not have a third key" must not
        # be asked again for the fourth, and must not touch the other volume.
        skipped: set = set()
        # Slots already wiped in THIS apply. `cryptsetup luksDump` is re-read per
        # removal, but a cached or slow-to-refresh read would otherwise hand the
        # same highest slot twice and leave the other one in the header.
        wiped: set = set()
        for change in changes:
            luks_name, _, kind_part = change.item.partition(":")
            kind = kind_part.split("#")[0]
            index = int(kind_part.split("#")[1]) if "#" in kind_part else 1
            device = self._luks_device(luks_name)
            if not device:
                raise CommandExecutionError(
                    f"Cannot change the {kind} keyslot: no open LUKS mapping named "
                    f"{luks_name!r}, so the volume is unknown.")
            if change.op is Op.INSTALL:
                if (luks_name, kind) in skipped:
                    continue
                if not self._enrol(device, luks_name, kind, index):
                    skipped.add((luks_name, kind))
            elif change.op is Op.REMOVE:
                self._wipe(device, kind, wiped)

    def _wipe(self, device: str, kind: str, wiped: set) -> None:
        """Wipe ONE keyslot of *kind*, the highest-numbered one still standing.

        Never ``--wipe-slot=<kind>``: that takes every keyslot of that kind, so
        going from three keys to two would take all three. Highest first, and
        *wiped* keeps N removals from naming the same slot N times.
        """
        slots = [s for s in self._slots_of(self._dump(device), kind)
                 if (device, s) not in wiped]
        if slots:
            flag = f"--wipe-slot={slots[-1]}"
            wiped.add((device, slots[-1]))
        else:
            flag = f"--wipe-slot={kind}"
        # check=True: a wipe that failed but reported success would leave a
        # token the config says is gone.
        Command.execute("systemd-cryptenroll", [flag, device], check=True)

    # --- asking the human -------------------------------------------------- #

    def _interactive(self) -> bool:
        """Is there a human to ask?

        Two conditions, and the second was learnt in a VM: a terminal has to
        exist, AND the run must not be ``--yes``. The guest installer runs on a
        serial console, so stdin passes `isatty` — and `dasik apply --yes` sat
        for ever at "plug in FIDO2 key 1 of 2" with nobody there. `--yes` is
        precisely the promise that nobody is; the policy decides instead.
        """
        if getattr(self.context, "assume_yes", False):
            return False
        try:
            return bool(sys.stdin) and sys.stdin.isatty()
        except (AttributeError, ValueError):     # closed/replaced stdin
            return False

    def _ask(self, prompt: str) -> str:
        try:
            return input(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            # The terminal went away mid-question: treat it as "skip", which is
            # the answer that changes nothing on the disk.
            print()
            return "s"

    def _policy(self) -> str:
        policy = self._cfg.get("luks_token_policy", {})
        if not isinstance(policy, dict):
            return "abort"
        return str(policy.get("enroll_failure", "abort"))

    def _enrol(self, device: str, luks_name: str, kind: str, index: int) -> bool:
        """Enrol one keyslot. Returns False when the human chose to skip it.

        A skip records NOTHING: the keyslot stays out of the header, so the next
        ``plan`` shows it exactly as it did before. That is the whole point —
        somebody who declared three keys and can only find two gets an install
        that finishes and a plan that still tells the truth.
        """
        total = self._count_for(luks_name, kind)
        if total > 1 and self._interactive():
            answer = self._ask(
                f"\nFIDO2 key {index} of {total} for {luks_name}: plug it in "
                f"(and unplug the others — systemd-cryptenroll needs exactly one), "
                f"then press Enter. [s = skip the remaining keys] ")
            if answer.startswith("s"):
                print(f"NOTE: skipping FIDO2 key {index} of {total} on {luks_name}. "
                      f"The keyslot is NOT enrolled and is not recorded as such — "
                      f"`dasik plan` will keep asking for it.")
                return False

        password = self._password_for(luks_name)
        if password is None:
            raise CommandExecutionError(
                f"Cannot enrol {kind} on {luks_name!r}: systemd-cryptenroll needs "
                f"an existing passphrase to authorise the new keyslot, and no "
                f"luks_password is declared. Add it to the partition for this "
                f"apply, or run `systemd-cryptenroll {_flag_for(kind)} {device}` "
                f"yourself. (Without it the command would sit waiting for input.)")

        while True:
            try:
                # check=True: every way this fails is a way the machine ends up
                # with the option on its kernel command line and no token in the
                # header — the key not plugged in, never touched, needing a PIN,
                # no TPM in the box.
                Command.execute("systemd-cryptenroll", [_flag_for(kind), device],
                                env={"PASSWORD": password}, check=True)
                return True
            except CommandExecutionError as failure:
                if self._interactive():
                    answer = self._ask(
                        f"\nEnrolling {kind} key {index} on {luks_name} failed: "
                        f"{failure}\n[r] retry  [s] skip this key and the rest  "
                        f"[a] abort the apply: ")
                    if answer.startswith("r"):
                        continue
                    if answer.startswith("s"):
                        print(f"NOTE: {kind} key {index} on {luks_name} was NOT "
                              f"enrolled; `dasik plan` will keep showing it.")
                        return False
                    raise
                if self._policy() == "warn-and-continue":
                    print(f"error: {kind} key {index} on {luks_name} was not "
                          f"enrolled: {failure}\nThe apply carries on; the keyslot "
                          f"stays out of the manifest, so the next apply retries it.")
                    return False
                raise

    def _count_for(self, luks_name: str, kind: str) -> int:
        for _part, name, declared_kind, count in self._declared():
            if name == luks_name and declared_kind == kind:
                return count
        return 1

    def _password_for(self, luks_name: str) -> Optional[str]:
        for part, name, _kind, _count in self._declared():
            if name == luks_name:
                password = part.get("luks_password")
                if password is not None:
                    return str(password)
        return None

    def import_state(self, managed=None) -> dict:
        """Nothing: the flags live inside the partition, and
        ``DiskPartitionAction.import_state`` already reads them out of the
        header. Capturing them here too would state the same fact twice."""
        return {}


def _flag_for(kind: str) -> str:
    for name, _flag, enrol, _token in _KINDS:
        if name == kind:
            return enrol
    return f"--{kind}-device=auto"
