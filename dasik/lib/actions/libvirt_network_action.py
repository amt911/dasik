"""Action: whether libvirt's `default` NAT network comes up with the daemon.

libvirt ships the network DEFINITION (``/etc/libvirt/qemu/networks/default.xml``,
a pacman-owned file) but not the symlink under ``autostart/`` that starts it.
``pacman -Ql libvirt`` lists the autostart DIRECTORY and nothing in it. So a
freshly installed host has a `default` network that is defined, inactive, and
stays inactive across reboots — and the first guest fails with

    Requested operation is not valid: network 'default' is not active

which the Arch wiki carries as its own troubleshooting entry. Every machine this
repo installs had the symlink made by hand, so it survived no reinstall and no
``sync`` ever saw it.

Why a symlink and not ``virsh net-autostart default``: the symlink IS what virsh
writes, and it is the only form that works from a chroot, where no libvirtd is
listening. The domain therefore applies during a normal phase-4 install exactly
as it does day-2.

The link is written ABSOLUTE, matching virsh. That makes reading it back a trap
worth stating: inside ``/mnt`` an absolute link points at the LIVE ISO's
``/etc``, so ``os.path.exists`` answers about the installer rather than the
target. ``actual()`` uses ``islink``, which asks about the link itself.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from .abstract_action import AbstractAction
from ..logging import run_logger
from ..state.change import Change, Op
from ..state.set_math import compute_changes

NETWORKS_DIR = "/etc/libvirt/qemu/networks"
AUTOSTART_DIR = "/etc/libvirt/qemu/networks/autostart"

# The only network libvirt defines for you, and the only one this domain knows
# how to autostart. A network someone else defined has an XML file dasik never
# wrote, and adopting it would mean owning a definition it cannot reproduce.
_DEFAULT = "default"


class LibvirtNetworkAction(AbstractAction):
    """Own the autostart of libvirt's shipped `default` network."""

    _DOMAIN = "libvirt_networks"

    def __init__(self, config: Any, context=None):
        super().__init__(config, context)
        cfg: Dict[str, Any] = config if isinstance(config, dict) else {}
        self._kvm: Dict[str, Any] = cfg.get("kvm") or {}
        self._declared = bool(self._kvm.get("default_network"))

    @classmethod
    def empty_config(cls):
        """Root-level action: bootstrap from an empty mapping, not a list."""
        return {}

    @property
    def name(self) -> str:
        return "Libvirt Default Network"

    @property
    def is_optional(self) -> bool:
        return True

    # --- paths ----------------------------------------------------------- #

    def _target(self):
        return getattr(self.context, "target", None) if self.context else None

    def _path(self, absolute: str) -> str:
        target = self._target()
        return target.path(absolute) if target is not None else "/mnt" + absolute

    def _link_path(self) -> str:
        return self._path(f"{AUTOSTART_DIR}/{_DEFAULT}.xml")

    def _definition_path(self) -> str:
        return self._path(f"{NETWORKS_DIR}/{_DEFAULT}.xml")

    # --- v3 contract ------------------------------------------------------ #

    def actual(self) -> set:
        """``islink``, never ``exists`` — see the module docstring."""
        return {_DEFAULT} if os.path.islink(self._link_path()) else set()

    def _desired(self) -> set:
        return {_DEFAULT} if self._declared else set()

    def plan(self, managed: Any) -> List[Change]:
        changes, _drift = compute_changes(
            self._DOMAIN,
            desired=self._desired(),
            managed=managed or [],
            actual=self.actual(),
        )
        return changes

    def managed_keys(self) -> dict:
        return {self._DOMAIN: sorted(self._desired())}

    def apply(self, plan) -> None:
        for change in plan:
            if change.op is Op.REMOVE:
                self._unlink()
            else:
                self._link()

    def import_state(self, managed: Any = None) -> dict:
        """The flag this machine actually carries.

        Absent AND undeclared captures nothing, so a bootstrap ``sync`` from an
        empty seed adds no `kvm` block to a host that has no libvirt. Absent but
        declared is CLEARED rather than omitted: ``ConfigWriter.merge`` only
        overwrites a key, so silence would leave the stale `true` standing.

        ``install`` is never spoken for. It selects the package/unit toggle, and
        a captured config that carries libvirt as literal packages would have
        all thirteen of them subtracted out from under it. It is carried
        FORWARD, though: ``ConfigWriter.merge`` splices fragments over the
        config by top-level key, so a fragment holding only the flag would
        replace the whole `kvm` block and delete a declared toggle nothing else
        captures.
        """
        if self.actual():
            return {"kvm": {**self._kvm, "default_network": True}}
        if self._declared:
            return {"kvm": {**self._kvm, "default_network": False}}
        return {}

    # --- the two writes ---------------------------------------------------- #

    def _link(self) -> None:
        definition = self._definition_path()
        if not os.path.exists(definition):
            # `virsh net-undefine default` was run, or libvirt is not installed
            # yet. A link to a file that is not there makes libvirtd log an
            # error on every start, and defining the network is not this
            # domain's business — it owns the autostart, not the definition.
            run_logger.get().warning(
                f"libvirt has no {NETWORKS_DIR}/{_DEFAULT}.xml, so the "
                f"`default` network was not set to autostart. Restore it with "
                f"`virsh net-define /usr/share/libvirt/networks/default.xml`.")
            return
        link = self._link_path()
        if os.path.lexists(link):
            # `actual()` asks islink, so reaching here means the path holds
            # something that is NOT a symlink. os.symlink would abort the apply
            # with FileExistsError, and whatever that file is, it is not dasik's
            # to delete.
            run_logger.get().warning(
                f"{AUTOSTART_DIR}/{_DEFAULT}.xml exists and is not a symlink, "
                f"so the `default` network was left as it is. Remove it by hand "
                f"if libvirt should autostart the network.")
            return
        os.makedirs(os.path.dirname(link), exist_ok=True)
        # Absolute, the way virsh writes it: the link has to resolve on the
        # BOOTED machine, not on whatever root is mounted while it is written.
        os.symlink(f"{NETWORKS_DIR}/{_DEFAULT}.xml", link)
        self._warn_not_live()

    def _unlink(self) -> None:
        """Un-autostart. Never ``net-undefine``: dasik linked the network, it
        did not create it, and a running guest may still be on it."""
        try:
            os.unlink(self._link_path())
        except OSError:
            pass

    def _warn_not_live(self) -> None:
        """libvirtd reads the autostart directory when it starts, so on a live
        target the link lands and the network stays down until then. Say it;
        do not restart the daemon under running guests."""
        target = self._target()
        if target is not None and not target.is_chroot:
            run_logger.get().warning(
                "the `default` network will autostart from the next libvirtd "
                "start. To bring it up now, run `virsh net-start default`.")

    def verify(self) -> bool:
        return not self.plan(managed=[])
