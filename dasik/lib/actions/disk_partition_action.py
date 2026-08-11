"""Disk partitioning action."""
import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dasik.lib.actions.abstract_action import AbstractAction
from dasik.lib.models.disk_model import (
    DisksConfiguration,
    DiskLayout,
    Partition,
    FileSystemType,
    BtrfsSubvolume,
    _KEYDEV_FILESYSTEMS,
)
from dasik.lib.command_worker.command_worker import Command
from dasik.lib.exceptions.exceptions import CommandExecutionError
from dasik.lib.state.change import Change, Op

# lsblk FSTYPE -> dasik filesystem. Anything absent (ntfs, None, crypto_LUKS
# handled separately, …) is UNREPRESENTABLE and its partition is skipped during
# discovery — sync captures an inventory, not a lossy guess.
_DISCOVER_FS = {
    "ext4": "ext4", "btrfs": "btrfs", "xfs": "xfs",
    "vfat": "fat32", "fat32": "fat32", "swap": "swap",
}
_LABEL_OK = re.compile(r"[A-Za-z0-9_.-]{1,36}")
# Device-spec prefixes `rd.luks.key=<uuid>=/path:<spec>` may carry, used to tell
# a key device apart from a path that merely contains a colon.
_KEYDEV_SPEC_KINDS = {"UUID", "PARTUUID", "PARTLABEL", "LABEL"}
# The keyfile-timeout KernelCmdlineAction derives for a key-device unlock; sync
# must not capture it back as if the user had written it.
_KEYFILE_TIMEOUT = "keyfile-timeout=10s"


# Mountpoints whose permissions are NOT the mkdir default. `mkdir` yields 0755,
# so /mnt/var/tmp existed as 0755 before pacstrap and pacman warned "directory
# permissions differ on /mnt/var/tmp/ filesystem: 755 package: 1777". Both /tmp
# and /var/tmp must be world-writable + sticky from the moment they are created;
# relying on systemd-tmpfiles at first boot means the wrong mode is live for the
# whole install.
_MOUNTPOINT_MODES = {
    "/tmp": 0o1777,
    "/var/tmp": 0o1777,
}


def _mountpoint_mode(canonical: str) -> Optional[int]:
    """The mode a mountpoint must have, or None to keep the mkdir default."""
    return _MOUNTPOINT_MODES.get(canonical.rstrip("/") or "/")


def _make_mountpoint(host_path: str, canonical: "Optional[str]") -> None:
    """Create *host_path* (parents included) and enforce the canonical path's
    required mode, also when the directory already existed."""
    Path(host_path).mkdir(parents=True, exist_ok=True)
    mode = _mountpoint_mode(canonical) if canonical else None
    if mode is not None:
        os.chmod(host_path, mode)


