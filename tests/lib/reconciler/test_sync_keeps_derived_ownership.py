"""After a `sync`, dasik must still own what the toggles derive (issue #197).

`sync` is handed the RAW config on purpose — it rewrites that file and must not
flatten the derived items into it. But it also recomputes `managed ← actual()`,
and the actions whose `actual()` is scoped to *declared* paths
(`DropFilesAction`, `HomeFilesAction`) cannot see what a toggle contributes. So
every file a block derives quietly stopped being owned, and turning the block
off no longer removed it.

The asymmetry is what made it invisible: `PackagesAction` and `SystemdAction`
read the machine, so after a sync they own MORE, while the file domains own
less — in the same pass.
"""
from unittest.mock import patch

import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.action_registry import get_default_registry
from dasik.lib.actions.actions_handler_v2 import setup_actions
from dasik.lib.actions.drop_files_action import DropFilesAction
from dasik.lib.expand import expand_config, subtract_contributions
from dasik.lib.reconciler.reconciler import Reconciler
from dasik.lib.target.target import Target

_REFLECTOR = "/etc/xdg/reflector/reflector.conf"
_SEED = {"bootloader": "sd-boot", "reflector": {"countries": ["ES"]}}


@pytest.fixture
def machine(tmp_path):
    """A target carrying the file the `reflector` block derives."""
    (tmp_path / "etc/xdg/reflector").mkdir(parents=True)
    (tmp_path / "etc/xdg/reflector/reflector.conf").write_text(
        expand_config(_SEED)["files"][0]["content"])
    (tmp_path / "boot/loader/entries").mkdir(parents=True)
    (tmp_path / "boot/loader/loader.conf").write_text("default arch\n")
    (tmp_path / "boot/loader/entries/arch.conf").write_text(
        "title Arch\noptions root=LABEL=root rw\n")
    return tmp_path


def _sync(machine, seed):
    setup_actions()
    reconciler = Reconciler(
        config=seed, target=Target(root=str(machine)), manifest=None,
        action_metas=get_default_registry().get_all_actions(),
        owned_config=expand_config(seed),
    )
    new_config, manifest = reconciler.sync()
    return subtract_contributions(new_config, seed), manifest


def test_the_manifest_still_owns_the_derived_file(machine):
    _config, manifest = _sync(machine, _SEED)

    assert _REFLECTOR in manifest.to_dict()["managed"]["files"]


def test_the_captured_config_does_not_carry_it_as_a_hand_written_file(machine):
    """Ownership is not the same as declaration: the file stays attributed to
    the block, so the captured config re-derives it instead of repeating it."""
    config, _manifest = _sync(machine, _SEED)

    assert _REFLECTOR not in [f["path"] for f in config.get("files", [])]
    assert _REFLECTOR in [f["path"] for f in expand_config(config)["files"]]


def test_dropping_the_block_after_a_sync_still_removes_the_file(machine):
    """The regression itself. Before the fix this plan was empty, so the file
    lived on forever in a machine whose config no longer mentioned it."""
    _config, manifest = _sync(machine, _SEED)
    managed = manifest.to_dict()["managed"]["files"]

    action = DropFilesAction(expand_config({"bootloader": "sd-boot"}),
                             ActionContext(target=Target(root=str(machine))))

    assert [(c.op.name, c.item) for c in action.plan(managed=managed)] == [
        ("DELETE", _REFLECTOR)]


def test_without_the_expanded_config_ownership_is_the_declared_set_only(machine):
    """The default stays as it was: a caller that passes no `owned_config`
    (every existing one) gets exactly the old behaviour."""
    setup_actions()
    reconciler = Reconciler(
        config=_SEED, target=Target(root=str(machine)), manifest=None,
        action_metas=get_default_registry().get_all_actions(),
    )
    _new_config, manifest = reconciler.sync()

    assert _REFLECTOR not in manifest.to_dict()["managed"].get("files", [])


def test_a_file_nobody_declares_or_derives_is_still_not_owned(machine):
    """Widening ownership must not turn every file on the machine into dasik's:
    only what the config declares or a block derives."""
    (machine / "etc/modprobe.d").mkdir(parents=True, exist_ok=True)
    (machine / "etc/modprobe.d/somebody-elses.conf").write_text("options x y=1\n")

    _config, manifest = _sync(machine, _SEED)

    assert "/etc/modprobe.d/somebody-elses.conf" not in \
        manifest.to_dict()["managed"]["files"]


def test_the_cli_hands_sync_the_expanded_config(tmp_path, monkeypatch):
    """The wiring itself: `dasik sync` must pass it, or the fix is unreachable."""
    import json
    from dasik import __main__ as main

    config_path = tmp_path / "c.json"
    config_path.write_text(json.dumps(_SEED))
    seen = {}

    real_init = Reconciler.__init__

    def spy(self, *args, **kwargs):
        seen["owned_config"] = kwargs.get("owned_config")
        return real_init(self, *args, **kwargs)

    monkeypatch.setattr(Reconciler, "__init__", spy)
    with patch.object(main, "_target_or_none", return_value=Target(root=str(tmp_path))):
        main._cmd_sync(config_path, str(tmp_path))

    assert seen["owned_config"] is not None
    assert "reflector" in seen["owned_config"]["packages"]
