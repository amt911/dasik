"""Action: create snapper (btrfs snapshot) configs declaratively.

The snapper package + the timeline/cleanup timers are contributed by the
`snapper` expand toggle (packages + systemd). This action does the imperative
bit those can't: ``snapper -c <name> create-config <subvolume>``. It is
idempotent — a create-config is planned only for a config that does not already
exist under /etc/snapper/configs, so a converged system re-plans to nothing.
"""
import os
import shutil
from typing import Any, List

from .abstract_action import AbstractAction
from ..command_worker.command_worker import Command
from ..exceptions.exceptions import CommandExecutionError
from ..state.change import Change, Op

_CONFIGS_DIR = "/etc/snapper/configs"
_CONF_D_SNAPPER = "/etc/conf.d/snapper"


class SnapperAction(AbstractAction):
    """Create snapper configs for the declared btrfs subvolumes."""

    _DOMAIN = "snapper"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg = config if isinstance(config, dict) else {}
        self.enable: bool = cfg.get("enable", False)
        raw = cfg.get("configs") or ([{"name": "root", "subvolume": "/"}]
                                     if self.enable else [])
        # accept dicts or model-like objects
        self.configs: List[dict] = [
            c if isinstance(c, dict) else {"name": c.name, "subvolume": c.subvolume}
            for c in raw
        ]

    @property
    def name(self) -> str:
        return "Snapper Configuration"

    @property
    def is_optional(self) -> bool:
        return True

    @classmethod
    def empty_config(cls):
        return {}

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _config_path(self, name: str) -> str:
        t = self._target()
        canonical = f"{_CONFIGS_DIR}/{name}"
        return t.path(canonical) if t is not None else "/mnt" + canonical

    def _exists(self, name: str) -> bool:
        return os.path.exists(self._config_path(name))

    def _configs_dir(self) -> str:
        t = self._target()
        return t.path(_CONFIGS_DIR) if t is not None else "/mnt" + _CONFIGS_DIR

    # --- v3 contract -------------------------------------------------- #

    def actual(self) -> set:
        """Every config file under /etc/snapper/configs, REGARDLESS of
        `enable` -- the "A = all" convention `SystemdAction.actual()` already
        follows. Gating this on `enable` (as it did before) meant a `sync`
        run while the block was disabled/absent computed
        `actual ∩ (claimable ∪ declared)` with actual=set(), so the
        reconciler dispossessed the manifest of a config it still owned —
        the next plan/apply with the block still disabled/absent then saw
        nothing to remove (S2). The reconciler's own intersection with
        claimable/declared still scopes this: a config dasik never created
        is never falsely claimed just because its file happens to exist.
        """
        try:
            names = os.listdir(self._configs_dir())
        except OSError:
            return set()
        base = self._configs_dir()
        return {n for n in names if os.path.isfile(os.path.join(base, n))}

    def plan(self, managed):
        """CREATE for a declared config missing on disk, plus REMOVE for a
        managed config that has disappeared from the declaration.

        "Disappeared" covers all three forms the config disappears in: the
        name dropped out of ``configs`` while ``enable`` stayed true,
        ``enable`` flipped to false, or the whole ``snapper`` block is gone
        (the reconciler then hands :meth:`empty_config`). ``declared`` is
        empty in the latter two cases even if a stray ``configs`` list is
        still sitting in a disabled block — a disabled/absent block converges
        to nothing, full stop, the same rule ``managed_keys`` follows.

        A REMOVE is destructive by construction (``Change.__post_init__``):
        ``apply`` deletes every snapshot the config owns (see
        ``_delete_config``) plus the config's own registration. It never
        calls ``snapper delete-config`` — that command is MEASURED
        (docs/FACTS.md FACT-SFRM-3) to corrupt dasik's own recommended
        layout (a separately-mounted ``@.snapshots``), so ``_delete_config``
        reimplements the removal itself instead.
        """
        declared = {c["name"] for c in self.configs} if self.enable else set()
        changes: List[Change] = []
        if self.enable:
            for c in self.configs:
                if not self._exists(c["name"]):
                    changes.append(Change(self._DOMAIN, Op.CREATE, c["name"],
                                          reason="create-config"))
        for name in sorted(set(managed or ()) - declared):
            if self._exists(name):
                changes.append(Change(self._DOMAIN, Op.REMOVE, name,
                                      reason="no longer declared"))
        return changes

    @staticmethod
    def _snapshots_dir(subvol: str) -> str:
        base = subvol.rstrip("/")
        return f"{base}/.snapshots" if base else "/.snapshots"

    def _is_snapshots_mount(self, snap_dir: str, target) -> bool:
        res = Command.execute("mountpoint", ["-q", snap_dir], target=target)
        return getattr(res, "returncode", 1) == 0

    def apply(self, changes) -> None:
        target = self._target()
        if target is None:
            return
        if changes:
            self._ensure_snapper_installed(target)
        by_name = {c["name"]: c["subvolume"] for c in self.configs}
        for change in changes:
            if change.op is Op.REMOVE:
                # The config is no longer declared, so it is not in
                # `self.configs` any more — read its subvolume back from the
                # (still present, about to be deleted) config file on disk,
                # the same way `import_state` does.
                removed_subvol = self._read_subvolume(self._config_path(change.item))
                if removed_subvol is None:
                    # B1: a truncated/hand-edited/vanished config file must
                    # never fall back to "/" — that guessed ROOT's own
                    # subvolume and deleted every snapshot of a config that
                    # is still DECLARED. `import_state` already treats an
                    # unreadable SUBVOLUME as "skip this config"; apply must
                    # refuse just as hard, before any destructive call.
                    raise CommandExecutionError(
                        f"snapper config '{change.item}': cannot read "
                        "SUBVOLUME, refusing to delete snapshots"
                    )
                self._delete_config(change.item, removed_subvol, target)
                continue
            subvol = by_name.get(change.item)
            if subvol is None:
                continue
            self._create_config(change.item, subvol, target)

    @staticmethod
    def _ensure_snapper_installed(target) -> None:
        """Install snapper + snap-pac if they are not there yet.

        This action runs BEFORE PackagesAction on purpose: snap-pac's pacman
        hooks snapshot every transaction, so the config has to exist before the
        big package transaction, not after it (on 2026-07-19 the whole install
        ran with no snapper config and no snapshots). Running first means the
        binary may not be installed yet, so the action installs its own
        prerequisite. `--needed` makes it a no-op afterwards, and PackagesAction
        still owns both names in the manifest.
        """
        probe = Command.execute("pacman", ["-Qq", "snapper"], target=target)
        if getattr(probe, "returncode", 0) == 0:
            return
        Command.execute("pacman", ["--noconfirm", "--needed", "-S", "snapper", "snap-pac"],
                        target=target, check=True, stream=True)

    def _create_config(self, name: str, subvol: str, target) -> None:
        # `snapper create-config` fails if a `.snapshots` subvolume already
        # exists at the config path. Dasik pre-creates & mounts a dedicated
        # @snapshots subvolume there, so follow the Arch wiki: unmount it, let
        # snapper make its own nested .snapshots, delete that, and remount our
        # subvolume (via its existing fstab entry). Without this the create
        # failed silently and re-fired on every apply (non-idempotent).
        snap_dir = self._snapshots_dir(subvol)
        preexist = self._is_snapshots_mount(snap_dir, target)
        if preexist:
            Command.execute("umount", [snap_dir], target=target)
            Command.execute("rmdir", [snap_dir], target=target)

        res = Command.execute(
            "snapper", ["--no-dbus", "-c", name, "create-config", subvol],
            target=target,
        )
        if getattr(res, "returncode", 0) != 0:
            # Surface the failure instead of swallowing it (the bug the VM caught):
            # a swallowed error left the config uncreated and the action re-firing.
            raise CommandExecutionError(
                f"snapper create-config for '{name}' failed "
                f"(rc={getattr(res, 'returncode', '?')})"
            )

        if preexist:
            # Delete snapper's auto-created nested .snapshots and restore our own.
            Command.execute("btrfs", ["subvolume", "delete", snap_dir], target=target)
            Command.execute("mkdir", ["-p", snap_dir], target=target)
            Command.execute("mount", [snap_dir], target=target)

    def _numbered_snapshot_dirs(self, snap_dir: str, target) -> List[str]:
        """The numbered snapshot directories directly under *snap_dir*
        (each one a real record: ``<N>/info.xml`` + the ``<N>/snapshot``
        btrfs subvolume). Read directly off disk — like ``import_state``
        already does for the configs directory — rather than parsed out of
        ``snapper -c <name> list``, so it works even with the config's own
        metadata already gone (mid-removal) or the D-Bus daemon unavailable.
        """
        real = target.path(snap_dir) if target is not None else snap_dir
        try:
            return sorted((n for n in os.listdir(real) if n.isdigit()), key=int)
        except OSError:
            return []

    def _delete_config(self, name: str, subvol: str, target) -> None:
        """Delete a config's snapshots and its own registration.

        MEASURED in a guest (docs/FACTS.md FACT-SFRM-*): plain
        ``snapper --no-dbus -c <name> delete-config`` only works cleanly when
        ``.snapshots`` is snapper's OWN nested subvolume (no separate mount) —
        it deletes every snapshot plus the ``.snapshots`` subvolume itself,
        clears the name from ``/etc/conf.d/snapper``'s ``SNAPPER_CONFIGS``,
        rc=0. On dasik's OWN recommended layout — a SEPARATELY mounted
        ``@.snapshots`` (the same one ``_create_config``'s wiki-dance defends
        on the way in, config/vm-btrfs-snapper.json style with an explicit
        ``@.snapshots`` subvolume) — the identical command instead deletes the
        individual snapshots fine, then FAILS
        (``btrfs_util_delete_subvolume_fd() errno:22``) trying to
        delete/unmount the CONTAINER subvolume, because it is a live mount
        boundary snapper cannot cross that way: rc=1, and it leaves
        ``.snapshots`` unmounted with its ``/etc/fstab`` entry GONE while the
        config file is still there — a half-broken machine reported as a
        failure, worse off than before the call.

        So this method never hands snapper that path at all: it does the
        removal itself, the SAME way for both layouts — delete every numbered
        snapshot subvolume under ``.snapshots`` directly (measured safe even
        on the separate mount: that half of a real delete-config already
        succeeds there), then drop the config's own bookkeeping (the file
        under ``/etc/snapper/configs``, its name out of ``SNAPPER_CONFIGS``)
        by hand — exactly what a successful delete-config itself leaves
        behind. Only when ``.snapshots`` is NOT a separate mount does it also
        delete the ``.snapshots`` container subvolume, matching what the
        plain command does in that case; dasik's own provisioned
        ``@.snapshots`` mount is never touched, so it stays mounted and
        usable — the destructive half is scoped to exactly what dasik owns
        (the snapshots and the config), never the disk layout underneath it.

        Snapshot-deletion failures abort immediately, config metadata LAST:
        an interrupted removal leaves the config still fully present (just
        missing some snapshots) rather than a config gone with orphaned
        snapshot subvolumes nothing can find by name any more — the next
        `plan` still sees (and can retry) the REMOVE either way.

        Retry-safe (S1): every btrfs delete is preceded by an existence check,
        so a run resumed after an interruption never hands `btrfs` a path that
        is already gone — the numbered `<N>/snapshot` (killed between the
        subvolume delete and its `rm -rf <N>`) and the `.snapshots` container
        itself (already removed by an earlier partial run, or a day-2
        `--target /mnt` pass that only mounted `@`, leaving `.snapshots` a
        plain, empty directory) both converge instead of raising forever. The
        container additionally goes through `btrfs subvolume show` — a plain
        directory is never handed to `subvolume delete`, which would fail on
        it exactly the same way an already-deleted path does.

        The leftover bookkeeping directory (`<N>/`, just `info.xml` once its
        `snapshot` subvolume is gone) and the config's own file are removed
        through Python (`shutil.rmtree`/`os.remove`) on the target's
        host-visible path, the same idiom `FirewallAction` already uses for
        its zone file — there is no chroot boundary to cross for a path
        already resolved via `target.path()`, so there is no reason to shell
        out for a plain file/directory delete (N6). Only the real btrfs
        subvolume operations still go through `Command.execute`.
        """
        snap_dir = self._snapshots_dir(subvol)
        preexist = self._is_snapshots_mount(snap_dir, target)
        for n in self._numbered_snapshot_dirs(snap_dir, target):
            snap_path = f"{snap_dir}/{n}/snapshot"
            if self._exists_on_target(snap_path, target):
                res = Command.execute("btrfs", ["subvolume", "delete", snap_path],
                                      target=target)
                if getattr(res, "returncode", 0) != 0:
                    raise CommandExecutionError(
                        f"btrfs subvolume delete failed for '{snap_path}' "
                        f"(rc={getattr(res, 'returncode', '?')})"
                    )
            # else: an interrupted previous run already deleted the
            # subvolume but never got to remove the leftover `<N>/`
            # bookkeeping directory below -- nothing to delete, still
            # retry-safe.
            self._rmtree_on_target(f"{snap_dir}/{n}", target)
        if not preexist and self._is_subvolume(snap_dir, target):
            res = Command.execute("btrfs", ["subvolume", "delete", snap_dir],
                                  target=target)
            if getattr(res, "returncode", 0) != 0:
                raise CommandExecutionError(
                    f"btrfs subvolume delete failed for '{snap_dir}' "
                    f"(rc={getattr(res, 'returncode', '?')})"
                )
        self._remove_on_target(self._config_path(name))
        self._drop_from_snapper_configs_list(name, target)

    def _resolve_on_target(self, path: str, target) -> str:
        return target.path(path) if target is not None else path

    def _exists_on_target(self, path: str, target) -> bool:
        return os.path.exists(self._resolve_on_target(path, target))

    def _is_subvolume(self, path: str, target) -> bool:
        """True when *path* is itself a btrfs subvolume, not merely an
        existing directory -- guards ``_delete_config`` against handing
        ``btrfs subvolume delete`` a plain directory (S1), which fails and
        would leave the removal stuck the same way an already-deleted path
        does."""
        if not self._exists_on_target(path, target):
            return False
        res = Command.execute("btrfs", ["subvolume", "show", path], target=target)
        return getattr(res, "returncode", 1) == 0

    @staticmethod
    def _remove_on_target(path: str) -> None:
        """Delete a plain file at its already-resolved, host-visible path."""
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    def _rmtree_on_target(self, path: str, target) -> None:
        """Recursively delete a leftover bookkeeping directory -- never a
        subvolume itself (those go through ``btrfs subvolume delete``
        above)."""
        shutil.rmtree(self._resolve_on_target(path, target), ignore_errors=True)

    def _drop_from_snapper_configs_list(self, name: str, target) -> None:
        """Remove *name* from ``/etc/conf.d/snapper``'s ``SNAPPER_CONFIGS``
        — plain text munging (no subvolume boundary involved), so a direct
        read/write is safe and mirrors what a successful delete-config itself
        does (measured: FACT-SFRM-*). Written atomically (N6): a temp file in
        the same directory, then ``os.replace`` — a crash mid-write leaves the
        ORIGINAL file untouched rather than a truncated SNAPPER_CONFIGS."""
        path = target.path(_CONF_D_SNAPPER) if target is not None else _CONF_D_SNAPPER
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return
        out = []
        for line in lines:
            if line.strip().startswith("SNAPPER_CONFIGS="):
                remaining = [n for n in line.split("=", 1)[1].strip().strip('"').split()
                            if n != name]
                out.append(f'SNAPPER_CONFIGS="{" ".join(remaining)}"\n')
            else:
                out.append(line)
        tmp_path = f"{path}.dasik-tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.writelines(out)
        os.replace(tmp_path, path)

    def managed_keys(self) -> dict:
        if not self.enable:
            return {self._DOMAIN: []}
        return {self._DOMAIN: [c["name"] for c in self.configs]}

    def import_state(self, managed=None) -> dict:
        """Capture the snapper configs that exist on the target (sync).

        Each file under /etc/snapper/configs is a config named after the file,
        with its subvolume in ``SUBVOLUME=``. Returning ``{}`` (the old
        behaviour) meant a host with real snapshots round-tripped into a config
        with no `snapper` section at all — the action was then skipped as absent.
        """
        configs_dir = self._configs_dir()
        try:
            names = sorted(os.listdir(configs_dir))
        except OSError:
            return {}
        configs: List[dict] = []
        for name in names:
            path = os.path.join(configs_dir, name)
            if not os.path.isfile(path):
                continue
            subvol = self._read_subvolume(path)
            if subvol:
                configs.append({"name": name, "subvolume": subvol})
        if not configs:
            return {}
        return {self._DOMAIN: {"enable": True, "configs": configs}}

    @staticmethod
    def _read_subvolume(path: str) -> "str | None":
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    key, sep, value = line.partition("=")
                    if sep and key.strip() == "SUBVOLUME":
                        return value.strip().strip('"').strip("'") or None
        except OSError:
            return None
        return None

    # --- legacy executor bridge --------------------------------------- #