class DiskPartitionAction(AbstractAction):
    """Action to handle disk partitioning declaratively (v3 domain "disks")."""

    _DOMAIN = "disks"

    @property
    def KEY_NAME(self) -> str:
        """Return the key name for this action."""
        return "disks"

    def __init__(self, config=None, context=None):
        """Initialize the disk partition action.

        Accepts the raw config dict (``{"disks": [...]}``), a
        ``DisksConfiguration`` model, or ``None`` (no disks → no-op).
        """
        super().__init__(config, context)
        self.disks: List[DiskLayout] = self._parse(config)
        self.partition_map: Dict[str, str] = {}  # Maps partition label to device path

    @staticmethod
    def _parse(config) -> "List[DiskLayout]":
        if config is None:
            return []
        if isinstance(config, DisksConfiguration):
            return list(config.disks)
        if isinstance(config, dict):
            raw = config.get("disks")
            if not raw:
                return []
            return [DiskLayout.model_validate(d) for d in raw]
        return []

    @property
    def name(self) -> str:
        return "Disk Partitioning"

    @property
    def is_optional(self) -> bool:
        return True

    @classmethod
    def empty_config(cls):
        return {}

    @property
    def can_incrementally_change(self) -> bool:
        """Disk partitioning cannot be done incrementally."""
        return False

    # --- v3 contract -------------------------------------------------- #

    def _device_labels(self, device: str) -> set:
        """Partition labels currently present on *device* (empty if none)."""
        try:
            result = Command.execute("lsblk", ["-no", "LABEL", device])
            out = result.stdout
            if isinstance(out, bytes):
                out = out.decode("utf-8", "replace")
            return {line.strip() for line in out.splitlines() if line.strip()}
        except Exception:
            return set()

    def _disk_converged(self, disk: DiskLayout) -> bool:
        want = {p.label for p in disk.partitions}
        return bool(want) and want.issubset(self._device_labels(disk.device))

    def actual(self) -> set:
        return {d.device for d in self.disks if self._disk_converged(d)}

    def managed_keys(self) -> dict:
        return {self._DOMAIN: sorted(self.actual())}

    def plan(self, managed) -> list:
        changes = []
        for disk in self.disks:
            if self._disk_converged(disk):
                continue
            if disk.wipe_disk or not self._has_partition_table(disk.device):
                # destructive=True: the op is INSTALL, but applying this runs
                # `wipefs --all` + `sgdisk --zap-all` + mkfs. Only this domain
                # knows that, and without saying so the change slipped past the
                # confirmation prompt that `pacman -R` has to pass.
                # The reason names what is being erased — a bare "wipe_disk" in
                # a y/N prompt tells the user nothing about which disk it is.
                existing = sorted(self._device_labels(disk.device))
                found = f" (holds: {', '.join(existing)})" if existing else ""
                changes.append(Change(
                    self._DOMAIN, Op.INSTALL, disk.device,
                    reason=(f"{'wipe_disk' if disk.wipe_disk else 'empty disk'} "
                            f"— ERASES {disk.device}{found}"),
                    destructive=True,
                ))
            else:
                print(
                    f"  Warning: {disk.device} is populated and does not match the "
                    f"declared layout; set wipe_disk:true to repartition. Skipping."
                )
        return changes

    def apply(self, changes) -> None:
        if not changes:
            return
        targets = {c.item for c in changes}
        for disk in self.disks:
            if disk.device in targets:
                self._process_disk(disk)

    def _target(self):
        return self.context.target if self.context is not None else None

    @staticmethod
    def _decode(out) -> str:
        return out.decode("utf-8", "replace") if isinstance(out, bytes) else (out or "")

    def _luks_backing_device(self, luks_name: str) -> "Optional[str]":
        """Backing device of the open mapping (via `cryptsetup status`), or None."""
        try:
            status = Command.execute("cryptsetup", ["status", luks_name], target=self._target())
            for line in self._decode(status.stdout).splitlines():
                if "device:" in line:
                    return line.split("device:")[1].strip()
        except Exception:
            return None
        return None

    def _read_luks_uuid(self, luks_name: str) -> "Optional[str]":
        """Real LUKS header UUID of the open mapping *luks_name*, or None.
        Best-effort: any failure (not open, cryptsetup missing) yields None."""
        dev = self._luks_backing_device(luks_name)
        if not dev:
            return None
        try:
            res = Command.execute("cryptsetup", ["luksUUID", dev], target=self._target())
            return self._decode(res.stdout).strip() or None
        except Exception:
            return None

    def _kernel_cmdline_text(self) -> str:
        """The target's kernel cmdline (from /proc/cmdline on the live host). Empty
        if unreadable — sync only recovers extra luks options on the running system."""
        target = self._target()
        path = target.path("/proc/cmdline") if target is not None else "/proc/cmdline"
        try:
            with open(path, "r") as f:
                return f.read()
        except Exception:
            return ""

    def _read_luks_options(self, uuid: str) -> "List[str]":
        """Extra rd.luks.options tokens for <uuid> beyond what dasik derives
        itself, so sync recovers luks_options from the live kernel cmdline.

        Derived, and therefore subtracted: fido2-device=auto / tpm2-device=auto,
        and — when this volume unlocks from a key DEVICE — the default
        keyfile-timeout dasik adds so a missing pendrive falls back to the
        passphrase. A different timeout is the user's and survives.
        """
        auto = {"fido2-device=auto", "tpm2-device=auto"}
        _keyfile, keydev = self._read_luks_keyfile(uuid)
        if keydev:
            auto.add(_KEYFILE_TIMEOUT)
        for tok in self._kernel_cmdline_text().split():
            if tok.startswith(f"rd.luks.options={uuid}="):
                opts = tok.split("=", 2)[2].split(",")
                return [o for o in opts if o and o not in auto]
        return []

    def _read_luks_keyfile(self, uuid: str) -> "Tuple[Optional[str], Optional[str]]":
        """``(keyfile path, key device spec)`` for <uuid> from the live cmdline.

        ``rd.luks.key=<uuid>=/path[:<device spec>]``. The device spec is split
        off the LAST colon and only when it looks like one, so a path containing
        a colon is not mangled into a device.
        """
        for tok in self._kernel_cmdline_text().split():
            if not tok.startswith(f"rd.luks.key={uuid}="):
                continue
            value = tok.split("=", 2)[2]
            head, sep, tail = value.rpartition(":")
            if sep and (tail.startswith("/dev/")
                        or tail.split("=")[0].upper() in _KEYDEV_SPEC_KINDS):
                return head, tail
            return value, None
        return None, None

    def _keydev_filesystem(self, spec: str) -> "Optional[str]":
        """Filesystem of the key device, so the captured config can put the
        right module in the initramfs. Best-effort: an unprobeable device still
        captures the unlock itself, just without this detail."""
        kind, sep, value = spec.partition("=")
        if not sep:
            return None
        flag = {"UUID": "-U", "LABEL": "-L"}.get(kind.upper())
        if not flag:
            return None
        try:
            result = Command.execute("lsblk", ["-no", "FSTYPE", flag, value],
                                     target=self._target())
        except Exception:            # noqa: BLE001 - probing is never fatal
            return None
        fstype = self._decode(getattr(result, "stdout", b"")).strip().splitlines()
        return fstype[0].strip() if fstype and fstype[0].strip() else None

    def _capture_unlock_keyfile(self, part: dict, uuid: str) -> None:
        """Write the keyfile unlock this volume boots with into *part*."""
        keyfile, keydev = self._read_luks_keyfile(uuid)
        if not keyfile:
            return
        part["unlock_keyfile"] = keyfile
        if not keydev:
            return
        part["unlock_keydev"] = keydev
        fstype = self._keydev_filesystem(keydev)
        if fstype in _KEYDEV_FILESYSTEMS:
            part["unlock_keydev_fs"] = fstype

    def _read_luks_tokens(self, luks_name: str) -> set:
        """Which hardware-token unlock methods are enrolled in the LUKS header:
        {"fido2", "tpm2"} from the `cryptsetup luksDump` Tokens section. Lets sync
        recover unlock_fido2/unlock_tpm2 from a live system. Best-effort."""
        dev = self._luks_backing_device(luks_name)
        if not dev:
            return set()
        try:
            dump = self._decode(
                Command.execute("cryptsetup", ["luksDump", dev], target=self._target()).stdout)
        except Exception:
            return set()
        tokens = set()
        if "systemd-fido2" in dump:
            tokens.add("fido2")
        if "systemd-tpm2" in dump:
            tokens.add("tpm2")
        return tokens

    def import_state(self, managed=None) -> dict:
        """Reflect the declared disk layout back into the config non-destructively.

        Like nixos-generate-config's hardware-configuration.nix: the captured
        stanza forces ``format``/``wipe_disk`` OFF (so a synced config can NEVER
        reformat on re-apply), bakes in the real LUKS header UUID (the unlock fact,
        read from the running mapping), and drops the plaintext ``luks_password``
        (a secret is never persisted by sync). No disks declared -> discover the
        live layout from scratch (``_discover_disks``), best-effort and equally
        non-destructive.
        """
        if not self.disks:
            discovered = self._discover_disks()
            return {"disks": {"disks": discovered}} if discovered else {}
        disks_out = []
        for disk in self.disks:
            d = disk.model_dump(mode="json")
            d["wipe_disk"] = False
            for p in d.get("partitions", []):
                p["format"] = False
                p.pop("luks_password", None)
                if p.get("encrypt") and p.get("luks_name"):
                    uuid = self._read_luks_uuid(p["luks_name"])
                    if uuid:
                        p["luks_uuid"] = uuid
                    tokens = self._read_luks_tokens(p["luks_name"])
                    if "fido2" in tokens:
                        p["unlock_fido2"] = True
                    if "tpm2" in tokens:
                        p["unlock_tpm2"] = True
                    if uuid:
                        extra = self._read_luks_options(uuid)
                        if extra:
                            p["luks_options"] = extra
                        self._capture_unlock_keyfile(p, uuid)
            disks_out.append(d)
        return {"disks": {"disks": disks_out}}

    # --- live layout discovery (sync from an empty seed) -------------- #

    @staticmethod
    def _map_fs(fstype: "Optional[str]") -> "Optional[str]":
        """dasik filesystem for an lsblk FSTYPE, or None if unrepresentable."""
        return _DISCOVER_FS.get((fstype or "").strip().lower())

    @staticmethod
    def _map_ptype(parttypename: "Optional[str]") -> str:
        """Map a GPT partition-type name to dasik's coarse partition_type."""
        name = (parttypename or "").lower()
        if "efi system" in name:
            return "esp"
        if "swap" in name:
            return "linux-swap"
        return "linux"

    @staticmethod
    def _hoist_common_mount_options(subs: "List[dict]") -> "List[str]":
        """Options present on EVERY subvolume are moved up to the partition and
        removed from each subvolume (mutates *subs*). Returns the hoisted options."""
        if not subs:
            return []
        common = set(subs[0].get("mount_options", []))
        for s in subs[1:]:
            common &= set(s.get("mount_options", []))
        if not common:
            return []
        for s in subs:
            s["mount_options"] = [o for o in s.get("mount_options", []) if o not in common]
        return sorted(common)

    @staticmethod
    def _subvol_mount_options(partition, subvol) -> "List[str]":
        """Effective mount options for a subvolume: the partition's mount_options
        as a base (so a hoisted `compress-force=…` applies), plus the subvolume's
        own (de-duplicated), plus `subvol=<name>`."""
        merged = list(partition.mount_options)
        for o in subvol.mount_options:
            if o not in merged:
                merged.append(o)
        merged.append(f"subvol={subvol.name}")
        return merged

    @staticmethod
    def _bytes_to_size(n: int) -> str:
        """Render a byte count as a model-valid size string (whole MiB)."""
        return f"{max(1, round(n / (1024 * 1024)))}MiB"

    @staticmethod
    def _role_label(ptype: str, mountpoints: "List[Optional[str]]",
                    fs: "Optional[str]") -> str:
        """A portable, role-based label for a partition that has no filesystem
        label of its own: root / <mount basename> / esp / swap. Used so a captured
        layout carries meaningful, disk-independent labels instead of the source
        device name (e.g. 'vda5')."""
        mps = [m for m in mountpoints if m]
        if "/" in mps:
            return "root"
        for m in mps:
            base = re.sub(r"[^A-Za-z0-9_.-]", "", m.rstrip("/").split("/")[-1])
            if base:
                return base            # /boot -> boot, /home -> home, /srv -> srv
        if ptype == "esp":
            return "esp"
        if fs == "swap":
            return "swap"
        return "part"

    @staticmethod
    def _safe_label(candidates: "List[Optional[str]]", fallback: str,
                    used: set) -> str:
        """First candidate that is a valid, unused label; else the sanitized
        device name, de-duplicated with a numeric suffix. Labels must be unique
        per disk and match the model's charset — real disks have blank or
        space-bearing labels, so discovery always synthesizes a safe one."""
        for c in candidates:
            if c and _LABEL_OK.fullmatch(c) and c not in used:
                return c
        base = re.sub(r"[^A-Za-z0-9_.-]", "", fallback)[:36] or "part"
        label, i = base, 1
        while label in used:
            suffix = f"_{i}"
            label = base[: 36 - len(suffix)] + suffix
            i += 1
        return label

    def _lsblk_tree(self) -> list:
        """Top-level block devices as lsblk's JSON tree (best-effort, [] on error)."""
        try:
            res = Command.execute(
                "lsblk",
                ["-J", "-b", "-o",
                 "NAME,PATH,TYPE,FSTYPE,LABEL,PARTLABEL,PARTTYPENAME,SIZE,MOUNTPOINT,PTTYPE"],
                target=self._target())
            data = json.loads(self._decode(res.stdout) or "{}")
            return data.get("blockdevices", []) or []
        except Exception:
            return []

    def _findmnt_btrfs_rows(self) -> "List[tuple]":
        """(target, source, options) for every mounted btrfs (for subvolumes)."""
        try:
            res = Command.execute(
                "findmnt", ["-rn", "-t", "btrfs", "-o", "TARGET,SOURCE,OPTIONS"],
                target=self._target())
        except Exception:
            return []
        rows = []
        for line in self._decode(res.stdout).splitlines():
            cols = line.split(None, 2)
            if len(cols) == 3:
                rows.append((cols[0], cols[1], cols[2]))
        return rows

    def _btrfs_subvols(self, node: dict) -> "List[dict]":
        """Mounted subvolumes of the btrfs on *node*, from findmnt. Best-effort;
        maps each `subvol=/@x` mount to a BtrfsSubvolume {name, mountpoint}."""
        source = node.get("path")
        if not source:
            return []
        subs, seen = [], set()
        for target, src, opts in self._findmnt_btrfs_rows():
            if src.split("[")[0] != source:      # findmnt shows /dev/x[/@sub]
                continue
            subvol = next((o.split("=", 1)[1] for o in opts.split(",")
                           if o.startswith("subvol=")), None)
            if not subvol:
                continue
            name = subvol.rstrip("/").split("/")[-1]
            if not name or name in seen:
                continue
            seen.add(name)
            mopts = [o for o in opts.split(",") if o.startswith("compress")]
            subs.append({"name": name, "mountpoint": target,
                         "mount_options": mopts or ["compress-force=zstd"]})
        return subs

    def _partition_from_node(self, node: dict, used: set) -> "Optional[dict]":
        """Build a partition dict from an lsblk `part` node, or None to skip it
        (unrepresentable filesystem / closed LUKS)."""
        fstype = node.get("fstype")
        encrypt, luks_name, luks_uuid = False, None, None
        inner = node
        if (fstype or "").lower() == "crypto_luks":
            kids = [c for c in node.get("children", []) if c.get("type") == "crypt"]
            if not kids:
                return None                      # locked: inner fs unknown -> skip
            inner = kids[0]
            luks_name = inner.get("name")
            fs = self._map_fs(inner.get("fstype"))
            if not fs or not luks_name:
                return None
            encrypt = True
            luks_uuid = self._read_luks_uuid(luks_name)
        else:
            fs = self._map_fs(fstype)
            if not fs:
                return None
        ptype = self._map_ptype(node.get("parttypename"))
        subs = self._btrfs_subvols(inner) if fs == "btrfs" else []
        # A btrfs with subvolumes mounts the PARTITION at the root ("/") subvol's
        # mountpoint — the convention _mount_partitions needs to include it in the
        # mount pass — and the subvolumes carry the rest. Take it from the subvols
        # (not lsblk, which leaks one arbitrary subvol mount like /var/tmp up to the
        # partition). Without subvolumes, use the fs mountpoint.
        if subs:
            mnt = next((s["mountpoint"] for s in subs if s["mountpoint"] == "/"), None)
        else:
            mnt = inner.get("mountpoint") or node.get("mountpoint")
        # Synthesized labels are ROLE-based (root/boot/home/esp/swap), not the
        # source device name — a captured layout is portable to a differently-named
        # disk, and the number in "vda5" was meaningless on the target.
        mount_hints = [mnt] + [s["mountpoint"] for s in subs]
        role = self._role_label(ptype, mount_hints, fs)
        label = self._safe_label(
            [node.get("partlabel"), inner.get("label"), node.get("label")],
            role, used)
        used.add(label)
        part: Dict = {
            "label": label,
            "size": self._bytes_to_size(int(node.get("size") or 0)),
            "filesystem": fs,
            "partition_type": ptype,
            "format": False,
        }
        if subs:
            # DRY: a mount option shared by ALL subvolumes (e.g. compress-force=zstd)
            # is hoisted to the partition once; the mount pass re-applies it to each
            # subvolume mount. Avoids repeating it on every subvolume.
            common = self._hoist_common_mount_options(subs)
            if common:
                part["mount_options"] = common
            part["btrfs_subvolumes"] = subs
        if mnt:
            part["mountpoint"] = mnt
        if encrypt and luks_name:
            part["encrypt"] = True
            part["luks_name"] = luks_name
            if luks_uuid:
                part["luks_uuid"] = luks_uuid
            tokens = self._read_luks_tokens(luks_name)
            if "fido2" in tokens:
                part["unlock_fido2"] = True
            if "tpm2" in tokens:
                part["unlock_tpm2"] = True
            if luks_uuid:
                extra = self._read_luks_options(luks_uuid)
                if extra:
                    part["luks_options"] = extra
                self._capture_unlock_keyfile(part, luks_uuid)
        return part

    def _discover_disks(self) -> "List[dict]":
        """Discover the live disk layout as an inventory of dasik disk stanzas.

        Non-destructive by construction: every disk comes back with
        ``wipe_disk: false`` and every partition with ``format: false``, so a
        synced config can never repartition. Partitions whose filesystem dasik
        cannot represent (ntfs, unformatted, locked LUKS) are skipped; a disk
        with no representable partitions is omitted. Each disk is validated
        through the model and dropped if it does not (never emit an invalid disk).
        """
        out: List[dict] = []
        for dev in self._lsblk_tree():
            if dev.get("type") != "disk":
                continue
            used: set = set()
            parts = []
            for child in dev.get("children", []) or []:
                if child.get("type") != "part":
                    continue
                p = self._partition_from_node(child, used)
                if p:
                    parts.append(p)
            if not parts:
                continue
            disk = {
                "device": dev.get("path") or f"/dev/{dev.get('name')}",
                "partition_table": "msdos" if dev.get("pttype") == "dos" else "gpt",
                "wipe_disk": False,
                "partitions": parts,
            }
            # Deliberate skip: never emit a disk that won't validate.
            try:
                DiskLayout.model_validate(disk)
            except Exception:  # nosec B112
                continue
            out.append(disk)
        return out

    # --- legacy executor bridge --------------------------------------- #

    def is_needed(self) -> bool:
        return bool(self.plan(managed=[]))

    def execute(self) -> None:
        self.apply(self.plan(managed=[]))

    def _before_check(self) -> bool:
        """Check if disk partitioning needs to be done.
        
        Returns:
            True if disks are configured
        """
        return len(self.disks) > 0

    def after_check(self) -> None:
        """Post-action checks."""
        # Verify all partitions were created
        print("\nVerifying created partitions...")
        for label, device in self.partition_map.items():
            if Path(device).exists():
                print(f"  ✓ {label}: {device}")
            else:
                print(f"  ✗ {label}: {device} (NOT FOUND)")

    def do_action(self) -> None:
        """Execute the disk partitioning action (compatibility method)."""
        if self._before_check():
            self.run()
            self.after_check()
        else:
            print("No disks configured, skipping partitioning.")

    def run(self) -> None:
        """Execute the disk partitioning process."""
        print("Starting disk partitioning process...")

        for disk in self.disks:
            print(f"\nProcessing disk: {disk.device}")
            self._process_disk(disk)

        print("\nDisk partitioning completed successfully!")

    @staticmethod
    def _size_to_mib(size: str) -> float:
        """Parse a partition size string (e.g. '512MiB', '4GiB') to MiB."""
        s = size.strip()
        # binary (GiB/MiB) and decimal (GB/MB) suffixes, normalised to MiB
        for suffix, factor in (("GiB", 1024.0), ("MiB", 1.0),
                               ("GB", 1e9 / 2 ** 20), ("MB", 1e6 / 2 ** 20)):
            if s.endswith(suffix):
                return float(s[: -len(suffix)]) * factor
        # bare number → assume MiB
        return float(s)

    def _validate_sizes(self, disk: DiskLayout) -> None:
        """Abort BEFORE wiping if the fixed-size partitions exceed the disk.

        A layout larger than the device used to fail late (parted clamps/errors,
        swallowed) and only surfaced as a confusing "no space" after other
        destructive steps ran. 'rest' / percentage partitions are skipped — they
        fill whatever is left.
        """
        disk_mib = self._get_disk_size_mib(disk.device)
        fixed = 0.0
        for p in disk.partitions:
            s = p.size.strip().lower()
            if s == "rest" or s.endswith("%"):
                continue
            fixed += self._size_to_mib(p.size)
        if fixed + 1 > disk_mib:   # +1 MiB for the GPT/alignment start offset
            raise CommandExecutionError(
                f"declared partitions need ~{fixed:.0f} MiB but {disk.device} is "
                f"only ~{disk_mib:.0f} MiB — shrink the sizes or use a bigger disk."
            )

    def _process_disk(self, disk: DiskLayout) -> None:
        """Process a single disk layout.

        Args:
            disk: Disk layout configuration
        """
        # Check if device exists
        if not Path(disk.device).exists():
            raise FileNotFoundError(f"Device {disk.device} does not exist")
        
        # Show current partition layout
        self._show_current_layout(disk.device)

        # Fail loudly BEFORE any destructive op if the layout can't fit the disk
        self._validate_sizes(disk)

        # Wipe disk if requested
        if disk.wipe_disk:
            self._wipe_disk(disk.device)
            # Create partition table after wiping
            self._create_partition_table(disk.device, disk.partition_table.value)
        else:
            # Only create partition table if it doesn't exist
            if not self._has_partition_table(disk.device):
                print(f"No partition table found on {disk.device}, creating one...")
                self._create_partition_table(disk.device, disk.partition_table.value)
            else:
                print(f"Using existing partition table on {disk.device}")
        
        # Create partitions
        self._create_partitions(disk)
        
        # Refresh partition table
        self._refresh_partition_table(disk.device)
        
        # Format partitions. Reaching _process_disk means plan() decided to
        # (re)partition this disk — which only happens on a fresh/empty or wiped
        # disk (a populated disk without wipe_disk is refused by plan()). So every
        # partition here was just created EMPTY and MUST be formatted; the
        # `format` flag is NOT a gate here. (`format: false`, which sync writes for
        # day-2 idempotency, is honored by plan() skipping a converged disk
        # entirely, so this loop is never reached in that case.) Skipping mkfs on a
        # freshly-created partition left /boot raw -> unmountable -> empty fstab.
        for partition in disk.partitions:
            self._format_partition(disk.device, partition)
        
        # Mount partitions
        self._mount_partitions(disk)

    def _show_current_layout(self, device: str) -> None:
        """Show current partition layout.
        
        Args:
            device: Device path
        """
        print(f"\nCurrent layout of {device}:")
        try:
            result = Command.execute("lsblk", [device, "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT"])
            print(result.stdout)
        except Exception as e:
            print(f"Warning: Could not display current layout: {e}")

    def _has_partition_table(self, device: str) -> bool:
        """Check if device has a partition table.
        
        Args:
            device: Device path
            
        Returns:
            True if device has a partition table
        """
        try:
            result = Command.execute("parted", ["-s", device, "print"])
            # Decode stdout if it's bytes
            stdout = result.stdout.decode('utf-8') if isinstance(result.stdout, bytes) else result.stdout
            # parted ALWAYS prints a "Partition Table:" line — even for an empty
            # disk, where it reads "unknown" (and "loop" for a whole-device
            # filesystem with no table). Only a real label (gpt/msdos/bsd/…)
            # counts; matching the bare prefix would treat an empty disk as
            # partitioned and refuse to create a table on first install.
            import re
            m = re.search(
                r"(?:Partition Table|Tabla de particiones):\s*(\S+)", stdout
            )
            if not m:
                return False
            return m.group(1).strip().lower() not in ("unknown", "loop", "none")
        except Exception:
            # Fail SAFE: a probe that could not RUN (parted/arch-chroot missing,
            # exec error) is not evidence the disk is empty. On a disk-wiping tool
            # we must not assume "no table" on uncertainty — that would let a
            # wipe_disk:false plan repartition an unreadable-but-populated disk.
            # Assume a table exists so plan() routes to "populated, skipping".
            # (An empty disk never reaches here: parted runs and reports "unknown".)
            return True

    def _get_existing_partitions(self, device: str) -> List[Dict[str, str]]:
        """Get existing partitions on the device.
        
        Args:
            device: Device path
            
        Returns:
            List of partition info dictionaries with keys: number, start, end, size
        """
        try:
            result = Command.execute("parted", ["-s", device, "unit", "MiB", "print"])
            # Decode stdout if it's bytes
            stdout = result.stdout.decode('utf-8') if isinstance(result.stdout, bytes) else result.stdout
            partitions = []
            
            # Parse parted output
            lines = stdout.split('\n')
            for line in lines:
                # Look for lines that start with a number (partition entries)
                line = line.strip()
                if not line:
                    continue
                
                # Skip lines containing "Free Space" or without proper structure
                if 'Free Space' in line or 'free space' in line.lower():
                    continue
                
                # Check if line starts with a digit
                if line[0].isdigit():
                    parts = line.split()
                    if len(parts) >= 4:
                        # Verify first part is an integer (partition number)
                        try:
                            partition_num = int(parts[0])
                            partitions.append({
                                'number': str(partition_num),
                                'start': parts[1],
                                'end': parts[2],
                                'size': parts[3]
                            })
                        except ValueError:
                            # Not a valid partition number, skip this line
                            continue
            
            return partitions
        except Exception as e:
            print(f"Warning: Could not get existing partitions: {e}")
            return []

    def _get_next_available_start(self, device: str) -> str:
        """Get the next available start position after existing partitions.
        
        Args:
            device: Device path
            
        Returns:
            Start position as string (e.g., "100MiB")
        """
        existing_partitions = self._get_existing_partitions(device)
        
        if not existing_partitions:
            # No existing partitions, start at 1MiB for alignment
            return "1MiB"
        
        print(f"Detected {len(existing_partitions)} existing partition(s):")
        for part in existing_partitions:
            print(f"  Partition {part['number']}: {part['start']} -> {part['end']} (size: {part['size']})")
        
        # Find the maximum end position
        max_end = "1MiB"
        for part in existing_partitions:
            end_str = part.get('end', '0MiB')
            # Extract numeric value
            try:
                end_value = float(end_str.replace('MiB', '').replace('MB', '').replace('GiB', '').replace('GB', ''))
                # Convert to MiB if needed
                if 'GiB' in end_str or 'GB' in end_str:
                    end_value = end_value * 1024
                current_max = float(max_end.replace('MiB', '').replace('MB', ''))
                if end_value > current_max:
                    max_end = f"{end_value}MiB"
            except ValueError:
                continue
        
        # Return the position right after the last partition
        return max_end
    
    def _get_disk_size_mib(self, device: str) -> float:
        """Get the total size of the disk in MiB.
        
        Args:
            device: Device path
            
        Returns:
            Disk size in MiB
        """
        try:
            result = Command.execute("blockdev", ["--getsize64", device])
            stdout_str = result.stdout.decode('utf-8') if isinstance(result.stdout, bytes) else str(result.stdout)
            size_bytes = int(stdout_str.strip())
            size_mib = size_bytes / (1024 * 1024)
            return size_mib
        except Exception:
            # Fallback: try to parse from parted
            try:
                result = Command.execute("parted", ["-s", device, "unit", "MiB", "print"])
                stdout_str = result.stdout.decode('utf-8') if isinstance(result.stdout, bytes) else str(result.stdout)
                for line in stdout_str.split('\n'):
                    if 'Disk' in line and device in line:
                        # Extract size from line like "Disk /dev/vda: 15360MiB"
                        parts = line.split(':')
                        if len(parts) >= 2:
                            size_str = parts[1].strip().split()[0]
                            return float(size_str.replace('MiB', '').replace('MB', ''))
            except Exception:  # unparsable parted output -> large-number default below
                pass

        # If all else fails, return a large number
        return 999999.0

    def _wipe_disk(self, device: str) -> None:
        """Wipe the entire disk.
        
        Args:
            device: Device path
        """
        print(f"Wiping disk {device}...")
        print("WARNING: This will destroy all data on the disk!")
        
        # Wipe first and last few MB of the disk
        Command.execute("wipefs", ["--all", "--force", device])
        Command.execute("sgdisk", ["--zap-all", device])

    def _create_partition_table(self, device: str, table_type: str) -> None:
        """Create a new partition table.
        
        Args:
            device: Device path
            table_type: Partition table type (gpt or msdos)
        """
        print(f"Creating {table_type} partition table on {device}...")
        Command.execute("parted", ["-s", device, "mklabel", table_type])

    def _create_partitions(self, disk: DiskLayout) -> None:
        """Create all partitions on the disk.
        
        Args:
            disk: Disk layout configuration
        """
        print(f"Creating partitions on {disk.device}...")
        
        # Get existing partitions to find the next partition number and start position
        existing_partitions = self._get_existing_partitions(disk.device)
        
        # Determine next partition number
        if existing_partitions:
            last_partition_num = max(int(p['number']) for p in existing_partitions)
            partition_number = last_partition_num + 1
            print(f"Found {len(existing_partitions)} existing partition(s), starting from partition {partition_number}")
        else:
            partition_number = 1
            print("No existing partitions found, starting from partition 1")
        
        # Get starting position and disk size
        start = self._get_next_available_start(disk.device)
        disk_size_mib = self._get_disk_size_mib(disk.device)
        start_value = float(start.replace('MiB', '').replace('MB', '').replace('GiB', '').replace('GB', ''))
        if 'GiB' in start or 'GB' in start:
            start_value = start_value * 1024
        
        # Check if there's enough space
        available_space_mib = disk_size_mib - start_value
        print(f"Starting new partitions at {start}")
        print(f"Available space: {available_space_mib:.1f} MiB ({available_space_mib / 1024:.2f} GiB)")
        
        if available_space_mib < 100:  # Less than 100 MiB available
            raise RuntimeError(
                f"Not enough space available on {disk.device}. "
                f"Only {available_space_mib:.1f} MiB available after existing partitions. "
                f"Set 'wipe_disk: true' in your configuration to wipe and recreate the disk."
            )
        
        for partition in disk.partitions:
            end = self._calculate_partition_end(start, partition.size, disk.device)
            
            # Create partition using parted
            cmd = [
                "-s", disk.device,
                "mkpart", "primary"
            ]
            
            # Add filesystem type for parted (different from actual filesystem)
            if partition.filesystem == FileSystemType.FAT32:
                cmd.append("fat32")
            elif partition.filesystem == FileSystemType.SWAP:
                cmd.append("linux-swap")
            else:
                cmd.append("ext4")  # Default for parted
            
            cmd.extend([start, end])
            
            print(f"Creating partition {partition.label}: {start} to {end}")
            Command.execute("parted", cmd)
            
            # Set partition type flags for GPT
            if disk.partition_table.value == "gpt":
                if partition.partition_type.value == "esp":
                    Command.execute("parted", ["-s", disk.device, "set", str(partition_number), "esp", "on"])
                elif partition.partition_type.value == "linux-swap":
                    Command.execute("parted", ["-s", disk.device, "set", str(partition_number), "swap", "on"])
            
            # Store partition device path
            part_device = self._get_partition_device(disk.device, partition_number)
            self.partition_map[partition.label] = part_device
            print(f"Partition {partition.label} created at {part_device}")
            
            start = end
            partition_number += 1

    def _calculate_partition_end(self, start: str, size: str, device: str) -> str:
        """Calculate the end position for a partition.
        
        Args:
            start: Start position (e.g., "100MiB")
            size: Partition size (e.g., "100MiB", "50%", "rest")
            device: Device path
            
        Returns:
            End position string for parted
        """
        if size.lower() == "rest":
            return "100%"
        elif size.endswith("%"):
            return size
        else:
            # For absolute sizes, we need to calculate: start + size = end
            # Parse start value
            start_value = float(start.replace('MiB', '').replace('MB', '').replace('GiB', '').replace('GB', ''))
            start_unit = 'MiB'
            if 'GiB' in start or 'GB' in start:
                start_unit = 'GiB'
                if 'GB' in start:
                    start_value = start_value * 1000 / 1024  # Convert GB to GiB
            
            # Parse size value
            size_value: float = 0.0
            size_unit = 'MiB'
            if 'GiB' in size or 'GB' in size:
                size_value = float(size.replace('GiB', '').replace('GB', ''))
                size_unit = 'GiB'
                if 'GB' in size:
                    size_value = size_value * 1000 / 1024  # Convert GB to GiB
            elif 'MiB' in size or 'MB' in size:
                size_value = float(size.replace('MiB', '').replace('MB', ''))
                size_unit = 'MiB'
                if 'MB' in size:
                    size_value = size_value * 1000 / 1024  # Convert MB to MiB
            
            # Convert everything to the same unit (MiB for consistency)
            if start_unit == 'GiB':
                start_value = start_value * 1024
            if size_unit == 'GiB':
                size_value = size_value * 1024
            
            # Calculate end position
            end_value = start_value + size_value
            
            # Return in the most appropriate unit
            if end_value >= 1024:
                return f"{end_value / 1024:.1f}GiB"
            else:
                return f"{end_value:.1f}MiB"

    def _get_partition_device(self, device: str, partition_number: int) -> str:
        """Get the device path for a specific partition number.
        
        Args:
            device: Base device path (e.g., /dev/sda or /dev/nvme0n1)
            partition_number: Partition number
            
        Returns:
            Full partition device path (e.g., /dev/sda1 or /dev/nvme0n1p1)
        """
        # A partition node is "<dev>p<N>" when the device name ends in a digit
        # (nvme0n1 -> nvme0n1p1, mmcblk0 -> mmcblk0p1, loop0 -> loop0p1,
        # nbd0 -> nbd0p1); otherwise the number appends directly (sda -> sda1,
        # vda -> vda1). Keying off the trailing digit is the standard rule and
        # also covers loop/nbd, which the old nvme/mmcblk special-case missed
        # (breaking the documented loopback test flow).
        if device and device[-1].isdigit():
            return f"{device}p{partition_number}"
        else:
            return f"{device}{partition_number}"

    def _refresh_partition_table(self, device: str) -> None:
        """Refresh the kernel's partition table.
        
        Args:
            device: Device path
        """
        print("Refreshing partition table...")
        try:
            Command.execute("partprobe", [device])
        except Exception:
            # If partprobe fails, try alternative
            Command.execute("blockdev", ["--rereadpt", device])

    def _format_partition(self, base_device: str, partition: Partition) -> None:
        """Format a partition with the specified filesystem.
        
        Args:
            base_device: Base device path
            partition: Partition configuration
        """
        part_device = self.partition_map[partition.label]
        
        # Handle encryption first
        if partition.encrypt:
            part_device = self._encrypt_partition(part_device, partition)
        
        print(f"Formatting {partition.label} ({part_device}) as {partition.filesystem.value}...")
        
        if partition.filesystem == FileSystemType.EXT4:
            Command.execute("mkfs.ext4", ["-F", "-L", partition.label, part_device])
        
        elif partition.filesystem == FileSystemType.BTRFS:
            Command.execute("mkfs.btrfs", ["-f", "-L", partition.label, part_device])
            # Create subvolumes if specified
            if partition.btrfs_subvolumes:
                self._create_btrfs_subvolumes(part_device, partition.btrfs_subvolumes)
        
        elif partition.filesystem == FileSystemType.FAT32:
            Command.execute("mkfs.fat", ["-F32", "-n", partition.label, part_device])
        
        elif partition.filesystem == FileSystemType.SWAP:
            Command.execute("mkswap", ["-L", partition.label, part_device])
        
        elif partition.filesystem == FileSystemType.XFS:
            Command.execute("mkfs.xfs", ["-f", "-L", partition.label, part_device])
        
        # Update partition map with encrypted device if applicable
        if partition.encrypt:
            self.partition_map[partition.label] = part_device

    def _encrypt_partition(self, device: str, partition: Partition) -> str:
        """Encrypt a partition using LUKS.
        
        Args:
            device: Partition device path
            partition: Partition configuration
            
        Returns:
            Path to the opened LUKS device (/dev/mapper/...)
        """
        if not partition.luks_name:
            raise ValueError(f"luks_name is required for encrypted partition {partition.label}")

        from dasik.lib.actions.luks_uuid import luks_uuid
        print(f"Encrypting partition {partition.label}...")
        name = partition.luks_name
        # Pin the UUID so kernel-cmdline can derive rd.luks.name up front (the
        # plan is built before this apply runs, so the header UUID isn't
        # otherwise known on the first apply).
        uuid_args = ["--uuid", luks_uuid(name, partition.luks_uuid)]

        if partition.luks_keyfile:
            # Key-file based: cryptsetup reads the passphrase from the file.
            key = ["--key-file", partition.luks_keyfile]
            Command.execute("cryptsetup",
                            ["luksFormat", "--type", "luks2", "--batch-mode", *uuid_args, *key, device])
            Command.execute("cryptsetup", ["open", *key, device, name])
        elif partition.luks_password is not None:
            # Declarative passphrase: piped over stdin via `--key-file -`, so
            # nothing is prompted and the passphrase never hits argv/the process
            # list. This is what makes an encrypted install unattended.
            passphrase = partition.luks_password.encode()
            Command.execute(
                "cryptsetup",
                ["luksFormat", "--type", "luks2", "--batch-mode", *uuid_args, "--key-file", "-", device],
                input=passphrase,
            )
            Command.execute(
                "cryptsetup", ["open", "--key-file", "-", device, name],
                input=passphrase,
            )
        else:
            # No passphrase/keyfile declared — legacy interactive fallback.
            print("NOTE: no luks_password/luks_keyfile set; cryptsetup will prompt.")
            Command.execute("cryptsetup", ["luksFormat", "--type", "luks2", *uuid_args, device])
            Command.execute("cryptsetup", ["open", device, name])

        # The extra key for automatic boot unlock (a pendrive keyfile) is NOT
        # enrolled here: LuksKeyfileAction owns it, so it also works on an
        # already-installed machine — this path only runs while formatting.

        # Optional hardware-backed keyslots for passwordless unlock.
        if partition.unlock_tpm2:
            self._enroll_cryptenroll(device, partition, "--tpm2-device=auto")
        if partition.unlock_fido2:
            self._enroll_cryptenroll(device, partition, "--fido2-device=auto")

        return f"/dev/mapper/{name}"

    def _enroll_cryptenroll(self, device: str, partition: Partition, kind: str) -> None:
        """Enroll a TPM2/FIDO2 keyslot with systemd-cryptenroll, authorised by the
        existing passphrase via $PASSWORD (the passphrase stays as a fallback).
        """
        if partition.luks_password is None:
            print(f"NOTE: {kind} enroll skipped ({partition.label}): needs luks_password.")
            return
        Command.execute("systemd-cryptenroll", [kind, device],
                        env={"PASSWORD": partition.luks_password})

    def _create_btrfs_subvolumes(self, device: str, subvolumes: List[BtrfsSubvolume]) -> None:
        """Create btrfs subvolumes.
        
        Args:
            device: Btrfs partition device
            subvolumes: List of subvolume configurations
        """
        # Mount temporarily to create subvolumes
        temp_mount = "/mnt/btrfs_temp"
        Path(temp_mount).mkdir(parents=True, exist_ok=True)
        
        try:
            Command.execute("mount", [device, temp_mount])
            
            for subvol in subvolumes:
                print(f"Creating btrfs subvolume: {subvol.name}")
                subvol_path = f"{temp_mount}/{subvol.name}"
                Command.execute("btrfs", ["subvolume", "create", subvol_path])
            
            Command.execute("umount", [temp_mount])
        finally:
            # Cleanup temp mount point (best-effort; a leftover empty dir is benign)
            try:
                Path(temp_mount).rmdir()
            except Exception:  # best-effort cleanup in a finally block
                pass

    @staticmethod
    def _mount_depth(mountpoint: str) -> int:
        """Number of path components in a mountpoint, for mount ordering.

        ``/`` -> 0, ``/boot`` -> 1, ``/boot/efi`` -> 2. Using ``count('/')``
        instead is wrong: both ``/`` and ``/boot`` yield 1, so root ties with a
        top-level mount and may be mounted *after* it, shadowing the child.
        """
        return len([c for c in mountpoint.split("/") if c])

    def _mount_partitions(self, disk: DiskLayout) -> None:
        """Mount all partitions according to their configuration.

        Args:
            disk: Disk layout configuration
        """
        print("\nMounting partitions...")
        
        # Sort partitions by mountpoint depth so parents mount before children —
        # crucially, root ("/") must mount FIRST, else mounting it at /mnt
        # shadows an already-mounted child (e.g. the ESP at /mnt/boot), leaving
        # the child empty and the install non-bootable.
        # A btrfs with subvolumes must be mounted even if the PARTITION has no
        # mountpoint of its own (the subvolumes carry the mountpoints) — else the
        # root subvolumes never mount and genfstab is empty. Order by the effective
        # mountpoint (partition's, or the shallowest subvolume's), so "/" mounts
        # first and doesn't shadow a child like /boot.
        def _effective(p):
            if p.mountpoint:
                return p.mountpoint
            if p.filesystem == FileSystemType.BTRFS and p.btrfs_subvolumes:
                return min((s.mountpoint for s in p.btrfs_subvolumes),
                           key=self._mount_depth)
            return None

        partitions_to_mount = [
            p for p in disk.partitions
            if _effective(p) is not None and p.filesystem != FileSystemType.SWAP
        ]
        partitions_to_mount.sort(key=lambda p: self._mount_depth(_effective(p)))
        
        for partition in partitions_to_mount:
            if partition.filesystem == FileSystemType.BTRFS and partition.btrfs_subvolumes:
                self._mount_btrfs_subvolumes(partition)
            else:
                self._mount_partition(partition)
        
        # Enable swap if present
        for partition in disk.partitions:
            if partition.filesystem == FileSystemType.SWAP:
                device = self.partition_map[partition.label]
                print(f"Enabling swap on {device}")
                Command.execute("swapon", [device])

    def _mount_partition(self, partition: Partition) -> None:
        """Mount a single partition.
        
        Args:
            partition: Partition configuration
        """
        device = self.partition_map[partition.label]
        mountpoint = f"/mnt{partition.mountpoint}"

        # Create mountpoint (with the mode that path requires, e.g. /var/tmp 1777)
        _make_mountpoint(mountpoint, partition.mountpoint)
        
        # Build mount command
        mount_cmd = ["mount"]
        if partition.mount_options:
            mount_cmd.extend(["-o", ",".join(partition.mount_options)])
        mount_cmd.extend([device, mountpoint])
        
        print(f"Mounting {partition.label} at {mountpoint}")
        Command.execute("mount", mount_cmd[1:])  # Skip 'mount' as Command adds it

    def _mount_btrfs_subvolumes(self, partition: Partition) -> None:
        """Mount btrfs subvolumes.
        
        Args:
            partition: Partition configuration with subvolumes
        """
        device = self.partition_map[partition.label]
        
        for subvol in partition.btrfs_subvolumes:
            mountpoint = f"/mnt{subvol.mountpoint}"
            _make_mountpoint(mountpoint, subvol.mountpoint)
            
            # Partition-level mount_options apply to every subvolume (+ its own).
            options = self._subvol_mount_options(partition, subvol)

            mount_cmd = ["mount", "-o", ",".join(options), device, mountpoint]
            
            print(f"Mounting subvolume {subvol.name} at {mountpoint}")
            Command.execute("mount", mount_cmd[1:])

    def get_partition_device(self, label: str) -> Optional[str]:
        """Get the device path for a partition by its label.
        
        Args:
            label: Partition label
            
        Returns:
            Device path or None if not found
        """
        return self.partition_map.get(label)

    def get_all_partitions(self) -> Dict[str, str]:
        """Get all partition mappings.
        
        Returns:
            Dictionary mapping partition labels to device paths
        """
        return self.partition_map.copy()
