"""A repartition must reach the confirmation prompt.

`DiskPartitionAction` emits its wipe as `Op.INSTALL`, so before this the plan
had zero "destructive" changes and `Reconciler.apply()` never asked — a fresh
install ran `wipefs --all` + `sgdisk --zap-all` + mkfs unprompted, while
removing a package did ask. The prompt also has to say WHAT it is about to
erase; "Apply 1 destructive change(s)?" is not a device name.
"""
from unittest.mock import patch



from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.disk_partition_action import DiskPartitionAction
from dasik.lib.state.change import Change, Op, Plan
from dasik.lib.target.target import Target


def _cfg(wipe=True):
    return {"disks": [{
        "device": "/dev/sdz", "partition_table": "gpt", "wipe_disk": wipe,
        "partitions": [{"label": "root", "size": "rest", "filesystem": "ext4",
                        "mountpoint": "/", "format": True}],
    }]}


def _action(wipe=True, labels=()):
    a = DiskPartitionAction(_cfg(wipe), ActionContext(target=Target(root="/mnt")))
    with patch.object(DiskPartitionAction, "_device_labels", return_value=set(labels)), \
         patch.object(DiskPartitionAction, "_has_partition_table", return_value=False):
        return a.plan(managed=[])


def test_wipe_change_is_declared_destructive():
    changes = _action(wipe=True)
    assert changes and changes[0].destructive is True


def test_wipe_change_names_the_device_and_what_is_on_it():
    # Labels that do NOT match the declared layout — otherwise the disk counts
    # as converged and there is no change to inspect.
    changes = _action(wipe=True, labels=("windows", "data"))
    reason = changes[0].reason
    assert "/dev/sdz" in changes[0].item
    for label in ("windows", "data"):
        assert label in reason, reason


def test_empty_disk_is_still_flagged_destructive():
    """`format: true` on an unpartitioned disk still makes filesystems."""
    changes = _action(wipe=False)
    assert changes and changes[0].destructive is True


def test_plan_render_marks_it():
    changes = _action(wipe=True)
    assert "DESTRUCTIVE" in Plan(changes).render()


# --- the gate itself -------------------------------------------------------- #

def _reconciler(plan_changes):
    from dasik.lib.reconciler.reconciler import Reconciler
    r = Reconciler(config={}, target=Target(root="/mnt"), manifest=None,
                   action_metas=[])
    return r, Plan(plan_changes)


def test_apply_prompts_for_a_disk_wipe():
    r, plan = _reconciler([Change("disks", Op.INSTALL, "/dev/sdz",
                                  reason="wipe_disk", destructive=True)])
    asked = []

    def fake_input(prompt):
        asked.append(prompt)
        return "n"

    assert r.apply(plan, [], input_fn=fake_input) is None      # refused
    assert asked, "a disk wipe must not proceed without asking"


def test_prompt_lists_what_will_be_destroyed():
    r, plan = _reconciler([
        Change("disks", Op.INSTALL, "/dev/sdz", reason="wipe_disk (esp, root)",
               destructive=True),
        Change("packages", Op.REMOVE, "vim"),
    ])
    shown = []

    def fake_input(prompt):
        shown.append(prompt)
        return "n"

    r.apply(plan, [], input_fn=fake_input)
    text = "\n".join(shown)
    assert "/dev/sdz" in text
    assert "vim" in text


def test_yes_still_skips_the_prompt():
    r, plan = _reconciler([Change("disks", Op.INSTALL, "/dev/sdz",
                                  reason="wipe_disk", destructive=True)])

    def boom(prompt):
        raise AssertionError("--yes must not prompt")

    # No actions registered, so apply() just persists an empty manifest.
    r.apply(plan, [], assume_yes=True, input_fn=boom)
