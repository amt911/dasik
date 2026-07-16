"""Live discovery of local /etc snippet files for `sync` from an empty seed:
modprobe.d, modules-load.d, udev/rules.d, profile.d (skip symlinks + pacman-owned)
plus /etc/crypttab when it has real entries."""
import os
from unittest.mock import patch, MagicMock

from dasik.lib.actions.drop_files_action import DropFilesAction
from dasik.lib.actions.action_context import ActionContext
from dasik.lib.target.target import Target


def _ctx(root):
    return ActionContext(target=Target(root=root))


def _seed_tree(tmp_path):
    """Build a fake target /etc mirroring the user's real local files + noise."""
    def w(rel, content):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return p
    w("etc/modprobe.d/nested_virt.conf", "options kvm_amd nested=1\n")   # local
    w("etc/modprobe.d/firewalld-sysctls.conf", "# owned by firewalld\n")  # OWNED
    w("etc/modules-load.d/virtio.conf", "virtio\nvirtio_pci\n")           # local
    w("etc/udev/rules.d/1-qudelix.rules", 'KERNEL=="hidraw*"\n')          # local
    w("etc/profile.d/cuda.sh", "export PATH=/opt/cuda/bin:$PATH\n")       # OWNED
    # a symlink in profile.d (package default) -> must be skipped
    target = tmp_path / "etc/profile.d/cuda.sh"
    (tmp_path / "etc/profile.d/link.sh").symlink_to(target)
    return tmp_path


# files the "package manager" owns -> skipped during discovery
_OWNED = {"/etc/modprobe.d/firewalld-sysctls.conf", "/etc/profile.d/cuda.sh"}


def _pacman(cmd, args=None, *rest, **kw):
    # pacman -Qo <path> : rc 0 if owned, 1 otherwise
    if cmd == "pacman" and args and args[0] == "-Qo":
        owned = args[1] in _OWNED
        return MagicMock(returncode=0 if owned else 1)
    return MagicMock(returncode=1)


def _discover(tmp_path):
    a = DropFilesAction({}, _ctx(str(tmp_path)))
    with patch("dasik.lib.actions.drop_files_action.Command.execute", side_effect=_pacman):
        return a.import_state(managed=[])


def test_discovers_local_files_skips_owned_and_symlinks(tmp_path):
    frag = _discover(_seed_tree(tmp_path))
    modprobe = {e["name"] for e in frag["modprobe_conf"]}
    assert modprobe == {"nested_virt.conf"}                 # firewalld one skipped
    assert {e["name"] for e in frag["modules_load"]} == {"virtio.conf"}
    assert {e["name"] for e in frag["udev_rules"]} == {"1-qudelix.rules"}
    # profile.d: cuda.sh owned, link.sh is a symlink -> nothing captured
    assert frag["profile_d"] == []


def test_discovered_content_is_verbatim(tmp_path):
    frag = _discover(_seed_tree(tmp_path))
    virtio = next(e for e in frag["modules_load"] if e["name"] == "virtio.conf")
    assert virtio["content"] == "virtio\nvirtio_pci\n"


def test_declared_entry_wins_over_discovery(tmp_path):
    _seed_tree(tmp_path)
    a = DropFilesAction({"modprobe_conf": [{"name": "nested_virt.conf", "content": "DECLARED"}]},
                        _ctx(str(tmp_path)))
    with patch("dasik.lib.actions.drop_files_action.Command.execute", side_effect=_pacman):
        frag = a.import_state(managed=[])
    entries = {e["name"]: e["content"] for e in frag["modprobe_conf"]}
    # declared refreshes from disk (the file exists) -> on-disk content, one entry
    assert list(entries) == ["nested_virt.conf"]
    assert entries["nested_virt.conf"] == "options kvm_amd nested=1\n"


def test_crypttab_captured_when_real_entries(tmp_path):
    (tmp_path / "etc").mkdir(parents=True)
    (tmp_path / "etc/crypttab").write_text(
        "# comment\nswap LABEL=cryptswap /dev/urandom swap,cipher=aes-xts-plain64\n")
    frag = _discover(tmp_path)
    ct = next((f for f in frag["files"] if f["path"] == "/etc/crypttab"), None)
    assert ct is not None
    assert "cryptswap" in ct["content"]


def test_crypttab_skipped_when_only_comments(tmp_path):
    (tmp_path / "etc").mkdir(parents=True)
    (tmp_path / "etc/crypttab").write_text("# only comments\n#\n\n")
    frag = _discover(tmp_path)
    assert all(f["path"] != "/etc/crypttab" for f in frag["files"])


def test_crypttab_absent_no_entry(tmp_path):
    (tmp_path / "etc").mkdir(parents=True)
    frag = _discover(tmp_path)
    assert all(f["path"] != "/etc/crypttab" for f in frag["files"])


def test_no_discovery_without_target():
    a = DropFilesAction({}, None)
    frag = a.import_state(managed=[])
    assert frag["modprobe_conf"] == [] and frag["files"] == []


def test_discovered_config_reapplies_as_noop(tmp_path):
    # feed the captured sections back -> plan writes nothing (files already match)
    frag = _discover(_seed_tree(tmp_path))
    cfg = {k: frag[k] for k in ("udev_rules", "modprobe_conf", "modules_load", "profile_d")}
    b = DropFilesAction(cfg, _ctx(str(tmp_path)))
    assert b.plan(managed=b.managed_keys()["files"]) == []
