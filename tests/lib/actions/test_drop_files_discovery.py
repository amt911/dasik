"""Live discovery of local /etc snippet files for `sync` from an empty seed:
modprobe.d, modules-load.d, udev/rules.d, profile.d (skip symlinks + pacman-owned)
plus /etc/crypttab when it has real entries."""
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


def test_discovers_sysctl_tmpfiles_sddm(tmp_path):
    (tmp_path / "etc/sysctl.d").mkdir(parents=True)
    (tmp_path / "etc/sysctl.d/99-swappiness.conf").write_text("vm.swappiness=10\n")
    (tmp_path / "etc/tmpfiles.d").mkdir(parents=True)
    (tmp_path / "etc/tmpfiles.d/mything.conf").write_text("d /run/x 0755 root root\n")
    (tmp_path / "etc/sddm.conf.d").mkdir(parents=True)
    (tmp_path / "etc/sddm.conf.d/rootless-x11.conf").write_text("[General]\n")
    frag = _discover(tmp_path)
    assert {e["name"] for e in frag["sysctl_d"]} == {"99-swappiness.conf"}
    assert {e["name"] for e in frag["tmpfiles_d"]} == {"mything.conf"}
    assert {e["name"] for e in frag["sddm_conf_d"]} == {"rootless-x11.conf"}


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


def test_discovers_wireguard_confs_into_files(tmp_path):
    (tmp_path / "etc/wireguard").mkdir(parents=True)
    (tmp_path / "etc/wireguard/wg0.conf").write_text(
        "[Interface]\nPrivateKey = SECRET=\nAddress = 10.0.0.2/32\n"
        "[Peer]\nPublicKey = PUB=\nEndpoint = vpn.example:51820\n")
    (tmp_path / "etc/wireguard/wg1.conf").write_text("[Interface]\nPrivateKey = K2=\n")
    (tmp_path / "etc/wireguard/README").write_text("not a conf")  # ignored (no .conf)
    frag = _discover(tmp_path)
    wg = {f["path"]: f["content"] for f in frag["files"] if "/etc/wireguard/" in f["path"]}
    assert set(wg) == {"/etc/wireguard/wg0.conf", "/etc/wireguard/wg1.conf"}
    assert "PrivateKey = SECRET=" in wg["/etc/wireguard/wg0.conf"]


def test_wireguard_absent_no_files(tmp_path):
    (tmp_path / "etc").mkdir(parents=True)
    frag = _discover(tmp_path)
    assert all("/etc/wireguard/" not in f["path"] for f in frag["files"])


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


# --- NetworkManager WireGuard + file mode --------------------------------- #

import os as _os
import stat as _stat


def test_discovers_nm_wireguard_connection(tmp_path):
    d = tmp_path / "etc/NetworkManager/system-connections"
    d.mkdir(parents=True)
    (d / "wg-home.nmconnection").write_text(
        "[connection]\nid=wg-home\ntype=wireguard\n\n"
        "[wireguard]\nprivate-key=SECRETKEY=\n")
    (d / "MyWifi.nmconnection").write_text(
        "[connection]\nid=MyWifi\ntype=wifi\n\n[wifi-security]\npsk=hunter2\n")
    frag = _discover(tmp_path)
    wg = {f["path"]: f for f in frag["files"] if "system-connections" in f["path"]}
    assert list(wg) == ["/etc/NetworkManager/system-connections/wg-home.nmconnection"]
    entry = wg["/etc/NetworkManager/system-connections/wg-home.nmconnection"]
    assert entry["mode"] == "0600"
    assert "private-key=SECRETKEY=" in entry["content"]      # secret captured verbatim


def test_nm_wireguard_absent_dir_no_crash(tmp_path):
    (tmp_path / "etc").mkdir(parents=True)
    frag = _discover(tmp_path)
    assert all("system-connections" not in f["path"] for f in frag["files"])


def test_wireguard_conf_gets_0600_mode(tmp_path):
    (tmp_path / "etc/wireguard").mkdir(parents=True)
    (tmp_path / "etc/wireguard/wg0.conf").write_text("[Interface]\nPrivateKey = K=\n")
    frag = _discover(tmp_path)
    wg = next(f for f in frag["files"] if f["path"] == "/etc/wireguard/wg0.conf")
    assert wg["mode"] == "0600"


def test_apply_chmods_file_with_mode(tmp_path):
    from dasik.lib.state.change import Change, Op
    cfg = {"files": [{"path": "/etc/wireguard/wg0.conf",
                      "content": "[Interface]\nPrivateKey = K=\n", "mode": "0600"}]}
    a = DropFilesAction(cfg, _ctx(str(tmp_path)))
    a.apply([Change("files", Op.CREATE, "/etc/wireguard/wg0.conf")])
    p = tmp_path / "etc/wireguard/wg0.conf"
    assert _stat.S_IMODE(_os.stat(p).st_mode) == 0o600


def test_apply_no_chmod_when_no_mode(tmp_path):
    from dasik.lib.state.change import Change, Op
    cfg = {"files": [{"path": "/etc/foo.conf", "content": "x\n"}]}
    a = DropFilesAction(cfg, _ctx(str(tmp_path)))
    a.apply([Change("files", Op.CREATE, "/etc/foo.conf")])
    # written with the default umask (no explicit chmod) — just assert it exists
    assert (tmp_path / "etc/foo.conf").exists()
