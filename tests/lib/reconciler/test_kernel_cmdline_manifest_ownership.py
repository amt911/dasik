"""S3 (review of fix/rootflags-sync-drift): `managed_keys()` must record the
token that is REALLY on the entry, not the literal desired one — proven at the
Reconciler level, where `_build_new_manifest` calls `managed_keys()` on EVERY
action in the plan's results, including one whose OWN plan was silent this
round because some UNRELATED domain changed.

Before the fix, a silent `kernel_cmdline` plan (root cause B's equivalence:
declared `zstd:3`, entry has bare `zstd`) still had `managed_keys()` return the
LITERAL derived token. That is harmless as long as nothing else ever rebuilds
the manifest — but the moment an unrelated domain's apply does, the new
manifest claims ownership of a `rootflags=` value that is not on the entry at
all, and the next `sync` (`actual ∩ (claimable ∪ declared)`) can find neither
the literal nor the live bare token among the two sets, silently losing
ownership of `rootflags=` altogether (the "manifest must agree with the system
it describes" state-invariant AGENTS.md calls out).

A minimal synthetic ``action_metas`` (KernelCmdlineAction + a stub) is used
instead of the full ``setup_actions()`` registry — this is a Reconciler-level
mechanism test, not an end-to-end install, and the full registry's other
actions shell out to real tooling (pacman, systemctl, …) that has no place in
a unit test.
"""
from dasik.lib.actions.abstract_action import AbstractAction
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.reconciler.reconciler import Reconciler
from dasik.lib.state.change import Change, Op
from dasik.lib.target.target import Target

# The CAPTURED shape: findmnt resolved the declared bare `compress-force=zstd`
# to the kernel's default level `zstd:3` — exactly evidence #1 from the
# original brief.
_CONFIG = {
    "bootloader": "sd-boot",
    "kernel_cmdline": ["quiet"],
    "disks": {"disks": [{
        "device": "/dev/vda", "partition_table": "gpt",
        "partitions": [{"label": "root", "size": "rest", "filesystem": "btrfs",
                        "partition_type": "linux", "mountpoint": None,
                        "btrfs_subvolumes": [{"name": "@", "mountpoint": "/",
                                              "mount_options": ["compress-force=zstd:3",
                                                                "noatime"]}]}]}]},
}

# What a REAL install of the bare (undeclared-level) config would have
# recorded for `kernel_cmdline`: at install time the entry did not exist yet,
# so there was nothing to substitute and `managed_keys()` recorded the literal
# bare token, matching what `apply()` wrote — no manifest an install writes
# ever omits the token it owns.
_INSTALL_MANAGED = ["root=LABEL=root", "rw",
                    "rootflags=compress-force=zstd,noatime,subvol=@", "quiet"]


class _StubUnrelatedAction(AbstractAction):
    """A fake domain that plans exactly one INSTALL the first time — the
    "unrelated change" that forces `Reconciler.apply()` to rebuild the WHOLE
    manifest via `_build_new_manifest`, which calls `managed_keys()` on every
    result, including `kernel_cmdline`'s (whose own plan is silent)."""

    @property
    def name(self) -> str:
        return "stub"

    def is_needed(self) -> bool:
        return True

    def execute(self) -> None:
        pass

    def plan(self, managed):
        return [] if "x" in managed else [Change("stub", Op.INSTALL, "x")]

    def apply(self, changes) -> None:
        pass

    def managed_keys(self) -> dict:
        return {"stub": ["x"]}


def _machine(tmp_path):
    (tmp_path / "boot/loader/entries").mkdir(parents=True)
    (tmp_path / "boot/loader/loader.conf").write_text("default arch\n")
    (tmp_path / "boot/loader/entries/arch.conf").write_text(
        "title Arch\noptions root=LABEL=root rw "
        "rootflags=compress-force=zstd,noatime,subvol=@ quiet\n")
    return tmp_path


def _action_metas():
    return [
        {"class": KernelCmdlineAction, "config_key": "__root__",
         "is_optional": True, "required_fields": [], "depends_on": []},
        {"class": _StubUnrelatedAction, "config_key": "__root__",
         "is_optional": True, "required_fields": [], "depends_on": []},
    ]


def _install_manifest():
    return {"managed": {"kernel_cmdline": list(_INSTALL_MANAGED)}}


def test_kernel_cmdline_plan_is_silent_but_the_unrelated_change_is_real(tmp_path):
    """Sanity check on the scenario itself before asserting the fix."""
    target = Target(root=str(_machine(tmp_path)))
    reconciler = Reconciler(config=_CONFIG, target=target,
                            manifest=_install_manifest(), action_metas=_action_metas())

    plan, _results = reconciler.build_plan()

    assert [c for c in plan.changes if c.domain == "kernel_cmdline"] == []
    assert any(c.domain == "stub" for c in plan.changes)


def test_managed_keys_agrees_with_the_entry_after_an_unrelated_apply(tmp_path):
    target = Target(root=str(_machine(tmp_path)))
    reconciler = Reconciler(config=_CONFIG, target=target,
                            manifest=_install_manifest(), action_metas=_action_metas())
    plan, results = reconciler.build_plan()

    new_manifest = reconciler.apply(plan, results, assume_yes=True)

    assert new_manifest is not None
    managed = new_manifest.to_dict()["managed"]["kernel_cmdline"]
    assert "rootflags=compress-force=zstd,noatime,subvol=@" in managed
    assert "rootflags=compress-force=zstd:3,noatime,subvol=@" not in managed


def test_the_next_plan_off_the_rebuilt_manifest_is_still_silent(tmp_path):
    """The round trip: the manifest S3 fixed must itself replan to nothing —
    the general "plan -> apply -> plan silent" invariant, exercised here
    specifically across an UNRELATED domain's apply forcing the rebuild."""
    target = Target(root=str(_machine(tmp_path)))
    reconciler = Reconciler(config=_CONFIG, target=target,
                            manifest=_install_manifest(), action_metas=_action_metas())
    plan, results = reconciler.build_plan()
    new_manifest = reconciler.apply(plan, results, assume_yes=True)

    next_reconciler = Reconciler(config=_CONFIG, target=target,
                                 manifest=new_manifest.to_dict(),
                                 action_metas=_action_metas())
    next_plan, _next_results = next_reconciler.build_plan()

    assert [c for c in next_plan.changes if c.domain == "kernel_cmdline"] == []
