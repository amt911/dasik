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
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import CommandExecutionError
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

    def _declared(self) -> List[Tuple[Dict[str, Any], str, str]]:
        """``(partition, luks_name, kind)`` for every declared token unlock."""
        out: List[Tuple[Dict[str, Any], str, str]] = []
        disks = self._cfg.get("disks", {})
        if not isinstance(disks, dict):
            return out
        for disk in disks.get("disks", []):
            for part in disk.get("partitions", []):
                if not part.get("encrypt"):
                    continue
                name = part.get("luks_name", "cryptroot")
                for kind, flag, _enrol, _token in _KINDS:
                    if part.get(flag):
                        out.append((part, name, kind))
        return out

    @staticmethod
    def _item(luks_name: str, kind: str) -> str:
        return f"{luks_name}:{kind}"

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
            for kind in self._enrolled(self._dump(device)):
                out.add(self._item(luks_name, kind))
        return out

    # --- v3 contract -------------------------------------------------------- #

    def plan(self, managed) -> List[Change]:
        changes: List[Change] = []
        desired: List[str] = []
        dumps: Dict[str, str] = {}

        for part, luks_name, kind in self._declared():
            item = self._item(luks_name, kind)
            desired.append(item)
            device = self._luks_device(luks_name)
            if not device:
                changes.append(Change(
                    self._DOMAIN, Op.INSTALL, item,
                    reason=f"LUKS volume {luks_name!r} is not open, so the header "
                           f"cannot be read — the token may not be enrolled"))
                continue
            dump = dumps.setdefault(device, self._dump(device))
            if kind in self._enrolled(dump):
                continue
            if part.get("luks_password") is None:
                changes.append(Change(
                    self._DOMAIN, Op.INSTALL, item,
                    reason="not enrolled, and no luks_password to authorise it "
                           "with (sync never captures the passphrase — declare "
                           "it for this apply, or enrol by hand)"))
                continue
            changes.append(Change(self._DOMAIN, Op.INSTALL, item,
                                  reason="not enrolled in the LUKS header"))

        # Declared off but previously owned: wipe the keyslot — unless doing so
        # would leave nothing that opens the volume.
        for item in managed or []:
            if item in desired:
                continue
            luks_name, _, kind = item.partition(":")
            if kind not in {k for k, *_ in _KINDS}:
                continue
            device = self._luks_device(luks_name)
            if not device:
                continue
            dump = dumps.setdefault(device, self._dump(device))
            if kind not in self._enrolled(dump):
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
        return {self._DOMAIN: [self._item(name, kind)
                               for _part, name, kind in self._declared()]}

    def apply(self, changes) -> None:
        for change in changes:
            luks_name, _, kind = change.item.partition(":")
            device = self._luks_device(luks_name)
            if not device:
                raise CommandExecutionError(
                    f"Cannot change the {kind} keyslot: no open LUKS mapping named "
                    f"{luks_name!r}, so the volume is unknown.")
            if change.op is Op.INSTALL:
                self._enrol(device, luks_name, kind)
            elif change.op is Op.REMOVE:
                # check=True: a wipe that failed but reported success would
                # leave a token the config says is gone.
                Command.execute("systemd-cryptenroll", [f"--wipe-slot={kind}", device],
                                check=True)

    def _enrol(self, device: str, luks_name: str, kind: str) -> None:
        password = self._password_for(luks_name)
        if password is None:
            raise CommandExecutionError(
                f"Cannot enrol {kind} on {luks_name!r}: systemd-cryptenroll needs "
                f"an existing passphrase to authorise the new keyslot, and no "
                f"luks_password is declared. Add it to the partition for this "
                f"apply, or run `systemd-cryptenroll {_flag_for(kind)} {device}` "
                f"yourself. (Without it the command would sit waiting for input.)")
        # check=True: every way this fails is a way the machine ends up with the
        # option on its kernel command line and no token in the header — the key
        # not plugged in, never touched, needing a PIN, no TPM in the box.
        Command.execute("systemd-cryptenroll", [_flag_for(kind), device],
                        env={"PASSWORD": password}, check=True)

    def _password_for(self, luks_name: str) -> Optional[str]:
        for part, name, _kind in self._declared():
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

    # --- legacy executor path ------------------------------------------- #

    def is_needed(self) -> bool:
        return bool(self.plan(managed=[]))

    def execute(self) -> None:
        self.apply(self.plan(managed=[]))


def _flag_for(kind: str) -> str:
    for name, _flag, enrol, _token in _KINDS:
        if name == kind:
            return enrol
    return f"--{kind}-device=auto"
