"""`root=` and `rw` are derived from the layout, never removable (issue #189).

On an unencrypted machine nothing derived them: `BootloaderAction` writes them
into the entry, and `KernelCmdlineAction` only derived a `root=` for an
ENCRYPTED root. So the tokens sat on the entry as parameters nobody declared —
and the moment a `sync` recorded ownership of the live entry, any later plan
proposed to delete them:

    - [kernel_cmdline] remove root=LABEL=ROOT  (no longer declared)
    - [kernel_cmdline] remove rw               (no longer declared)

which is a machine that does not boot, announced as a routine removal.
"""
import pytest

from dasik.lib.actions.action_context import ActionContext
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.target.target import Target


_PLAIN = {"bootloader": "sd-boot", "disks": {"disks": [{
    "device": "/dev/vda", "partition_table": "gpt",
    "partitions": [
        {"label": "ESP", "size": "512MiB", "filesystem": "fat32",
         "partition_type": "esp", "mountpoint": "/boot"},
        {"label": "ROOT", "size": "rest", "filesystem": "ext4",
         "partition_type": "linux", "mountpoint": "/"},
    ]}]}}


def _entry(tmp_path, options):
    entries = tmp_path / "boot/loader/entries"
    entries.mkdir(parents=True, exist_ok=True)
    (entries / "arch.conf").write_text(f"title Arch\noptions {options}\n")
    (tmp_path / "boot/loader/loader.conf").write_text("default arch\n")
    return tmp_path


def _plan(tmp_path, config, options, managed=()):
    action = KernelCmdlineAction(config, ActionContext(target=Target(root=str(tmp_path))))
    return [(c.op.name, c.item) for c in action.plan(managed=list(managed))]


# --- derived from the layout ------------------------------------------------ #

def test_the_root_of_an_unencrypted_layout_is_derived(tmp_path):
    _entry(tmp_path, "quiet")

    planned = _plan(tmp_path, _PLAIN, "quiet")

    assert ("INSTALL", "root=LABEL=ROOT") in planned
    assert ("INSTALL", "rw") in planned


def test_an_entry_that_already_has_them_plans_nothing(tmp_path):
    _entry(tmp_path, "root=LABEL=ROOT rw")

    assert _plan(tmp_path, _PLAIN, "root=LABEL=ROOT rw") == []


def test_an_encrypted_root_still_derives_the_mapper_device(tmp_path):
    config = {"bootloader": "sd-boot", "disks": {"disks": [{
        "device": "/dev/vda", "partition_table": "gpt",
        "partitions": [{"label": "ROOT", "size": "rest", "filesystem": "ext4",
                        "partition_type": "linux", "mountpoint": "/",
                        "encrypt": True, "luks_name": "cryptroot",
                        "luks_uuid": "11111111-2222-3333-4444-555555555555"}]}]}}
    _entry(tmp_path, "quiet")

    planned = [item for _op, item in _plan(tmp_path, config, "quiet")]

    assert "root=/dev/mapper/cryptroot" in planned
    assert "root=LABEL=ROOT" not in planned


def test_an_explicit_root_still_wins(tmp_path):
    """A hand-written `root=` overrides the derived one — the merge rule that
    already existed for encrypted roots, unchanged."""
    config = dict(_PLAIN, kernel_cmdline=["root=/dev/vda2"])
    _entry(tmp_path, "quiet")

    planned = [item for _op, item in _plan(tmp_path, config, "quiet")]

    assert "root=/dev/vda2" in planned
    assert "root=LABEL=ROOT" not in planned


# --- and never removable ----------------------------------------------------- #

def test_the_root_parameter_is_never_removed_even_when_owned(tmp_path):
    """The regression. A manifest that owns `root=`/`rw` (every manifest a sync
    wrote) plus a config that cannot derive them — no `disks` block, the day-2
    shape — used to plan their deletion."""
    _entry(tmp_path, "root=LABEL=ROOT rw quiet")

    planned = _plan(tmp_path, {"bootloader": "sd-boot"}, "root=LABEL=ROOT rw quiet",
                    managed=["root=LABEL=ROOT", "rw", "quiet"])

    assert ("REMOVE", "root=LABEL=ROOT") not in planned
    assert ("REMOVE", "rw") not in planned


def test_an_ordinary_parameter_is_still_removed(tmp_path):
    """The safety net must not swallow the whole domain."""
    _entry(tmp_path, "root=LABEL=ROOT rw quiet")

    planned = _plan(tmp_path, {"bootloader": "sd-boot"}, "root=LABEL=ROOT rw quiet",
                    managed=["quiet"])

    assert ("REMOVE", "quiet") in planned


@pytest.mark.parametrize("token", ["root=/dev/mapper/cryptroot", "rootflags=subvol=@",
                                   "rootfstype=ext4", "rw", "ro"])
def test_every_root_defining_token_is_protected(tmp_path, token):
    _entry(tmp_path, token)

    planned = _plan(tmp_path, {"bootloader": "sd-boot"}, token, managed=[token])

    assert ("REMOVE", token) not in planned
