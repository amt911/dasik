"""`enable_trim` has to reach the SSD, not just schedule a timer.

expand_trim only added `fstrim.timer`. A LUKS mapping does not pass discards
down unless it is opened with `discard`, so on every encrypted config — which is
all of the interesting ones — the timer ran and trimmed nothing while the config
said TRIM was on.

Opting in is the user's call: `discard` on a LUKS volume reveals which blocks
are in use. That is why it follows `enable_trim` rather than being the default.
"""
from dasik.lib.actions.initramfs.dracut import DracutBackend
from dasik.lib.actions.kernel_cmdline_action import KernelCmdlineAction
from dasik.lib.actions.luks_uuid import luks_uuid
from dasik.lib.target.target import Target


ROOT_UUID = luks_uuid("cryptroot")


def _cfg(trim=False, encrypt=True, extra_opts=None):
    part = {"label": "root", "filesystem": "btrfs", "mountpoint": "/"}
    if encrypt:
        part.update({"encrypt": True, "luks_name": "cryptroot"})
    if extra_opts:
        part["luks_options"] = extra_opts
    cfg = {"disks": {"disks": [{"partitions": [part]}]}}
    if trim:
        cfg["enable_trim"] = True
    return cfg


def _tokens(cfg):
    return KernelCmdlineAction(cfg)._desired_tokens()


def test_trim_adds_discard_to_the_luks_options():
    assert f"rd.luks.options={ROOT_UUID}=discard" in _tokens(_cfg(trim=True))


def test_no_trim_no_discard():
    assert not [t for t in _tokens(_cfg(trim=False)) if "discard" in t]


def test_discard_joins_the_declared_luks_options():
    tokens = _tokens(_cfg(trim=True, extra_opts=["token-timeout=10s"]))
    opt = next(t for t in tokens if t.startswith("rd.luks.options="))
    assert "token-timeout=10s" in opt and "discard" in opt


def test_discard_is_not_repeated_when_already_declared():
    tokens = _tokens(_cfg(trim=True, extra_opts=["discard"]))
    opt = next(t for t in tokens if t.startswith("rd.luks.options="))
    assert opt.count("discard") == 1


def test_an_unencrypted_trim_config_adds_nothing():
    assert not [t for t in _tokens(_cfg(trim=True, encrypt=False))
                if "discard" in t]


# --- the same flag must reach /etc/crypttab (post-boot mounts) -------------- #

def test_crypttab_entry_carries_discard():
    ct = DracutBackend(_cfg(trim=True), Target(root="/")).crypttab()
    assert "discard" in ct


def test_crypttab_without_trim_has_no_discard():
    ct = DracutBackend(_cfg(trim=False), Target(root="/")).crypttab()
    assert "discard" not in ct
