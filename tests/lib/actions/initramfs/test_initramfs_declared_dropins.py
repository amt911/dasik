"""A drop-in the CONFIG declares must rebuild the image in the SAME run.

Counting the files on disk is not enough, and a VM is what proved it: the
reconciler builds the whole plan before any action applies, so when a run adds a
/etc/dracut.conf.d drop-in, InitramfsAction.plan() plans against a directory
DropFilesAction has not written to yet. The file lands, `apply` reports success,
and the image is untouched — the next `plan` then wants the rebuild, which
breaks the plan -> apply -> plan invariant even though the change is no longer
invisible.

So the plan has to read what the config DECLARES, not only what is on disk.
Removal is the same problem mirrored: the file is still there at plan time and
every file left behind is older than the image, so ownership — which lives in
the manifest — is the only thing that can say "DropFiles is about to delete
this".
"""
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.initramfs_action import InitramfsAction
from dasik.lib.state.change import Op
from dasik.lib.target.target import Target

_DROPIN = "/etc/dracut.conf.d/50-foreign.conf"
_BODY = 'omit_drivers+=" nvidia "\n'


def _target(tmp_path):
    """A converged dracut machine: dasik.conf written, image built after it."""
    import os
    (tmp_path / "etc/dracut.conf.d").mkdir(parents=True)
    (tmp_path / "usr/lib/modules/6.1.0").mkdir(parents=True)
    (tmp_path / "usr/lib/modules/6.1.0/pkgbase").write_text("linux\n")
    (tmp_path / "boot").mkdir()
    (tmp_path / "boot/initramfs-linux.img").write_text("image")
    (tmp_path / "etc/dracut.conf.d/dasik.conf").write_text("")
    os.utime(tmp_path / "etc/dracut.conf.d/dasik.conf", (1, 1))
    os.utime(tmp_path / "etc/dracut.conf.d", (1, 1))
    os.utime(tmp_path / "boot/initramfs-linux.img", (2, 2))
    return Target(root=str(tmp_path))


def _action(tmp_path, files=None, owned=None):
    cfg = {"initramfs": "dracut"}
    if files is not None:
        cfg["files"] = files
    manifest = {"managed": {"files": list(owned)}} if owned is not None else None
    ctx = ActionContext(target=_target(tmp_path), manifest=manifest)
    action = InitramfsAction(cfg, ctx)
    # The generator probe shells out to pacman against the target; this suite is
    # about the drop-ins, so pin it to what the config declares.
    action._detect_generator = lambda: "dracut"          # type: ignore[method-assign]
    return action


def _write(tmp_path, name, body):
    import os
    p = tmp_path / "etc/dracut.conf.d" / name
    p.write_text(body)
    os.utime(p, (1, 1))
    os.utime(tmp_path / "etc/dracut.conf.d", (1, 1))
    return p


def test_a_declared_dropin_that_is_not_on_disk_yet_plans_a_rebuild(tmp_path):
    """The reported case. DropFilesAction will write it later in this very run;
    the image has to be rebuilt after it, so the rebuild must be in the plan."""
    action = _action(tmp_path, files=[{"path": _DROPIN, "content": _BODY}])
    changes = action.plan(managed=[])
    assert [c.op for c in changes] == [Op.MODIFY], changes


def test_a_declared_dropin_whose_content_drifted_plans_a_rebuild(tmp_path):
    action = _action(tmp_path, files=[{"path": _DROPIN, "content": _BODY}])
    _write(tmp_path, "50-foreign.conf", 'omit_drivers+=" something-else "\n')
    changes = action.plan(managed=[])
    assert [c.op for c in changes] == [Op.MODIFY], changes


def test_a_declared_dropin_already_on_disk_is_converged(tmp_path):
    action = _action(tmp_path, files=[{"path": _DROPIN, "content": _BODY}])
    _write(tmp_path, "50-foreign.conf", _BODY)
    assert action.plan(managed=[]) == []


def test_an_owned_dropin_no_longer_declared_plans_a_rebuild(tmp_path):
    """Removal: the config dropped it, dasik owns it, so DropFilesAction is
    about to delete it. Nothing on disk can say that — every file left behind is
    older than the image — so the manifest is what tells us."""
    action = _action(tmp_path, files=[], owned=[_DROPIN])
    _write(tmp_path, "50-foreign.conf", _BODY)
    changes = action.plan(managed=[])
    assert [c.op for c in changes] == [Op.MODIFY], changes


def test_a_foreign_dropin_dasik_never_owned_is_left_alone(tmp_path):
    """Somebody else's file — envycontrol's, a hand-written one. dasik neither
    declares nor owns it, so it is not about to change: planning a rebuild for
    it would mean planning the same change on every single run, for ever."""
    action = _action(tmp_path, files=[], owned=[])
    _write(tmp_path, "99-someone-elses.conf", "# not ours\n")
    assert action.plan(managed=[]) == []
