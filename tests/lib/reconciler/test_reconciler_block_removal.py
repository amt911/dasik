"""Reconciler defect: an optional action whose config block is ABSENT must
still be visited when a previous generation owns items in its domain
(``Reconciler._any_managed_for``), so ownership can be released (or, for a
set-math domain like ``systemd``, so the owned-but-undeclared item is planned
for removal).

The probe used to be ``cls.__new__(cls)`` + ``managed_keys()`` — an instance
with NO ``__init__`` run at all. For every action below that reads a
config-derived instance attribute inside ``managed_keys()`` (directly, or
through ``ScalarV3Action``/``CompositeV3Action``'s value machinery), that
raised ``AttributeError``, was swallowed by the broad ``except Exception``, and
reported "owns nothing" — so ``build_plan`` skipped the action outright
(``continue``) and it never even reached ``results``. Two failures follow from
that, both silent:

* the aggregate plan never proposes the systemd DISABLE the documented
  contract promises for an owned-but-undeclared unit;
* the new manifest (built only from VISITED actions in
  ``Reconciler._build_new_manifest``) drops the domain's ownership — which
  happens to look correct for the domains whose own ``managed_keys()`` is
  already declaration-gated (disks/timezone return ``[]`` for an empty
  config), but is a coincidence of never running, not a contract.

Fixed probe: a REAL instance (``cls(cls.empty_config(), None)``), so
``managed_keys()`` sees the same derived attributes ``plan()``/``apply()``
would. Falls back to the old bare-``__new__`` probe if construction itself
raises, and to "owns nothing" if that ALSO raises.

Each test below drives ``Reconciler.build_plan()`` through the REAL registry
(``setup_actions()``), for one action at a time, exactly the way
``test_feature_detectability.py``'s "block absent from the WHOLE config"
section already does for ``PacmanRepositoriesAction``. What matters for a
"silent" domain (disks/timezone/locales/pacman) is asserted twice: the plan
proposes nothing for it, AND — the part the old probe broke — the action was
actually VISITED (``results`` names it), which is what lets
``_build_new_manifest`` release ownership deliberately rather than by never
running. ``systemd``, ``snapper`` and ``firewall`` are the domains where
visiting the action changes the PLAN itself: systemd a DISABLE of the unit
the manifest still owns; snapper/firewall a destructive REMOVE of the config
the manifest still owns ("snapper y firewall deben eliminar su config si
desaparece" — the removal semantics landed after this file's probe fix, so
what was once asserted as "silent" for these two is now asserted as REMOVE).
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from dasik.lib.actions.action_registry import get_default_registry
from dasik.lib.actions.actions_handler_v2 import setup_actions
from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.actions.firewall_action import FirewallAction
from dasik.lib.actions.locale_action import LocaleAction
from dasik.lib.actions.pacman_action import PacmanAction
from dasik.lib.actions.snapper_action import SnapperAction
from dasik.lib.actions.systemd_action import SystemdAction
from dasik.lib.actions.timezone_action import TimezoneAction
from dasik.lib.reconciler.reconciler import Reconciler
from dasik.lib.target.target import Target


@pytest.fixture(autouse=True)
def _real_registry():
    """Every test drives the REAL registry, not a hand-built meta list — the
    bug is in how the Reconciler decides whether to even construct the
    action, which only the real ``config_key``/``is_optional`` wiring
    exercises the same way `dasik plan` does."""
    setup_actions()
    yield


def _meta_for(cls):
    metas = [m for m in get_default_registry().get_all_actions() if m["class"] is cls]
    assert len(metas) == 1, f"{cls.__name__} is not registered exactly once"
    return metas


def _run(config, manifest, metas, target_root):
    reconciler = Reconciler(config=config, target=Target(root=str(target_root)),
                            manifest=manifest, action_metas=metas)
    plan, results = reconciler.build_plan()
    return reconciler, plan, results


def _domain_changes(plan, domain):
    return [(c.op.name, c.item) for c in plan.changes if c.domain == domain]


# --- timezone ---------------------------------------------------------- #

def test_timezone_block_absent_plans_nothing_and_releases_ownership(tmp_path):
    etc = tmp_path / "etc"
    etc.mkdir(parents=True)
    (etc / "localtime").symlink_to("/usr/share/zoneinfo/Europe/Madrid")

    manifest = {"managed": {"timezone": ["Europe/Madrid"]}}
    reconciler, plan, results = _run({}, manifest, _meta_for(TimezoneAction), tmp_path)

    assert _domain_changes(plan, "timezone") == []
    assert len(results) == 1, "TimezoneAction must be VISITED, not skipped"
    assert isinstance(results[0].action, TimezoneAction)

    new_manifest = reconciler._build_new_manifest(results)
    assert not new_manifest.managed.get("timezone")


# --- locales ------------------------------------------------------------- #

_LOCALES_ITEM = json.dumps({
    "selected_locales": ["en_US.UTF-8 UTF-8"],
    "desired_locale": "en_US.UTF-8",
    "desired_tty_layout": "us",
}, sort_keys=True)


def test_locales_block_absent_plans_nothing_and_releases_ownership(tmp_path):
    etc = tmp_path / "etc"
    etc.mkdir(parents=True)
    (etc / "locale.gen").write_text("en_US.UTF-8 UTF-8\n#es_ES.UTF-8 UTF-8\n")
    (etc / "locale.conf").write_text("LANG=en_US.UTF-8")
    (etc / "vconsole.conf").write_text("KEYMAP=us")

    manifest = {"managed": {"locales": [_LOCALES_ITEM]}}
    reconciler, plan, results = _run({}, manifest, _meta_for(LocaleAction), tmp_path)

    assert _domain_changes(plan, "locales") == []
    assert len(results) == 1, "LocaleAction must be VISITED, not skipped"
    assert isinstance(results[0].action, LocaleAction)

    new_manifest = reconciler._build_new_manifest(results)
    # The regression this suite exists for: CompositeV3Action's generic
    # managed_keys() serializes _desired_state() unconditionally, which for an
    # undeclared LocaleAction is a real (falsy-looking but non-empty) JSON
    # blob of empty fields — a FAKE claim of ownership over nothing, instead
    # of releasing the domain. LocaleAction.managed_keys() must gate on
    # `_declared` the same way `plan()` already does.
    assert not new_manifest.managed.get("locales")


# --- pacman ---------------------------------------------------------------- #

_PACMAN_ITEM = json.dumps({
    "Color": True, "Parallel": True, "VerbosePkgLists": False, "multilib": False,
}, sort_keys=True)


def test_pacman_block_absent_plans_nothing_and_releases_ownership(tmp_path):
    etc = tmp_path / "etc"
    etc.mkdir(parents=True)
    (etc / "pacman.conf").write_text(
        "[options]\nParallelDownloads = 5\nColor\n\n"
        "#[multilib]\n#Include = /etc/pacman.d/mirrorlist\n")

    manifest = {"managed": {"pacman": [_PACMAN_ITEM]}}
    reconciler, plan, results = _run({}, manifest, _meta_for(PacmanAction), tmp_path)

    assert _domain_changes(plan, "pacman") == []
    assert len(results) == 1, "PacmanAction must be VISITED, not skipped"
    assert isinstance(results[0].action, PacmanAction)

    new_manifest = reconciler._build_new_manifest(results)
    # Same shape of regression as locales: PacmanAction is also a
    # CompositeV3Action, and its _desired_state() defaults every flag rather
    # than raising, so the generic managed_keys() invents a truthy item for a
    # config nobody declared.
    assert not new_manifest.managed.get("pacman")


# --- disks ------------------------------------------------------------------ #

def test_disks_block_absent_plans_nothing_and_releases_ownership(tmp_path):
    manifest = {"managed": {"disks": ["/dev/vda"]}}
    reconciler, plan, results = _run({}, manifest, _meta_for(DiskPartitionAction),
                                     tmp_path)

    assert _domain_changes(plan, "disks") == []
    assert len(results) == 1, "DiskPartitionAction must be VISITED, not skipped"
    assert isinstance(results[0].action, DiskPartitionAction)

    new_manifest = reconciler._build_new_manifest(results)
    assert not new_manifest.managed.get("disks")


# --- snapper ----------------------------------------------------------------- #

def test_snapper_block_absent_plans_removal_and_releases_ownership(tmp_path):
    """Product decision (superseding the comment this test used to carry):
    "snapper... deben eliminar su config si desaparece" — a config the
    manifest owns is REMOVEd (destructive: `snapper delete-config` also drops
    its snapshots), not just silently disowned."""
    configs = tmp_path / "etc/snapper/configs"
    configs.mkdir(parents=True)
    (configs / "root").write_text('SUBVOLUME="/"\n')

    manifest = {"managed": {"snapper": ["root"]}}
    reconciler, plan, results = _run({}, manifest, _meta_for(SnapperAction), tmp_path)

    assert _domain_changes(plan, "snapper") == [("REMOVE", "root")]
    assert plan.destructive() != []
    assert len(results) == 1, "SnapperAction must be VISITED, not skipped"
    assert isinstance(results[0].action, SnapperAction)

    new_manifest = reconciler._build_new_manifest(results)
    assert not new_manifest.managed.get("snapper")


# --- firewall ---------------------------------------------------------------- #

def test_firewall_block_absent_plans_removal_and_releases_ownership(tmp_path):
    """Product decision (superseding the comment this test used to carry):
    "firewall... debe eliminar su config si desaparece" — a zone the manifest
    owns is REMOVEd, not just silently disowned."""
    zones = tmp_path / "etc/firewalld/zones"
    zones.mkdir(parents=True)
    (zones / "public.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n<zone>\n  <short>Public</short>\n'
        '  <service name="dhcpv6-client"/>\n  <service name="ssh"/>\n</zone>\n')

    manifest = {"managed": {"firewall": ["public"]}}
    reconciler, plan, results = _run({}, manifest, _meta_for(FirewallAction), tmp_path)

    assert _domain_changes(plan, "firewall") == [("REMOVE", "public")]
    assert plan.destructive() != []
    assert len(results) == 1, "FirewallAction must be VISITED, not skipped"
    assert isinstance(results[0].action, FirewallAction)

    new_manifest = reconciler._build_new_manifest(results)
    assert not new_manifest.managed.get("firewall")


# --- systemd (destructive: DISABLE of an owned-but-undeclared unit) -------- #

def test_systemd_block_absent_plans_a_disable_for_the_owned_unit(tmp_path):
    manifest = {"managed": {"systemd": ["sshd.service"]}}

    def fake_execute(cmd, args, target=None, **kwargs):
        if args[:1] == ["list-unit-files"]:
            return MagicMock(stdout=b"sshd.service enabled\n", returncode=0)
        return MagicMock(stdout=b"", returncode=0)

    with patch("dasik.lib.actions.systemd_action.Command.execute",
              side_effect=fake_execute):
        reconciler, plan, results = _run({}, manifest, _meta_for(SystemdAction),
                                         tmp_path)

    assert _domain_changes(plan, "systemd") == [("DISABLE", "sshd.service")]
    assert plan.destructive() != []
    assert len(results) == 1, "SystemdAction must be VISITED, not skipped"

    applied: list = []

    def fake_apply_execute(cmd, args, target=None, **kwargs):
        applied.append(args)
        return MagicMock(stdout=b"", returncode=0)

    with patch("dasik.lib.actions.systemd_action.Command.execute",
              side_effect=fake_apply_execute):
        new_manifest = reconciler.apply(plan, results, assume_yes=True)

    assert ["disable", "sshd.service"] in applied
    assert new_manifest is not None
    # SystemdAction.managed_keys() == {"systemd": self._d_on()}, and with no
    # `systemd` block declared `_d_on()` is []: the unit is fully released.
    assert not new_manifest.managed.get("systemd")


# --- red-proof helper (see report for the manual before/after run) --------- #
#
# These tests are proven to fail against the OLD probe
# (`action_cls.__new__(action_cls)` + no fallback) by temporarily reverting
# `Reconciler._any_managed_for` and re-running this file — never committed
# that way. See reconciler-report.md for the captured failure output.
